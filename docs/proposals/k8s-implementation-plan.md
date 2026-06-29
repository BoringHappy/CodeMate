# Implementation Plan: Webhook-Driven, Kubernetes-Orchestrated CodeMate

**Companion to:** [`k8s-webhook-architecture.md`](./k8s-webhook-architecture.md)
**Status:** Draft
**Branch:** `proposal-k8s-design`

This plan turns the architecture proposal into concrete, sequenced work. It maps each phase from
§9 of the proposal to workstreams with deliverables, the existing files they reuse/replace, and
acceptance criteria. Each phase is independently shippable and leaves the system working.

Contents: component inventory → repository strategy → **interfaces & data contracts** (message
schema, event mapping, CRD schema, queue keys, sidecar API) → the four phases (each with reference
pseudocode) → **reliability & edge cases** → cross-cutting (testing/observability) → milestones &
effort → decisions.

---

## 0. Guiding principles

- **Reuse the proven path.** The tmux session, `send_and_verify_command`, the `/tmp/.session_status`
  idle signal, the `check_git_changes` Stop hook, and all `/pr:*` `/git:*` `/issue:*` skills are
  kept as-is. We change *what wakes the agent*, not *how it works*.
- **Strangle, don't rewrite.** Stand the new path up beside the cron path and cut over per-repo.
- **Each phase is releasable.** No big-bang migration.

## 1. Component inventory (new vs. reused)

| Component | New / Reuse | Source today |
|-----------|-------------|--------------|
| Operator (webhook server + reconciler) | **New** (Python + [kopf](https://kopf.readthedocs.io/)) | — |
| `PRSession` CRD | **New** | — |
| Delivery sidecar (queue consumer → tmux) | **New** (thin) | logic adapted from `docker/setup/shell/monitor-pr.sh` |
| Message queue | **New** (Redis Streams / NATS) | replaces `/tmp/pr-monitor-state` |
| Shared Claude-auth PVC (`~/.claude`, `~/.claude.json`) | **New** (RWX) | replaces per-container Claude login |
| Claude auth init pod | **New** (one-time `claude login` via `kubectl exec`) | — |
| PR-bound Claude session store + resume | **New** | `run-claude.sh` gains `claude --resume` |
| Agent pod image | **Modify** | `docker/Dockerfile.claude`, `docker/setup/*` (drop cron) |
| Tmux launch + injection | **Reuse** | `docker/setup/run-claude.sh`, `common.sh::send_and_verify_command` |
| Idle/commit hooks | **Reuse** | `plugins/workspace/hooks/*` |
| Repo/branch/PR bootstrap | **Reuse/extend** | `docker/setup/python/setup-repo.py` |
| `codemate` launcher (cluster mode → create `PRSession`) | **Modify** | `codemate` (keeps local `docker run` as back-compat) |
| Helm chart | **New** | — |
| Tunnels (cloudflared / frpc) | **New** (chart templates) | — |

---

## Repository strategy

**Decision: develop in this repo (monorepo), in new top-level directories — do not split out a
separate repo yet.**

The operator is tightly coupled to what already lives here: it drives the existing image
(`docker/Dockerfile.claude`), the sidecar ports logic from `monitor-pr.sh` / `common.sh`, and it depends on
the existing hooks and `/pr:* /git:* /issue:*` plugins. The strangler migration (Phase 1 edits
`docker/setup/*` *and* adds operator code at once) is far simpler as atomic PRs in one repo than as a
coordinated cross-repo dance. One team, early stage, fast iteration, and reuse of existing CI / issue
templates all favor a monorepo.

The decision is also **low-risk because it's reversible the easy way**: extracting the operator into
its own repo later (with history, via `git filter-repo`) is cheap; merging two repos you wish were
one is painful. Start together; split only if the operator gains an independent audience/release
rhythm or the repo grows unwieldy.

### Target layout (additions, alongside existing dirs)

```
CodeMate/
  operator/                 # NEW — Python + kopf operator
    pyproject.toml          #   isolated deps; its own CI job
    codemate_operator/
      webhook.py            #   aiohttp/FastAPI: verify HMAC, map events
      reconcile.py          #   kopf handlers for PRSession
      events.py             #   event→message mapping (ported from monitor-pr.sh)
    sidecar/                #   delivery sidecar (queue consumer → send_and_verify_command)
    tests/
  charts/codemate/          # NEW — Helm chart (operator, queue, tunnels, claude-auth PVC)
  docker/                   # existing — image + setup scripts (sidecar wired in, cron removed)
  plugins/                  # existing — reused as-is
```

### Conventions for the monorepo
- **Separate CI:** add a workflow that runs the operator's Python tests/lint only on `operator/`
  changes; the existing `docker-build-claude.yml` keeps owning the image.
- **Independent versioning:** tag the operator/chart with a prefix (e.g. `operator/v0.1.0`,
  `chart/v0.1.0`) so release cadence is decoupled from the image without separate repos.
- **Plugin version-bump rule** (`.claude/rules/plugin-version-bump.md`) still applies to any
  `plugins/**` change; operator/chart dirs are outside its scope.

---

## Interfaces & data contracts

These are the contracts the components agree on. Lock them early — every phase depends on them.

### A. Internal message (`PRTask`)

The single normalized envelope the operator produces and the sidecar consumes. The `deliveryId`
(GitHub's `X-GitHub-Delivery`) is the **idempotency key** end-to-end.

```jsonc
{
  "deliveryId": "a1b2c3d4-5e6f-...",        // GitHub X-GitHub-Delivery; dedupe + ack key
  "repo": "BoringHappy/CodeMate",
  "pr": 241,
  "kind": "review_comment",                  // enum, see table B
  "actor": "alice",                          // event sender login
  "createdAt": "2026-06-07T10:32:00Z",
  "prompt": "PR review comment from @alice on docker/setup/run-claude.sh:42:\n\"...\"",
  "followup": "/pr:ack-comments",            // optional skill to run after prompt settles
  "ref": { "commentId": 12345, "path": "docker/setup/run-claude.sh", "line": 42 }
}
```

### B. Webhook → `PRTask` mapping (authoritative; ports `monitor-pr.sh`)

| GitHub event (`X-GitHub-Event` / action) | Filter (skip if…) | `kind` | Operator action | `prompt` / `followup` |
|---|---|---|---|---|
| `pull_request` / `opened`,`reopened` | — | `pr_opened` | **create `PRSession`** | seed = PR body |
| `pull_request` / `closed` | — | `pr_closed` | **delete `PRSession`** | — |
| `pull_request` / `ready_for_review` | has `pr-updated` label | `ready` | enqueue | run `/pr:update` |
| `issue_comment` / `created` (on a PR) | author is `[bot]`; body starts `Claude Replied:`; has 👀 | `issue_comment` | enqueue | text → reply; `followup=/pr:ack-comments` |
| `pull_request_review_comment` / `created` | author `[bot]`; last in thread starts `Claude Replied:` | `review_comment` | enqueue | run `/pr:fix-comments` on the thread |
| `pull_request_review` / `submitted` | author `[bot]`; empty body+no comments | `review` | enqueue | review summary as context |
| `check_suite`/`check_run` / `completed` | `conclusion != failure`; same commit already handled | `ci_failure` | enqueue | failing logs → fix; `followup=/git:commit` |
| `issues` / `labeled`,`issue_comment` (trigger) | label≠configured trigger | `bootstrap` | bootstrap branch+PR (Phase 4) | seed = issue body |
| `ping` | — | — | 200 OK, no-op | — |

The three skip-filters (`[bot]` author, `Claude Replied:` prefix, 👀 reaction) are lifted verbatim
from `monitor-pr.sh`'s jq filters so behavior matches today exactly.

### C. `PRSession` CRD (`openAPIV3Schema`, abbreviated)

```yaml
spec:
  repo:            {type: string}            # https URL
  prNumber:        {type: integer}
  branch:          {type: string}
  image:           {type: string}
  systemPromptRef: {type: string, enum: [standard, opensource]}
  query:           {type: string}            # optional seed (CLI mode)
  resources:       {type: object}            # k8s ResourceRequirements
  env:             {type: array}             # [{name,value}]
  secretRefs:      {type: array, items: {type: string}}
  claudeAuth:      {pvcName: string, mountReadOnly: boolean}
  idleTimeoutSeconds: {type: integer, default: 600}   # 0 = never scale to zero
status:
  phase:           {enum: [Pending, Running, Idle, ScaledToZero, Closing, Terminated]}
  podName:         {type: string}
  prState:         {type: string}            # mirror of GitHub
  claudeSessionId: {type: string}
  scaledToZero:    {type: boolean}
  lastEventDeliveryId: {type: string}        # dedupe cursor
  lastEventAt:     {type: string, format: date-time}
  sessionStatus:   {type: string}            # mirror of /tmp/.session_status
  queue:           {pending: integer, inFlight: integer}
  conditions:      {type: array}
# printerColumns: PR, PHASE, POD, IDLE, AGE  → nice `kubectl get prsessions`
```

### D. Queue keys (Phase 3, Redis Streams)

- Stream per PR: `codemate:stream:{owner}/{repo}#{pr}` · consumer group `agent`.
- Dedupe: `SET codemate:seen:{deliveryId} 1 EX 604800 NX` (1-week TTL) — returns false if duplicate.
- Dead-letter: `codemate:dlq:{owner}/{repo}#{pr}` after `maxDeliveries` (see Reliability).

### E. Sidecar HTTP API (Phase 1 transport)

- `POST /inbox` ← operator; body = `PRTask`; `202` once locally queued.
- `GET /status` → `{ sessionStatus, attachedClients, pending, lastProcessedDeliveryId }`.
- `GET /healthz` → liveness (tmux session present).

---

## Phase 1 — Operator webhook server + delivery sidecar (replace cron)

**Goal:** kill the per-container `gh` poll. Events arrive by webhook; the agent is unchanged.

### 1.1 Operator: webhook server (no CRD yet)
- [ ] Scaffold the operator as a **Python + kopf** service (decided — see Repository strategy). kopf
      runs the reconcile loop; mount an HTTP server alongside it (kopf supports a built-in aiohttp
      server, or run `aiohttp`/`FastAPI` in the same process) for the webhook endpoint first.
- [ ] `POST /webhook`: verify `X-Hub-Signature-256` (HMAC, constant-time compare); reject otherwise.
- [ ] Dedupe on `X-GitHub-Delivery` (in-memory LRU for Phase 1; Redis later).
- [ ] Map events → messages, mirroring the checks in `monitor-pr.sh` (see contract B).
- [ ] Deliver to the pod via a per-pod HTTP inbox (Phase 1 transport; queue comes in Phase 3).

Reference shape (`operator/codemate_operator/webhook.py`):

```python
async def handle_webhook(request):
    body = await request.read()
    verify_signature(request.headers["X-Hub-Signature-256"], body, WEBHOOK_SECRET)  # hmac, compare_digest
    delivery = request.headers["X-GitHub-Delivery"]
    if not mark_seen_nx(delivery):           # idempotent: duplicate delivery → ack & drop
        return web.Response(status=200)
    event = request.headers["X-GitHub-Event"]
    if event == "ping":
        return web.Response(status=200)
    task = map_event(event, json.loads(body))   # contract B → PRTask | None
    if task is None:
        return web.Response(status=200)         # filtered/ignored
    if task.kind == "pr_opened":   ensure_prsession(task)     # create CR
    elif task.kind == "pr_closed": delete_prsession(task)     # delete CR
    elif task.kind == "bootstrap": bootstrap_pr(task)         # Phase 4
    else:                          enqueue(task)              # content event → sidecar/queue
    return web.Response(status=200)
```

> Return `200` fast and do slow work async — GitHub times out webhooks at ~10s and will retry,
> which the dedupe set absorbs.

### 1.2 Agent pod: delivery sidecar
- [ ] New tiny sidecar process in the pod that:
  - exposes `POST /inbox` (or consumes the queue in Phase 3),
  - buffers messages FIFO,
  - waits until `/tmp/.session_status` ends in `Stop` (reuse the existing idle signal),
  - injects via `send_and_verify_command` (lifted from `common.sh`).
- [ ] **Human-attach coordination (§4.1):** before delivering, check `tmux list-clients -t
      claude-code`; if a maintainer is attached, treat the session as busy and **hold** queued
      messages until they detach (human has priority over the webhook path).
- [ ] **Remove cron**: delete the crontab entry and `monitor-pr.sh` invocation from
      `docker/setup/setup.sh` / image; port its formatting logic into the operator's event mapper.
- [ ] Keep `run-claude.sh`, the workspace hooks, and all skills untouched.

Reference shape (`operator/sidecar/loop.py`) — this is `monitor-pr.sh`'s "act only when Stopped,
send one, verify" logic, event-driven:

```python
for msg in inbox.consume():                  # local FIFO (P1) / Redis Streams (P3), blocking
    if already_processed(msg.deliveryId):    # idempotency across redelivery/restart
        inbox.ack(msg); continue
    while human_attached():                   # tmux list-clients -t claude-code → hold for humans
        sleep(2)
    wait_until_idle()                         # /tmp/.session_status tail == "Stop"
    send_and_verify_command(SESSION, msg.prompt)        # reused from common.sh
    if msg.followup:
        wait_until_idle(); send_and_verify_command(SESSION, msg.followup)
    wait_until_idle()                         # let the turn finish (commit happens via Stop hook)
    mark_processed(msg.deliveryId)
    inbox.ack(msg)
```

### 1.3 Acceptance criteria
- A real PR review comment reaches Claude **via webhook** and gets a fix + reply, with **no cron**.
- Event parity check: for each `monitor-pr.sh` branch (issue comment, review comment, ready-for-review,
  CI failure), there is a matching operator path with an equivalent message.
- Latency from comment → message delivered is seconds, not up to 60s.

---

## Phase 2 — `PRSession` CRD + reconciler (auto-provision pods)

**Goal:** no more manual `codemate --pr`. The operator creates/deletes pods bound to PR lifecycle.

### 2.1 CRD
- [ ] Define `PRSession` (`apiVersion: codemate.io/v1alpha1`) with the `spec`/`status` from
      proposal §6.2; write the `openAPIV3Schema` and generate CRD manifests.
- [ ] `spec` fields mirror today's `.env`/container args (repo, prNumber, branch, image,
      systemPromptRef, resources, env, secretRefs).

### 2.2 Reconciler
- [ ] Watch `PRSession`; ensure exactly one Pod per CR (one-PR-per-pod), with resource limits +
      NetworkPolicy.
- [ ] Pod spec = today's container (image + env) + the delivery sidecar; mounts secrets from
      `secretRefs`.
- [ ] **Lifecycle binding:** webhook handler creates the CR on `pull_request.opened`, deletes it on
      `closed`/merged; reconciler GC's the pod. No idle scale-to-zero (persistent context).
- [ ] **Periodic resync:** every N min, list open PRs via API and converge (self-heal missed webhooks).
- [ ] Populate `.status` (phase, podName, prState, lastEventDeliveryId, sessionStatus, queue depth).
- [ ] **Finalizer + ownerReferences:** add a finalizer so pod + session-blob cleanup runs before the
      CR is removed; set the Pod's `ownerReference` to the `PRSession` for automatic GC.

Reference shape (`operator/codemate_operator/reconcile.py`):

```python
@kopf.on.create('prsessions'); @kopf.on.resume('prsessions')
def ensure_pod(spec, name, namespace, patch, **_):
    pod = build_pod(spec, name)              # image + sidecar + claude-auth PVC(RO) + secretRefs
    pod.metadata.owner_references = [owner_ref(name)]   # GC
    server_side_apply(namespace, pod)        # idempotent
    patch.status['phase'] = 'Running'

@kopf.on.delete('prsessions')               # gated by finalizer
def teardown(spec, status, **_):
    delete_pod(status.get('podName'))
    delete_session_blob(spec, status.get('claudeSessionId'))

@kopf.timer('prsessions', interval=300.0)   # missed-webhook backstop
def resync(spec, status, **_):
    pr = gh.get_pr(spec['repo'], spec['prNumber'])
    if pr.state == 'closed':
        kopf.adopt; delete_self()           # converge even if we missed pull_request.closed

@kopf.timer('prsessions', interval=30.0)    # idle scale-to-zero (Phase 3)
def idle_check(spec, status, patch, **_):
    if spec['idleTimeoutSeconds'] and idle_seconds(status) > spec['idleTimeoutSeconds'] \
       and status['queue']['pending'] == 0:
        scale_pod_to_zero(status['podName']); patch.status |= {'scaledToZero': True, 'phase': 'ScaledToZero'}
```

A separate cluster-scoped timer lists **open PRs without a `PRSession`** and creates them — the
other half of the backstop (catches missed `pull_request.opened`).

### 2.3 Claude auth: shared PVC + auth init pod (proposal §6.3)
- [ ] Provision an **RWX** PVC `codemate-claude-auth` (confirm cluster has an NFS/CephFS/filestore
      provisioner). Holds `~/.claude/` and `~/.claude.json`.
- [ ] Ship an **auth init pod** manifest that mounts the PVC; admin runs `kubectl exec -it ... --
      claude` once to complete `claude login`. Document the rotation/refresh procedure (re-run init).
- [ ] Mount the PVC **read-only** at the Claude home path in every PR pod; remove any per-pod Claude
      login from the image/startup. Verify a fresh pod uses the shared creds with no interactive step.
- [ ] Guard against `~/.claude.json` write races: agent pods RO; only the init pod writes creds.

### 2.4 PR-bound Claude session (foundation for scale-to-zero)
- [ ] On first run, capture the Claude session id and record it in `PRSession.status.claudeSessionId`;
      store the session blob under a per-PR subdir on the PVC (avoids cross-pod contention).
- [ ] Modify `run-claude.sh` to start with `claude --resume <claudeSessionId>` when one exists, else cold.
- [ ] On PR close/merge, delete the PR's session blob alongside the pod.

### 2.5 `codemate` CLI cluster mode (proposal §5.2)
- [ ] Add a cluster-backed mode to the `codemate` launcher: instead of `docker run` locally, create a
      `PRSession` CR (via `kubectl`/operator API) from `--branch`/`--pr`/`--repo`/`--query`.
- [ ] The pod creates the PR itself by **reusing `setup-repo.py`** (already clones + checks out
      branch/PR + opens a PR from the template) — no empty-commit trick needed; no issue required.
- [ ] Keep the existing **local `docker run`** path as the dev/back-compat mode (flag or auto-detect
      cluster context). Seed `--query` becomes the session's first message.

### 2.6 Acceptance criteria
- Opening a PR auto-creates a pod that picks up the PR description as the seed message.
- `codemate --branch x` (cluster mode) creates a `PRSession`, the pod opens a PR, and work begins —
  no issue involved; the result is an ordinary `PRSession` (webhook-driven, attachable).
- A fresh PR pod authenticates Claude purely from the shared PVC — no per-pod login.
- Closing/merging deletes the pod (and session blob) within one reconcile.
- `kubectl get prsessions` lists every active agent across repos.
- Killing a pod → reconciler recreates it, **resumes the same Claude session** (context intact), and
  the queue redelivers unacked messages.

---

## Phase 3 — Durable queue, GitHub App auth, quotas

**Goal:** production-grade reliability, auth, and cost control.

### 3.1 Queue
- [ ] Stand up Redis Streams (or NATS JetStream); one stream/consumer-group per `repo#pr`.
- [ ] Operator publishes; sidecar consumes + acks on `Stop`. Move dedupe cursor to the queue/CR.
- [ ] Replace the Phase 1 in-memory dedupe and per-pod HTTP inbox.

### 3.2 GitHub App auth (replace PAT)
- [ ] Register a GitHub App; operator mints short-lived installation tokens per repo.
- [ ] Project tokens into pods via mounted secrets (rotate on expiry). Remove the long-lived token
      from the image/`.env` flow.

### 3.3 Idle scale-to-zero via session resume (proposal §6.3)
- [ ] Reconciler watches `status.sessionStatus` + queue depth; after `spec.idleTimeoutSeconds` with
      no work, scale the pod to zero and set `status.scaledToZero=true` (keep the `PRSession`).
- [ ] On the next event, wake the pod; `run-claude.sh` resumes from `claudeSessionId` so context is intact.
- [ ] Tune `idleTimeoutSeconds` default; ensure wake latency (pull image + resume) is acceptable.

### 3.4 Quotas / cost control
- [ ] Per-repo max concurrent *active* `PRSession`s; ResourceQuota per namespace.
- [ ] `log()`/metrics when sessions are queued/rejected due to quota (no silent capping).

### 3.5 Acceptance criteria
- Operator restart loses no events (queue durability + redelivery).
- An idle PR's pod scales to zero, then wakes on the next event with **full context restored**.
- No long-lived PAT anywhere; tokens are per-repo and short-lived.
- Exceeding the quota queues/blocks new sessions visibly.

---

## Phase 4 — Issue-driven bootstrap + tunnels + dashboard

**Goal:** the web-UI entry point and zero-public-IP deployment.

### 4.1 Issue bootstrap (proposal §5.1)
- [ ] Trigger config: label (`codemate`) / slash command (`/codemate start`) / issue template.
- [ ] On trigger: create branch `codemate/issue-<n>-<slug>`, `git commit --allow-empty`, open a
      **draft PR** with body `Closes #<n>` + issue rules. Reuse/extend `setup-repo.py` PR-creation.
- [ ] Optionally pre-process with `/issue:read-issue` / `/issue:refine-issue` / `/issue:triage-issue`.
- [ ] The resulting `pull_request.opened` flows into Phase 2.

### 4.2 Webhook exposure (proposal §8) — Helm
- [ ] Chart value `webhook.expose.mode: ingress | cloudflare | frpc | both`.
- [ ] `cloudflared` Deployment (2 replicas) + `ConfigMap` (`config.yaml`) + token Secret ref.
- [ ] `frpc` Deployment (2 replicas) + `ConfigMap` (`frpc.toml`) + token Secret ref.
- [ ] Plain `Ingress` template for the `ingress` mode.

### 4.3 Dashboard / ops
- [ ] Minimal web view (or `kubectl`/k9s plugin) listing `PRSession`s + status.
- [ ] Slack/Lark notifications reuse `plugins/workspace/hooks/send_to_*.sh`.

### 4.4 Interactive attach helper (§4.1)
- [ ] `codemate attach --pr <n>` (and `--repo`): resolves the pod by `codemate.io/pr` label and runs
      `kubectl exec -it ... -- tmux attach -t claude-code`.
- [ ] **Wake-on-attach:** if the `PRSession` is scaled to zero, the helper asks the operator to scale
      it up (annotation / `kubectl scale`), waits for Ready (session resumes from PVC), then attaches.
- [ ] Document the RBAC needed (`pods/exec` in the `codemate` namespace) for this maintainer channel.

### 4.5 Acceptance criteria
- Filing an issue with the trigger label produces a draft PR and a running session end-to-end.
- A cluster with **no public IP** receives GitHub webhooks through `cloudflared` and/or `frpc`.
- Active sessions are visible at a glance.
- `codemate attach --pr <n>` from a remote machine drops into the live Claude session; webhook
  messages are held while attached and drain after detach.

---

## Helm chart layout (target)

```
charts/codemate/
  Chart.yaml
  values.yaml                 # operator, queue, webhook.expose, cloudflare, frpc, claudeAuth, quotas
  crds/
    prsession.yaml
  templates/
    operator-deployment.yaml  # webhook server + reconciler (leader election)
    operator-rbac.yaml        # watch/create/delete pods + prsessions
    operator-service.yaml
    queue.yaml                # or external dependency
    claude-auth-pvc.yaml      # RWX PVC for shared ~/.claude + ~/.claude.json
    claude-auth-init-pod.yaml # one-time `claude login` helper (kubectl exec)
    cloudflared-deployment.yaml   # rendered when expose.mode in (cloudflare, both)
    frpc-deployment.yaml          # rendered when expose.mode in (frpc, both)
    ingress.yaml                  # rendered when expose.mode == ingress
    networkpolicy.yaml
```

## Reliability & edge cases

Concrete handling for the failure modes the happy path ignores. Build these into Phase 3 unless noted.

| # | Risk | Handling |
|---|------|----------|
| R1 | **At-least-once delivery → double actions** (e.g. two replies/commits) | `deliveryId` dedupe at three layers: webhook (`seen` set), sidecar (`already_processed`), and GitHub-visible side effects are naturally idempotent (👀 reaction, `Claude Replied:` prefix filter). Ack only *after* the turn settles. |
| R2 | **Out-of-order webhooks** (GitHub gives no ordering guarantee) | On wake, the sidecar prompt is built to be **self-contained**, and for `ready`/`ci_failure` the agent re-reads live PR state via skills rather than trusting event order. Per-PR stream ordering covers the common case; cross-event ordering is not relied upon. |
| R3 | **Poison message** (always crashes the turn) | Redis Streams `XCLAIM`/delivery-count; after `maxDeliveries` (default 3) move to `codemate:dlq:*`, set `PRSession` condition `NeedsAttention`, notify Slack/Lark. Never block the stream. |
| R4 | **Stuck / runaway session** (Claude hangs or loops) | Per-task watchdog: if no `Stop` within `taskTimeout` (e.g. 20m), capture the pane, mark `NeedsAttention`, and either restart the pod (resume session) or skip the message. Mirrors `check_git_changes` `MAX_BLOCKS` guard. |
| R5 | **Git divergence** (human pushes to the branch mid-session) | Before each `/git:commit`, the flow does `git pull --rebase` (already typical); on conflict the agent is prompted to resolve. Never force-push the user's branch without an explicit instruction. |
| R6 | **Abandoned bootstrap/CLI PRs** (draft PR nobody touches) | Reaper timer: draft PRs with no human activity for `staleTTL` (e.g. 14d) → comment, then close PR + delete branch + `PRSession`. Log what was reaped (no silent cleanup). |
| R7 | **Operator crash / rolling update** | kopf leader election; webhook served by all replicas (stateless); in-flight reconciles resume from CR state. Queue + `seen` set are external, so no loss. |
| R8 | **PVC unavailable / Claude auth expired** | Pod readiness gate checks the auth PVC mounts and creds load; on expiry the operator sets `NeedsAttention` and points to re-running the auth init pod. |
| R9 | **Scale-to-zero races a webhook** | Idle-check and enqueue both touch `status`; wake is triggered by any `pending>0`. Use resourceVersion/optimistic concurrency on the status patch so a message arriving during scale-down re-wakes the pod. |
| R10 | **Reopened PR** | If the session blob still exists on the PVC, resume it; else start cold. `claudeSessionId` is the key. |

New `spec` knobs introduced here: `maxDeliveries`, `taskTimeoutSeconds`, `staleTTLSeconds`.

## Cross-cutting

- **Testing:** no suite exists today. Add:
  - (a) **unit** — event mapper (a corpus of recorded GitHub payloads → expected `PRTask`), HMAC
    verify, dedupe;
  - (b) **integration** — kopf against `kind`/envtest: create `PRSession` → pod appears; delete → GC;
  - (c) **smoke** — fake webhook → pod → real commit on a scratch repo;
  - (d) **sidecar** — idle-gating + human-attach hold against a stub tmux.
- **Observability:** structured JSON logs + Prometheus metrics, all surfaced in `PRSession.status`:
  - `codemate_webhooks_total{event,result}` (result = accepted|deduped|filtered|invalid_sig)
  - `codemate_messages_delivered_total{kind}`, `codemate_message_deliver_seconds` (comment→inject)
  - `codemate_reconcile_errors_total`, `codemate_sessions{phase}`, `codemate_queue_pending{pr}`
  - `codemate_scale_to_zero_total`, `codemate_wake_seconds` (wake → ready)
  - **SLO targets:** webhook→inject p95 < 30s (warm), wake→inject p95 < 60s (cold); reconcile error rate < 1%.
  - **Audit:** one structured log line per event with `{deliveryId, repo, pr, kind, actor, action}`.
- **Docs:** update `CLAUDE.md` (new run model), add an operator README and a "self-hosting with
  cloudflared/frpc" guide.
- **Backward compatibility:** keep `codemate --pr/--branch` working as a local/dev path during the
  migration; the operator path is opt-in per repo until Phase 3 is stable.
- **Prerequisite — RWX storage:** Phase 2's shared Claude-auth PVC needs a ReadWriteMany storage
  class (NFS/CephFS/cloud filestore). Verify on the target cluster before starting Phase 2.
- **Deferred — trust/prompt-injection model:** per the architecture doc (open question #8), gating
  who can trigger the agent and hardening against injection is **out of scope** for this plan. Do
  **not** point the operator at repos with untrusted external contributors until that work lands.

## Sequencing & dependencies

```
Phase 1 (operator webhook + sidecar, drop cron)  ── shippable
        │
Phase 2 (CRD + reconciler, auto pods)            ── depends on P1 operator
        │
Phase 3 (queue + GitHub App + quotas)            ── depends on P2
        │
Phase 4 (issue bootstrap + tunnels + dashboard)  ── bootstrap depends on P2; tunnels independent
```

`webhook.expose` tunnels (4.2) have no code dependency on P1–P3 and can be built in parallel as
soon as the operator has a Service.

## Milestones & rough effort

Estimates are eng-weeks for one engineer, excluding review/iteration. Treat as relative sizing.

| Milestone | Scope | Effort | Exit criterion |
|-----------|-------|--------|----------------|
| **M1** | Operator skeleton + webhook verify + event mapper + payload corpus tests | ~1w | a recorded payload maps to the right `PRTask` in unit tests |
| **M2** | Sidecar + HTTP inbox + cron removal; wire into the existing image | ~1w | comment → fix, no cron (Phase 1 acceptance) |
| **M3** | `PRSession` CRD + reconciler (create/delete/finalizer/resync) | ~1.5w | open/close PR ↔ pod lifecycle; `kubectl get prsessions` |
| **M4** | Claude-auth PVC + init pod + session resume; `codemate` cluster mode | ~1w | fresh pod authed from PVC; CLI creates a working session |
| **M5** | Redis queue + dedupe/DLQ + GitHub App auth + quotas | ~1.5w | operator restart loses nothing; no PAT (Phase 3 acceptance) |
| **M6** | Idle scale-to-zero + reliability items R3–R10 | ~1.5w | idle pod scales to 0 and wakes with context (Phase 3 acceptance) |
| **M7** | Issue bootstrap + Helm tunnels + attach helper + dashboard | ~2w | issue→PR e2e; webhook reaches a no-public-IP cluster (Phase 4 acceptance) |

Critical path: M1 → M2 → M3 → M5 → M6. M4 parallels M3; M7's tunnels parallel everything.

## Decisions & open implementation decisions

- **Operator language — DECIDED: Python + kopf.** Matches the existing Python tooling
  (`setup-repo.py`, `setup-ccline.py`), keeps the whole repo in bash/python/Docker + a Helm chart
  (no Go toolchain to add), and lets the event mapper reuse logic ported from `monitor-pr.sh`. kopf
  handles the CRD watch/reconcile; an aiohttp/FastAPI server in the same process serves the webhook.
- **Repository — DECIDED: monorepo (this repo).** See [Repository strategy](#repository-strategy).
- Queue *(open)*: Redis Streams (simple, ubiquitous) vs. NATS JetStream (lighter, native subjects).
- Delivery *(decided)*: **sidecar** over an in-pod cron-style loop (cleaner lifecycle, testable).
- See the architecture doc §10 for the broader open questions (transport, persistence, issue linkage).
