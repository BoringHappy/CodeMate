# Implementation Plan: Webhook-Driven, Kubernetes-Orchestrated CodeMate

**Companion to:** [`k8s-webhook-architecture.md`](./k8s-webhook-architecture.md)
**Status:** Draft
**Branch:** `proposal-k8s-design`

This plan turns the architecture proposal into concrete, sequenced work. It maps each phase from
§9 of the proposal to workstreams with deliverables, the existing files they reuse/replace, and
acceptance criteria. Each phase is independently shippable and leaves the system working.

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
| Operator (webhook server + reconciler) | **New** (Go + controller-runtime, or Python + kopf) | — |
| `PRSession` CRD | **New** | — |
| Delivery sidecar (queue consumer → tmux) | **New** (thin) | logic adapted from `docker/setup/shell/monitor-pr.sh` |
| Message queue | **New** (Redis Streams / NATS) | replaces `/tmp/pr-monitor-state` |
| Shared Claude-auth PVC (`~/.claude`, `~/.claude.json`) | **New** (RWX) | replaces per-container Claude login |
| Claude auth init pod | **New** (one-time `claude login` via `kubectl exec`) | — |
| PR-bound Claude session store + resume | **New** | `run.sh` gains `claude --resume` |
| Agent pod image | **Modify** | `docker/Dockerfile`, `docker/setup/*` (drop cron) |
| Tmux launch + injection | **Reuse** | `docker/setup/run.sh`, `common.sh::send_and_verify_command` |
| Idle/commit hooks | **Reuse** | `plugins/workspace/hooks/*` |
| Repo/branch/PR bootstrap | **Reuse/extend** | `docker/setup/python/setup-repo.py` |
| Helm chart | **New** | — |
| Tunnels (cloudflared / frpc) | **New** (chart templates) | — |

---

## Phase 1 — Operator webhook server + delivery sidecar (replace cron)

**Goal:** kill the per-container `gh` poll. Events arrive by webhook; the agent is unchanged.

### 1.1 Operator: webhook server (no CRD yet)
- [ ] Scaffold the operator service (recommend **Go + controller-runtime**; kopf is fine if the team
      prefers Python). One binary, HTTP server first.
- [ ] `POST /webhook`: verify `X-Hub-Signature-256` (HMAC, constant-time compare); reject otherwise.
- [ ] Dedupe on `X-GitHub-Delivery` (in-memory LRU for Phase 1; Redis later).
- [ ] Map events → messages, mirroring the checks in `monitor-pr.sh`:
      `issue_comment`, `pull_request_review_comment`, `pull_request_review`,
      `pull_request.ready_for_review`, `check_suite/check_run` failures.
- [ ] Deliver to the pod via a per-pod HTTP inbox (Phase 1 transport; queue comes in Phase 3).

### 1.2 Agent pod: delivery sidecar
- [ ] New tiny sidecar process in the pod that:
  - exposes `POST /inbox` (or consumes the queue in Phase 3),
  - buffers messages FIFO,
  - waits until `/tmp/.session_status` ends in `Stop` (reuse the existing idle signal),
  - injects via `send_and_verify_command` (lifted from `common.sh`).
- [ ] **Remove cron**: delete the crontab entry and `monitor-pr.sh` invocation from
      `docker/setup/setup.sh` / image; port its formatting logic into the operator's event mapper.
- [ ] Keep `run.sh`, the workspace hooks, and all skills untouched.

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

### 2.4 Claude auth: shared PVC + auth init pod (proposal §6.3)
- [ ] Provision an **RWX** PVC `codemate-claude-auth` (confirm cluster has an NFS/CephFS/filestore
      provisioner). Holds `~/.claude/` and `~/.claude.json`.
- [ ] Ship an **auth init pod** manifest that mounts the PVC; admin runs `kubectl exec -it ... --
      claude` once to complete `claude login`. Document the rotation/refresh procedure (re-run init).
- [ ] Mount the PVC **read-only** at the Claude home path in every PR pod; remove any per-pod Claude
      login from the image/startup. Verify a fresh pod uses the shared creds with no interactive step.
- [ ] Guard against `~/.claude.json` write races: agent pods RO; only the init pod writes creds.

### 2.5 PR-bound Claude session (foundation for scale-to-zero)
- [ ] On first run, capture the Claude session id and record it in `PRSession.status.claudeSessionId`;
      store the session blob under a per-PR subdir on the PVC (avoids cross-pod contention).
- [ ] Modify `run.sh` to start with `claude --resume <claudeSessionId>` when one exists, else cold.
- [ ] On PR close/merge, delete the PR's session blob alongside the pod.

### 2.6 Acceptance criteria
- Opening a PR auto-creates a pod that picks up the PR description as the seed message.
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
- [ ] On the next event, wake the pod; `run.sh` resumes from `claudeSessionId` so context is intact.
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

### 4.4 Acceptance criteria
- Filing an issue with the trigger label produces a draft PR and a running session end-to-end.
- A cluster with **no public IP** receives GitHub webhooks through `cloudflared` and/or `frpc`.
- Active sessions are visible at a glance.

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

## Cross-cutting

- **Testing:** no suite exists today. Add (a) unit tests for the event mapper + HMAC verify, (b) an
  envtest/kind integration test for the reconciler, (c) a smoke test: fake webhook → pod → commit.
- **Observability:** structured logs + metrics (events received/deduped/delivered, reconcile errors,
  queue depth, sessions active). Surface in `PRSession.status`.
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

## Open implementation decisions

- Operator language: **Go + controller-runtime** (mature CRD tooling) vs. **Python + kopf** (matches
  existing `setup-repo.py`). *Leaning Go* for the reconciler; the event mapper could start in Python.
- Queue: Redis Streams (simple, ubiquitous) vs. NATS JetStream (lighter, native subjects).
- Sidecar vs. in-pod cron-style loop for delivery: **sidecar** (cleaner lifecycle, testable).
- See the architecture doc §10 for the broader open questions (transport, persistence, issue linkage).
