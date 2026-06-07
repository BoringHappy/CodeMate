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

```
                      GitHub
   (PR opened / review comment / issue comment / review / check_suite / push)
                        │  webhook (HMAC-signed)
                        ▼
        ┌───────────────────────────────────┐
        │      Webhook Gateway (middle       │   - verify X-Hub-Signature-256
        │      layer, always-on Service)     │   - normalize event → PRTask
        │                                    │   - dedupe, route by repo+PR
        └───────────────┬───────────────────┘
                        │ enqueue PRTask (key = repo#pr)
                        ▼
        ┌───────────────────────────────────┐
        │   Message Queue / Inbox            │   Redis Streams / NATS
        │   (durable, per-PR ordering)       │   one consumer group per PR
        └───────────────┬───────────────────┘
                        │
                        ▼
        ┌───────────────────────────────────┐
        │   Session Controller (operator)    │   reconciles PRSession CRDs:
        │                                     │   pod exists while PR is open;
        │                                     │   deleted on PR close/merge
        └───────────────┬───────────────────┘
                        │ create/scale/delete
          ┌─────────────┼───────────────────────────────┐
          ▼             ▼                                 ▼
   ┌────────────┐  ┌────────────┐                  ┌────────────┐
   │ Pod: PR #1 │  │ Pod: PR #2 │      ...         │ Pod: PR #N │
   │ ┌────────┐ │  │            │                  │            │
   │ │sidecar │ │  drain queue → wait for idle (/tmp/.session_status)
   │ │ (loop) │ │  → tmux send-keys (send_and_verify_command)
   │ └───┬────┘ │  │            │                  │            │
   │ ┌───▼────┐ │  persistent Claude `tmux` session reviews code,
   │ │ tmux   │ │  commits/pushes via /git:commit
   │ │ Claude │ │  │            │                  │            │
   │ └────────┘ │  │            │                  │            │
   │ git checkout of PR branch (isolated workspace) │            │
   └────────────┘  └────────────┘                  └────────────┘
```

### 3.1 Components

**A. Webhook Gateway (the "middle layer")** — small always-on service:
- Public HTTPS endpoint behind ingress; verifies `X-Hub-Signature-256` HMAC.
- Normalizes each GitHub event into an internal `PRTask` `{repo, pr, kind, payload, delivery_id}`.
- Dedupes on GitHub `X-GitHub-Delivery` id; enqueues keyed by `repo#pr` so a PR's events stay ordered.
- Stateless and horizontally scalable.
- It is the *source* of messages (replacing the in-container `gh` poll); delivery into the
  session still happens via `tmux send-keys` inside the pod (see §4).

**B. Message Queue / Inbox** — durable buffer (Redis Streams or NATS JetStream):
- Per-PR ordering and at-least-once delivery; survives pod restarts (fixes pain #5).
- If a pod crashes and is rescheduled, unacked messages are redelivered — no events are lost
  across the restart.

**C. Session Controller (operator)** — reconciles a `PRSession` custom resource:
- **Pod lifetime == PR lifetime.** Create exactly one pod when a PR opens (or on its first event);
  keep it running for the entire open lifetime; **shut it down when the PR is closed/merged**.
- One-PR-per-pod, kept on purpose; applies resource limits and network policy.
- No idle scale-to-zero: the persistent tmux session holds in-memory conversation context, so
  reaping it mid-PR would discard that context. The PR's open/closed state *is* the lifecycle
  signal (see §6).

**D. Per-PR Agent Pod** — the existing CodeMate image, **minus cron but keeping tmux**:
- The pod runs the same long-lived Claude `tmux` session as today (`run.sh`).
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

- **Persistent context.** A single long-lived session accumulates the conversation across many
  webhook events (description → review comment → CI failure → follow-up). One-shot headless runs
  would lose that context every event and have to re-establish it.
- **Zero rewrite of the proven path.** `send_and_verify_command`, the `/tmp/.session_status` idle
  signal, and the `check_git_changes` Stop hook all carry over unchanged — we are only swapping the
  cron poller for a webhook-fed queue in front of them.

So the sidecar's loop is essentially today's `monitor-pr.sh` "act only when Stopped, send one
message, verify submission" logic — but woken by a queue message instead of a 60s cron tick.

## 5. Event → action mapping

The gateway replaces every check currently in `monitor-pr.sh`:

| GitHub webhook | Action delivered to the PR pod |
|----------------|--------------------------------|
| `pull_request.opened` / `reopened` | Provision pod; seed initial run with the **PR description** as the requirement |
| `issue_comment.created` (on a PR) | Run with the comment text as prompt → address it, then `/pr:ack-comments` (👀) |
| `pull_request_review_comment.created` | Run `/pr:fix-comments` against the inline comment |
| `pull_request_review.submitted` | Run with the review summary as context |
| `pull_request.ready_for_review` | Run `/pr:update` to refresh title/description |
| `check_suite.completed` / `check_run.completed` (failure) | Fetch failing logs, run a fix, `/git:commit` |
| `pull_request.closed` / merged | Controller reaps the pod |
| `issues.opened` / `issue_comment` (requirement issues) | Optionally spawn a "spec" session before a PR exists |

Note the last row: because requirements can be written in **issue** comments too, the gateway can
kick off work that *creates* a PR, not just react to an existing one.

## 6. Lifecycle, state & isolation

**A pod's lifecycle is bound 1:1 to its PR's lifecycle:**

```
pull_request.opened ─────► controller creates Pod (PRSession) ─────► persistent
                                                                      tmux session
   review comments / issue comments / CI events  ──► injected into the live session
                                                                          │
pull_request.closed / merged ─────► controller deletes Pod  ◄────────────┘
```

- The pod starts when the PR opens and runs continuously while the PR is open, so its tmux session
  keeps full conversation context across every webhook event.
- When the PR is **closed or merged**, the controller tears the pod down (and frees its resources).
  A reopen recreates a fresh pod.
- **Mapping:** `PRSession` CRD keyed by `repo + pr`; pods labeled `codemate.io/repo`,
  `codemate.io/pr`. The gateway/controller find a session by label, never by host.
- **Workspace:** ephemeral — git is the source of truth, so a *crash-restarted* pod just
  re-checks-out the PR branch. (PVC-per-PR is an option if warm caches matter; recommend ephemeral.)
- **State that used to live in `/tmp/pr-monitor-state`** (last-checked time, dedupe, CI-notified
  flags) moves to the queue/Redis or `PRSession.status` — durable and centrally visible. In-session
  conversation context is intentionally *not* persisted; it lives only for the pod's (== PR's) life.
- **One PR per pod** is retained on purpose: isolated git checkout, independent crash blast radius,
  per-PR CPU/mem limits, trivial routing. Scaling = more pods, scheduled by k8s.

### 6.1 End-to-end sequence

A single event — a reviewer leaving an inline comment — flows like this:

```
GitHub        Gateway         Queue          Controller     Sidecar         tmux/Claude
  │              │              │                 │             │                 │
  │ pull_request_review_comment │                 │             │                 │
  ├─────────────►│              │                 │             │                 │
  │              │ verify HMAC  │                 │             │                 │
  │              │ dedupe(delivery_id)            │             │                 │
  │              ├─ enqueue ────►│ (key=repo#pr)  │             │                 │
  │              │              │                 │             │                 │
  │              │              │  PRSession for repo#pr exists? │                 │
  │              │              │◄────────────────┤ (pod already up; PR is open)  │
  │              │              │                 │             │                 │
  │              │              │  consume(repo#pr)             │                 │
  │              │              │◄────────────────────────────┤ (sidecar pulls) │
  │              │              │                 │             │ wait for idle   │
  │              │              │                 │             │ (.session_status│
  │              │              │                 │             │   ends in Stop) │
  │              │              │                 │             │ send_and_verify ├────►│
  │              │              │                 │             │                 │ /pr:fix-comments
  │              │              │                 │             │                 │ edit → /git:commit → push
  │              │              │                 │             │ ack (eyes 👀)   │◄────┤
  │              │              │◄ ack message ───┤ (on Stop)   │                 │
```

For `pull_request.opened` the only difference is an extra first step: the controller **creates**
the `PRSession`/pod, which boots the tmux session (`run.sh`) before the sidecar delivers the PR
description as the seed message. For `pull_request.closed`/merged the controller **deletes** the
`PRSession`, and Kubernetes garbage-collects the pod.

### 6.2 `PRSession` custom resource

The controller reconciles one `PRSession` per open PR. It is the single source of truth for
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
  resources:
    requests: { cpu: "500m", memory: 1Gi }
    limits:   { cpu: "2",    memory: 4Gi }
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

## 7. Security

- **Webhook auth:** HMAC `X-Hub-Signature-256` verified at the gateway; reject otherwise.
- **GitHub App over PAT:** the gateway mints short-lived, per-repo installation tokens and
  projects them into pods as mounted secrets — instead of baking one long-lived token into every
  container. Carries over the existing region check (`check-region.sh`) at the gateway tier.
- **Network policy:** pods may egress only to GitHub + the queue; only the gateway is publicly
  reachable (TLS via ingress).
- **Blast radius:** `--dangerously-skip-permissions` stays, but now each PR runs in its own pod
  with k8s resource limits and network isolation — a strictly tighter sandbox than a shared host.

## 8. Phased rollout

| Phase | Deliverable | Risk |
|-------|-------------|------|
| **0** | *(today)* cron polling + tmux in a single container | — |
| **1** | Stand up the gateway; replace the in-container cron poll with a webhook-fed inbox sidecar that injects into the **existing tmux session** via `send_and_verify_command`. Prove event parity with `monitor-pr.sh`. | Low — reuses image, tmux path & skills |
| **2** | Add the controller + `PRSession` CRD; auto-provision a pod on `pull_request.opened` and **tear it down on `pull_request.closed`/merged** (no more manual `codemate --pr`). | Medium |
| **3** | Durable queue (Redis Streams/NATS) + GitHub App auth + per-repo session quotas. | Medium |
| **4** | Multi-repo, web dashboard of active sessions, requirement-issue → auto-spawn. | — |

The existing `dev:run-image` / `dev:manage-k8s` plugins are natural building blocks for Phase 2's
pod provisioning and operational tooling.

## 9. Open questions

1. **Delivery transport:** direct HTTP to a per-pod sidecar vs. a shared durable queue.
   *Recommendation: queue* — durability and crash-redelivery outweigh the added component.
2. **Workspace persistence:** ephemeral re-checkout vs. PVC-per-PR. *Recommendation: ephemeral.*
3. **Operator implementation:** full CRD + controller-runtime/kopf vs. a plain service that just
   manages Deployments by label. Start simple (Phase 1/2), formalize the CRD in Phase 3.
4. **Concurrency within a PR:** strictly serial runs (proposed) vs. allowing a quick read-only run
   to interleave. Serial is safest for git; revisit if latency hurts.
5. **Cost controls:** per-repo pod quotas / max concurrent sessions to bound spend.

## 10. Summary

Move the GitHub-watching logic *out* of every container and into a single **webhook gateway**
(the middle layer), route events through a **durable per-PR queue**, and let a **Kubernetes
operator** run **one persistent-tmux Claude pod per PR**, with the **pod's lifecycle bound to the
PR's** (created on open, destroyed on close/merge). Webhook messages are injected into the live TUI
via the existing `send_and_verify_command`, preserving conversation context across events. This
removes the per-container 60s polling loop, scales to many concurrent PRs, and lets requirements be
expressed naturally in PR/issue descriptions and comments.
