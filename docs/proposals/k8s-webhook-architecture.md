# Proposal: Webhook-Driven, Kubernetes-Orchestrated CodeMate

**Status:** Draft / Request for Comments
**Author:** @veinkr-bot
**Date:** 2026-06-07
**Branch:** `proposal-k8s-design`

---

## 1. Motivation

Today CodeMate is a single-PR Docker workflow:

- `codemate --pr <n>` / `--branch <b>` launches **one container bound to one PR**, running
  Claude in a `tmux` session with `--dangerously-skip-permissions`.
- A cron job (`docker/setup/shell/monitor-pr.sh`) runs **every minute**, polling the GitHub
  API via `gh` to detect: new PR review comments, issue comments, ready-for-review, and CI
  failures. When it finds something *and* the session is idle, it injects a message into the
  TUI via `send_and_verify_command` (`tmux send-keys`).

This is efficient for a single PR, but has structural limits:

| # | Pain point | Root cause |
|---|------------|------------|
| 1 | **Doesn't scale past one PR** | Each PR = one manually-launched container on someone's host. No central scheduling, lifecycle, or resource isolation. |
| 2 | **Polling is wasteful & laggy** | Every container hits the GitHub API every 60s. N PRs = N pollers → rate-limit pressure and up to 60s latency per event. |
| 3 | **GitHub-watching logic is duplicated** | The same detection logic ships *inside every container* instead of living in one place. |
| 4 | **Fragile TUI injection** | Messages are delivered by typing into tmux (`send-keys` + the extended-keys `Enter` workaround + a status-file verify/retry loop). |
| 5 | **Host-bound, ephemeral state** | tmux session + `/tmp/pr-monitor-state` live on one host. Host dies → session lost. No way to list/manage all active sessions. |

## 2. Goals

- **One controller, many PRs.** A central middle layer manages many concurrent PR sessions.
- **Event-driven, not polled.** React to GitHub webhooks instead of a per-container `gh` loop.
- **Requirements live in GitHub.** PR description, PR review comments, and issue comments are
  the input channel — write the requirement in GitHub, the agent picks it up.
- **Keep the persistent tmux session; no human in the loop.** Each pod still runs a long-lived
  Claude `tmux` session, and webhook messages are injected into it via `tmux send-keys`
  (`send_and_verify_command`) — exactly as today. The difference is *nobody attaches to discuss*;
  the TUI is purely the runtime that receives injected webhook messages and reviews/acts on code.
  A persistent session is deliberate: it preserves **conversation context across events** that a
  one-shot `claude -p` / Agent SDK call would throw away.
- **Kubernetes-native lifecycle.** Provision, isolate, resource-limit, and reap PR sessions as
  first-class cluster objects.

### Non-goals

- Changing the Claude plugins/skills themselves (`/pr:*`, `/git:*` are reused as-is).
- Removing `--dangerously-skip-permissions` — it stays, now inside a per-PR k8s sandbox.

## 3. Architecture Overview

The middle layer is a **single Kubernetes operator** that both *receives the GitHub webhook* and
*controls the pods*. There is no separate gateway service: the operator runs an embedded HTTPS
webhook server in the same process as its reconcile manager (the same pattern operators already use
for admission webhooks).

```
                         GitHub
   (PR opened/closed · review comment · issue comment · review · check_suite)
                            │  webhook (HMAC-signed)            ▲
                            ▼                                   │ periodic resync
   ┌────────────────────────────────────────────────────────────────────────┐
   │                  CodeMate Operator  (single deployment)                  │
   │  ┌───────────────────────────┐      ┌────────────────────────────────┐  │
   │  │ embedded webhook server    │      │ PRSession reconciler           │  │
   │  │ - verify X-Hub-Signature   │─────►│ - ensure Pod == PRSession spec │  │
   │  │ - dedupe (delivery id)     │ CR   │ - create on open / delete on   │  │
   │  │ - lifecycle → create/del CR│ ops  │   close · update .status       │  │
   │  │ - content   → enqueue msg  │      │ - periodic GitHub resync       │  │
   │  └─────────────┬─────────────┘      └────────────────┬───────────────┘  │
   └────────────────┼───────────────────────────────────-┼──────────────────┘
                    │ enqueue message (key=repo#pr)        │ create / delete Pod
                    ▼                                      ▼
          ┌───────────────────┐               ┌──────────────────────────────┐
          │ Queue (per-PR      │               │ Pod: one per open PR          │
          │ ordering, durable) │               │ ┌────────┐   ┌─────────────┐  │
          └─────────┬─────────┘               │ │sidecar │──►│ tmux/Claude  │  │
                    │ consume → wait idle      │ │ (loop) │   │ (persistent) │  │
                    │ → send_and_verify_command│ └────────┘   └─────────────┘  │
                    └─────────────────────────►│ git checkout of PR branch     │
                                               └──────────────────────────────┘
```

### 3.1 Components

**A. CodeMate Operator (the middle layer)** — one deployment, two cooperating parts:

*Embedded webhook server* (served by all replicas, stateless):
- HTTPS endpoint exposed publicly via an ingress *or* an outbound tunnel (Cloudflare Tunnel / frpc
  — see §8); verifies `X-Hub-Signature-256` HMAC; dedupes on the GitHub `X-GitHub-Delivery` id.
- **Lifecycle events** (`opened`/`reopened`/`closed`/merged) → create or delete the `PRSession`
  custom resource. It does *not* touch pods directly — it only edits desired state.
- **Content events** (comments, reviews, CI results) → enqueue a message keyed by `repo#pr`.

*Reconciler* (runs under leader election):
- Watches `PRSession` CRs and makes the cluster match them: one pod per open PR, with resource
  limits and network policy; deletes the pod when the CR is removed. Updates `.status`.
- **Periodic GitHub resync:** every N minutes it lists open PRs via the API and reconciles,
  self-healing any webhook that was missed during an outage — a level-triggered backstop the
  cron design never had.

> Why one operator instead of a gateway + controller: the operator already runs a manager loop, so
> adding an HTTP listener to it is cheap and removes a moving part. Splitting *lifecycle → CR* from
> *content → queue* keeps the reconcile model clean — CRs carry **desired state** ("this PR should
> have a pod"), while the queue carries **ordered event data** ("deliver this exact comment"), which
> a CR models poorly.

**B. Message Queue / Inbox** — durable buffer (Redis Streams or NATS JetStream):
- Per-PR ordering and at-least-once delivery; survives pod restarts (fixes pain #5).
- If a pod crashes and is rescheduled, unacked messages are redelivered — no events are lost
  across the restart.

**C. Per-PR Agent Pod** — the existing CodeMate image, **minus cron but keeping tmux**:
- **Logical lifetime == PR lifetime** (the `PRSession` exists from PR open to close/merge), but the
  pod itself can **scale to zero between events**: the Claude session is bound to the PR and
  persisted to a shared PVC, so a reaped pod **resumes** that session (`claude --resume`) on the
  next event with full context — cheap to run, fast to wake (see §6.3).
- One-PR-per-pod is kept on purpose. The pod runs the same Claude `tmux` session as today (`run.sh`),
  resumed from disk rather than started cold.
- A lightweight **delivery sidecar** consumes the PR's queue and, per message, waits for the
  session to be idle, then injects the message into the TUI via `tmux send-keys` using the
  existing `send_and_verify_command`.
- Messages are **serialized per PR** (one at a time, in order) so git operations never race.
- Reuses existing skills end-to-end: `/pr:fix-comments`, `/pr:update`, `/git:commit`, `/pr:ack-comments`.

## 4. Message delivery into the persistent TUI session

We **keep** the persistent tmux Claude session and the proven keystroke-injection path. The only
things that change are the *source* and the *trigger* of messages — not how they reach Claude.

| Concern | Today | Proposed |
|---------|-------|----------|
| **Source of events** | In-container `gh` poll (`monitor-pr.sh`) | Webhook gateway → per-PR queue |
| **Trigger** | cron, every 60s | webhook arrival (push, near-instant) |
| **Idle gating** | `/tmp/.session_status` ends in `Stop` | unchanged — sidecar waits for `Stop` |
| **Delivery into Claude** | `send_and_verify_command` (`tmux send-keys` + `Enter`) | unchanged — same function |
| **Commit nudge** | `check_git_changes` Stop hook | unchanged |
| **Dedupe / ordering state** | `/tmp/pr-monitor-state` (host-bound) | queue / Redis / `PRSession.status` (durable) |

Why keep the TUI session rather than going headless (`claude -p` / Agent SDK):

- **Persistent context.** A single Claude session accumulates the conversation across many
  webhook events (description → review comment → CI failure → follow-up). One-shot headless runs
  would lose that context every event and have to re-establish it. Because the session is persisted
  to a PVC and bound to the PR (§6.3), this context even **survives the pod being scaled to zero**.
- **Zero rewrite of the proven path.** `send_and_verify_command`, the `/tmp/.session_status` idle
  signal, and the `check_git_changes` Stop hook all carry over unchanged — we are only swapping the
  cron poller for a webhook-fed queue in front of them.

So the sidecar's loop is essentially today's `monitor-pr.sh` "act only when Stopped, send one
message, verify submission" logic — but woken by a queue message instead of a 60s cron tick.

## 5. Event → action mapping

The operator's webhook handler replaces every check currently in `monitor-pr.sh`:

| GitHub webhook | Action delivered to the PR pod |
|----------------|--------------------------------|
| `pull_request.opened` / `reopened` | Provision pod; seed initial run with the **PR description** as the requirement |
| `issue_comment.created` (on a PR) | Run with the comment text as prompt → address it, then `/pr:ack-comments` (👀) |
| `pull_request_review_comment.created` | Run `/pr:fix-comments` against the inline comment |
| `pull_request_review.submitted` | Run with the review summary as context |
| `pull_request.ready_for_review` | Run `/pr:update` to refresh title/description |
| `check_suite.completed` / `check_run.completed` (failure) | Fetch failing logs, run a fix, `/git:commit` |
| `pull_request.closed` / merged | Operator deletes the `PRSession` → reconciler reaps the pod |
| `issues.labeled` / `issue_comment` (trigger) | **Bootstrap**: create branch + empty commit + draft PR, then start the session — see §5.1 |

### 5.1 Entry point: issue-driven bootstrap (creating the "empty" PR)

In practice a user starts from an **issue**, not a PR. GitHub's web UI lets anyone file an issue,
but you **cannot open an empty PR** there — a PR needs a head branch with at least one commit ahead
of base (the API rejects an identical head/base with *"nothing to compare"*). So when an issue
signals it wants an agent, the operator bootstraps the PR on the user's behalf.

**Trigger (configurable — pick one):**
- a **label** on the issue (e.g. `codemate` / `agent`), or
- a **slash command** in the issue body or a comment (e.g. `/codemate start`), or
- a dedicated **issue template** for agent tasks.

**On trigger, the operator (using a GitHub App token) does:**
1. Create a branch off the default branch: `codemate/issue-<n>-<slug>`.
2. Push an **empty commit** so the branch is ahead of base — this is the minimal diff that makes a
   PR openable:
   ```bash
   git commit --allow-empty -m "chore: start CodeMate session for #<n>"
   ```
3. Open a **draft PR** (`head=codemate/issue-<n>-<slug>`, `base=<default>`), body seeded from the
   issue: `Closes #<n>` plus the requirements/**rules** the user wrote.
4. The resulting `pull_request.opened` event then drives the normal flow (§5/§6): the operator
   creates the `PRSession`, the pod boots, and the **issue body + rules become the seed message**
   to Claude. The agent's first real work replaces the empty commit.

```
user files Issue (+ label/command, writes rules)
        │  issues.labeled / issue_comment webhook
        ▼
operator: create branch → empty commit → open draft PR (Closes #n, body = issue rules)
        │  pull_request.opened webhook (now a normal PR exists)
        ▼
operator: create PRSession → pod boots tmux → seed = issue requirements
        │
        ▼
normal PR loop (review comments, CI, issue/PR comments → the live session)
```

Optionally the bootstrap reuses the existing **issue plugin** — `/issue:read-issue` to pull full
context (including comments) and `/issue:refine-issue` / `/issue:triage-issue` to clean up the
request before seeding, so Claude starts from a well-formed spec.

**Issue ↔ PR relationship.** A consequence of this entry point: every *agent-bootstrapped* PR is
tied **1:1 to an issue** via `Closes #<n>`. This is a feature — every change traces back to a
tracked requirement, the issue is the durable discussion thread, and merging the PR auto-closes the
issue. The mapping is one issue → one branch → one PR → one pod.

This does **not** force *all* PRs to originate from an issue, though:

| How the PR is born | Issue required? | Operator behavior |
|--------------------|-----------------|-------------------|
| Web UI (the common case) | **Yes** — file an issue; operator bootstraps the empty PR | issue → empty commit → draft PR → session |
| CLI / IDE / `git push` + open PR | No — a human already has a branch with commits | operator just attaches a session on `pull_request.opened` |

So: the **issue is the mandatory entry point only for the web UI** (because empty PRs can't be
created there). Directly-opened PRs are still picked up. If you *want* to enforce "every PR has an
issue" as a policy, that becomes a simple operator rule (reject/relabel PRs with no linked issue) —
called out as open question #7.

## 6. Lifecycle, state & isolation

**The `PRSession` is bound 1:1 to its PR's lifecycle; the pod underneath can scale to zero:**

```
pull_request.opened ─────► operator creates PRSession ─────► Pod (resume/boot tmux)
                                                                   │ handle event
   review / issue / CI events ──► wake pod (if scaled to 0) → resume session → inject
                                                                   │ idle N min
                                                          Pod scaled to zero (session
                                                          persisted on PVC, PRSession kept)
pull_request.closed / merged ─────► operator deletes PRSession → Pod + session reaped
```

- The `PRSession` exists for the whole time the PR is open. The **pod** runs when there's work and
  may **scale to zero when idle**; the next event wakes it and it **resumes** the PR's Claude
  session from the PVC (§6.3), so conversation context is preserved across the gap.
- When the PR is **closed or merged**, the operator deletes the `PRSession`; the pod and the
  PR-bound session are reaped. A reopen recreates the `PRSession` (and resumes if the session blob
  still exists).
- **Mapping:** `PRSession` CRD keyed by `repo + pr`; pods labeled `codemate.io/repo`,
  `codemate.io/pr`. The operator finds a session by label, never by host.
- **Workspace:** ephemeral — git is the source of truth, so a woken/restarted pod just
  re-checks-out the PR branch. Only the Claude *session* state is persisted (§6.3), not the checkout.
- **State that used to live in `/tmp/pr-monitor-state`** (last-checked time, dedupe, CI-notified
  flags) moves to the queue/Redis or `PRSession.status` — durable and centrally visible.
- **One PR per pod** is retained on purpose: isolated git checkout, independent crash blast radius,
  per-PR CPU/mem limits, trivial routing. Scaling = more pods, scheduled by k8s.

### 6.1 End-to-end sequence

A single event — a reviewer leaving an inline comment — flows like this:

```
GitHub        Operator (webhook + reconciler)   Queue        Sidecar        tmux/Claude
  │              │                                 │            │                 │
  │ pull_request_review_comment                    │            │                 │
  ├─────────────►│ verify HMAC                     │            │                 │
  │              │ dedupe(delivery_id)             │            │                 │
  │              │ (content event → enqueue)       │            │                 │
  │              ├─ enqueue ──────────────────────►│(key=repo#pr)                 │
  │              │ reconciler: PRSession exists,    │            │                 │
  │              │ pod already up (PR is open) — noop                             │
  │              │                                 │ consume(repo#pr)             │
  │              │                                 │◄──────────┤ (sidecar pulls) │
  │              │                                 │            │ wait for idle   │
  │              │                                 │            │ (.session_status│
  │              │                                 │            │   ends in Stop) │
  │              │                                 │            │ send_and_verify ├────►│
  │              │                                 │            │                 │ /pr:fix-comments
  │              │                                 │            │                 │ edit→/git:commit→push
  │              │                                 │            │ ack (eyes 👀)   │◄────┤
  │              │                                 │◄ ack ──────┤ (on Stop)       │
```

For `pull_request.opened` the only difference is a first step: the webhook handler **creates** the
`PRSession` CR, the reconciler boots the pod + tmux session (`run.sh`), then the description is
delivered as the seed message. For `pull_request.closed`/merged the handler **deletes** the
`PRSession` CR and the reconciler garbage-collects the pod. If a webhook is ever missed, the
operator's periodic GitHub resync converges the same end state.

### 6.2 `PRSession` custom resource

The operator reconciles one `PRSession` per open PR. It is the single source of truth for
"which PRs have a live agent" — replacing the scattered, host-bound tmux sessions of today.

```yaml
apiVersion: codemate.io/v1alpha1
kind: PRSession
metadata:
  name: boringhappy-codemate-pr-241        # <owner>-<repo>-pr-<number>, DNS-safe
  namespace: codemate
  labels:
    codemate.io/repo: BoringHappy/CodeMate
    codemate.io/pr: "241"
spec:
  repo: https://github.com/BoringHappy/CodeMate.git
  prNumber: 241
  branch: proposal-k8s-design
  # Reuses the same knobs codemate/.env already passes to the container today.
  image: ghcr.io/boringhappy/codemate:latest
  systemPromptRef: standard                 # standard | opensource
  claudeAuth:                                # shared, pre-authenticated Claude creds (§6.3)
    pvcName: codemate-claude-auth            # RWX PVC holding ~/.claude + ~/.claude.json
    mountReadOnly: true
  resources:
    requests: { cpu: "500m", memory: 1Gi }
    limits:   { cpu: "2",    memory: 4Gi }
  idleTimeoutSeconds: 600                    # scale pod to zero after this idle (0 = never)
  env:                                       # non-secret; secrets come from secretRefs
    - name: CODEMATE_ALLOW_COUNTRY
      value: "US,CA"
  secretRefs:                                # projected into the pod, not stored in the CR
    - githubAppInstallationToken             # short-lived, minted by the gateway
    - slackWebhook
  # Lifecycle is driven by GitHub state, surfaced here for observability/manual override.
  lifecycle:
    bindToPR: true                           # delete pod when PR closes/merges (default)
    deleteOnMerge: true
status:
  phase: Running                             # Pending | Running | Closing | Terminated
  podName: codemate-pr-241-7c9f8
  prState: open                              # mirror of GitHub PR state
  lastEventDeliveryId: "a1b2c3d4-..."        # dedupe cursor (replaces /tmp/pr-monitor-state)
  lastEventAt: "2026-06-07T10:32:00Z"
  sessionStatus: Stop                        # mirror of /tmp/.session_status (idle/busy)
  claudeSessionId: "9f3c...e21"              # PR-bound Claude session, resumed on wake (§6.3)
  scaledToZero: false                        # true while the pod is reaped between events
  queue:
    pending: 0
    inFlight: 0
  conditions:
    - type: PodReady
      status: "True"
    - type: SessionIdle
      status: "True"
```

Notes:
- **Creation/deletion** is driven by GitHub: the gateway (or controller watching the events)
  creates the CR on `pull_request.opened` and deletes it on `closed`/merged. `kubectl get prsessions`
  then lists every active agent across all repos — the management surface that's missing today.
- **`status` absorbs the old `/tmp/pr-monitor-state`**: `lastEventDeliveryId` is the dedupe cursor,
  `sessionStatus` mirrors the idle/busy hook signal, and `queue.pending` shows backpressure.
- **Secrets stay out of the CR**; only references are stored, resolved to projected pod secrets.

### 6.3 Claude auth & session persistence

Two problems are solved by a **single shared PVC** holding Claude's home state
(`~/.claude/` and `~/.claude.json`):

**(a) Authentication — log in once, reuse everywhere.**
Instead of every pod authenticating Claude independently, a one-time **auth init pod** does the
login interactively and writes the credentials to the shared PVC:

```bash
# one-time, by an operator/admin:
kubectl apply -f codemate-auth-init.yaml          # pod that mounts the codemate-claude-auth PVC
kubectl exec -it codemate-auth-init -- claude      # complete `claude login` (OAuth/API key)
# credentials now live on the PVC at ~/.claude.json + ~/.claude/
kubectl delete pod codemate-auth-init             # init pod no longer needed
```

Every PR pod then mounts the same PVC (default **read-only**) at the Claude home path and reuses
those credentials — no per-pod login. Re-running the init pod refreshes/rotates the login.

> **Access mode & write races.** The PVC must be **RWX** (e.g. NFS / CephFS / cloud filestore) so
> many pods mount it at once. Credentials are mounted read-only by agent pods to avoid concurrent
> writes to `~/.claude.json` (token refresh is done only by the init pod). Per-PR *session* blobs
> (below) are written, so they live in a per-PR subdirectory to avoid cross-pod contention.

**(b) Session persistence — bind a Claude session to the PR for fast scale-to-zero.**
Claude Code persists each conversation as a resumable **session** under `~/.claude/`. We store the
PR's session on the PVC (keyed by `repo#pr`, recorded in `status.claudeSessionId`) so:

- when a pod boots/wakes, `run.sh` starts Claude with `--resume <claudeSessionId>` instead of cold —
  the full PR conversation context is restored from disk;
- therefore the pod can be **scaled to zero after `idleTimeoutSeconds`** of no events and recreated
  on the next webhook, **without losing context** — turning the cost/context tradeoff of §3.1 into
  "cheap when idle, warm when needed";
- on PR close/merge the operator deletes the PR's session blob alongside the pod.

This is what makes the lifecycle in §6 affordable: a PR can stay open for days while its pod
consumes resources only during the seconds it is actually working.

## 7. Security

- **Webhook auth:** HMAC `X-Hub-Signature-256` verified by the operator's webhook server; reject otherwise.
- **GitHub App over PAT:** the operator mints short-lived, per-repo installation tokens and
  projects them into pods as mounted secrets — instead of baking one long-lived token into every
  container. Carries over the existing region check (`check-region.sh`) at the operator tier.
- **Network policy:** agent pods may egress only to GitHub + the queue. The operator's webhook
  Service is *not* exposed directly; it is reached only via the tunnel/ingress chosen in §8
  (with `cloudflared`/`frpc`, exposure is an **outbound** connection from the tunnel pod — no
  inbound hole in the cluster firewall at all).
- **Blast radius:** `--dangerously-skip-permissions` stays, but now each PR runs in its own pod
  with k8s resource limits and network isolation — a strictly tighter sandbox than a shared host.
- **Shared Claude credentials (§6.3):** the auth PVC is mounted **read-only** by agent pods and
  reachable only by the CodeMate namespace (RBAC + the PVC's storage class). All PR pods share one
  Claude login, so it is a shared-fate credential — acceptable while the trust model below is
  deferred, but worth revisiting if untrusted repos are ever co-tenanted.
- **Deferred — prompt-injection / author trust model.** Because the agent acts on issue/PR text and
  runs with skip-permissions, untrusted authors could attempt to steer it (e.g. exfiltrate the
  token). Author-association gating, command allowlists, and secret-from-context hardening are
  **explicitly out of scope for this proposal** and tracked as future work (open question #8). The
  egress NetworkPolicy above is the only mitigation in place for now.

## 8. Exposing the webhook endpoint (tunnels & ingress)

GitHub webhooks are **outbound POSTs from GitHub to a public URL** — so the operator's webhook
server must be reachable from the internet. Many target clusters (home labs, private/on-prem,
behind NAT) have no public IP or ingress controller. Rather than require one, the chart supports
**outbound reverse tunnels**: the tunnel client runs inside the cluster, dials *out* to a public
edge, and gets back a stable public hostname that forwards to the operator Service. No inbound
firewall hole, no public LoadBalancer needed.

Two tunnel backends are supported, selectable (either, or both) via Helm values:

| Backend | What it is | Public edge | Best when |
|---------|------------|-------------|-----------|
| **Cloudflare Tunnel** (`cloudflared`) | Cloudflare's tunnel daemon (set up via Cloudflare Zero Trust; commonly grouped with "WARP"). Note: WARP-the-client connects you *to* Cloudflare; the **Tunnel** is what *publishes* your Service. | Cloudflare (free named tunnel) | You use Cloudflare DNS / want managed TLS + a free public hostname |
| **frpc** | `frp` reverse-proxy client → your own public `frps` server | Your own VPS running `frps` | You already run a public host and want full control |

Both are just **Deployments the chart renders** (2 replicas for HA) that point at the operator's
webhook Service; the operator itself is unchanged. If you *do* have a normal ingress/LoadBalancer,
set `webhook.expose.mode: ingress` and skip both tunnels.

### 8.1 Helm values

```yaml
webhook:
  service:
    port: 8080                       # operator webhook server port (tunnel target)
  expose:
    # ingress | cloudflare | frpc | both   (both = two public URLs for redundancy)
    mode: cloudflare

# --- Cloudflare Tunnel (cloudflared) -------------------------------------
cloudflare:
  enabled: true                      # auto-true when expose.mode is cloudflare/both
  replicas: 2
  # Recommended: token-based named tunnel created in the Zero Trust dashboard.
  tunnelTokenSecret:
    name: cloudflared-token          # kubectl create secret generic cloudflared-token --from-literal=token=...
    key: token
  ingress:                           # cloudflared route → operator Service
    hostname: codemate-webhook.example.com
    service: http://codemate-operator.codemate.svc.cluster.local:8080
  # image: cloudflare/cloudflared:latest

# --- frpc -----------------------------------------------------------------
frpc:
  enabled: false                     # auto-true when expose.mode is frpc/both
  replicas: 2
  server:
    addr: frps.example.com           # your public frps host
    port: 7000
  authTokenSecret:                   # must match frps token
    name: frpc-token
    key: token
  proxy:
    name: codemate-webhook
    type: https                      # https | http | tcp
    customDomains: ["codemate-webhook.example.com"]
    localSvc: codemate-operator.codemate.svc.cluster.local
    localPort: 8080
  # image: snowdreamtech/frpc:latest
```

### 8.2 How the value drives rendering

- `expose.mode` is the single switch. `cloudflare`/`frpc` render exactly one tunnel Deployment +
  its config (a `ConfigMap` for `cloudflared` `config.yaml` / `frpc.toml`, plus the token `Secret`
  reference). `both` renders both; `ingress` renders a normal `Ingress` and no tunnel.
- Tokens are **never** put in values — they're referenced from pre-created `Secret`s
  (`tunnelTokenSecret` / `authTokenSecret`), consistent with §7.
- **Both enabled** gives two public hostnames to the same operator Service. GitHub allows multiple
  webhooks per repo, so you can register both URLs for failover, or just point GitHub at one and
  keep the other warm. The HMAC secret (§7) is identical on both paths, so either is safe.
- The webhook public URL (whichever backend) is what you paste into the GitHub App / repo webhook
  config; the operator validates `X-Hub-Signature-256` regardless of how the request arrived.

## 9. Phased rollout

| Phase | Deliverable | Risk |
|-------|-------------|------|
| **0** | *(today)* cron polling + tmux in a single container | — |
| **1** | Stand up the operator with just its embedded webhook server; replace the in-container cron poll with a webhook-fed inbox sidecar that injects into the **existing tmux session** via `send_and_verify_command`. Prove event parity with `monitor-pr.sh`. | Low — reuses image, tmux path & skills |
| **2** | Add the operator's reconciler + `PRSession` CRD; **shared Claude-auth PVC + one-time auth init pod** (§6.3); auto-provision a pod on `pull_request.opened` and **tear it down on `pull_request.closed`/merged** (no more manual `codemate --pr`). | Medium |
| **3** | Durable queue (Redis Streams/NATS) + periodic GitHub resync backstop + **PR-bound session resume → idle scale-to-zero** (§6.3) + GitHub App auth + per-repo session quotas. | Medium |
| **4** | Multi-repo, web dashboard of active sessions, requirement-issue → auto-spawn. | — |

The existing `dev:run-image` / `dev:manage-k8s` plugins are natural building blocks for Phase 2's
pod provisioning and operational tooling.

## 10. Open questions

1. **Delivery transport:** direct HTTP to a per-pod sidecar vs. a shared durable queue.
   *Recommendation: queue* — durability and crash-redelivery outweigh the added component.
2. **Workspace persistence:** ephemeral re-checkout vs. PVC-per-PR. *Recommendation: ephemeral.*
3. **Operator implementation — DECIDED: Python + kopf** (CRD watch/reconcile) with an embedded
   aiohttp/FastAPI webhook server, developed in this repo (monorepo). Start simple (Phase 1/2),
   formalize the CRD in Phase 3. See the implementation plan's *Repository strategy*.
4. **Concurrency within a PR:** strictly serial runs (proposed) vs. allowing a quick read-only run
   to interleave. Serial is safest for git; revisit if latency hurts.
5. **Cost controls:** per-repo pod quotas / max concurrent sessions to bound spend.
6. **Tunnel default:** ship `expose.mode` defaulting to `cloudflare` (lowest setup for most users)
   vs. `ingress` (assume a real cluster). *Leaning `cloudflare`* given the home-lab target.
7. **Mandatory issue linkage:** enforce "every PR must close an issue" (clean traceability, single
   model) vs. allow issue-less direct PRs. *Leaning: bootstrap-from-issue is the default/recommended
   path, but don't hard-reject direct PRs unless a repo opts in.*
8. **Trust / prompt-injection model (deferred):** the agent acts on untrusted issue/PR text under
   skip-permissions. Author-association gating, command allowlists, and keeping secrets out of model
   context are **out of scope for now**; egress NetworkPolicy is the only current mitigation. Must be
   designed before exposing this to repos with untrusted external contributors.
9. **Auth PVC storage:** requires an **RWX** storage class (NFS/CephFS/cloud filestore) for many
   pods to share `~/.claude`. Credentials mounted read-only; per-PR session blobs in per-PR subdirs
   to avoid write contention. Confirm the target cluster has an RWX provisioner.

## 11. Summary

Move the GitHub-watching logic *out* of every container and into a single **Kubernetes operator**
(the middle layer) that both **receives the GitHub webhook** (embedded HTTPS server) and **controls
the pods** (reconciles `PRSession` CRs). Lifecycle events create/delete the CR; content events are
routed through a **durable per-PR queue**. The operator runs **one tmux Claude pod per PR**, bound
to the PR's lifecycle; pods **scale to zero when idle** and **resume the PR's Claude session** from
a shared PVC on the next event, so a long-open PR costs resources only while actually working. That
same PVC holds Claude credentials authenticated **once** by an auth init pod and reused by every
pod. A **periodic GitHub resync** self-heals any missed webhook. Webhook messages are injected into
the live TUI via the existing `send_and_verify_command`, preserving conversation context across
events. The webhook endpoint is exposed to GitHub via a Helm-selectable backend — **Cloudflare
Tunnel (`cloudflared`)**, **frpc**, both, or a plain ingress — so even a private/home cluster with
no public IP works out of the box. This removes the per-container 60s polling loop, scales to many
concurrent PRs, and lets requirements be expressed naturally in PR/issue descriptions and comments.
