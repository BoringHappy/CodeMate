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
- **No TUI.** The agent runs **headless** (review/act on code), not an interactive tmux session
  we type into.
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
        │                                     │   ensure a pod exists for any
        │                                     │   PR with pending work; reap idle
        └───────────────┬───────────────────┘
                        │ create/scale/delete
          ┌─────────────┼───────────────────────────────┐
          ▼             ▼                                 ▼
   ┌────────────┐  ┌────────────┐                  ┌────────────┐
   │ Pod: PR #1 │  │ Pod: PR #2 │      ...         │ Pod: PR #N │
   │ ┌────────┐ │  │            │                  │            │
   │ │ runner │ │  drain queue → run `claude -p "<prompt>"` (headless)
   │ │ (loop) │ │  serialize per PR → commit/push via /git:commit
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

**B. Message Queue / Inbox** — durable buffer (Redis Streams or NATS JetStream):
- Per-PR ordering and at-least-once delivery; survives pod restarts (fixes pain #5).
- Lets the controller scale a pod to zero and replay buffered events when it comes back.

**C. Session Controller (operator)** — reconciles a `PRSession` custom resource:
- For each PR with pending tasks, ensure exactly one pod exists (one-PR-per-pod, kept on purpose).
- Applies resource limits, network policy, and **idle reaping** (scale-to-zero after N idle minutes).
- Tears down on `pull_request.closed`/merged.

**D. Per-PR Agent Pod** — the existing CodeMate image, minus cron and minus tmux:
- A lightweight **runner loop** consumes the PR's queue and, per message, executes a **headless**
  Claude run: `claude -p "<prompt>" --dangerously-skip-permissions --append-system-prompt ...`.
- Runs are **serialized per PR** (one at a time) so git operations never race within a checkout.
- Reuses existing skills end-to-end: `/pr:fix-comments`, `/pr:update`, `/git:commit`, `/pr:ack-comments`.

## 4. Headless execution (no TUI)

This is the key simplification from the original sketch. We **do not** keep an interactive tmux
session and type into it. Each task is a one-shot headless invocation:

| Today (TUI) | Proposed (headless) |
|-------------|---------------------|
| Persistent `tmux` session | Per-task `claude -p` process (or Agent SDK call) |
| `send_and_verify_command` (`send-keys` + `Enter` workaround) | Pass the prompt as an argument — no keystroke injection |
| `/tmp/.session_status` polling to know when idle | Process exit = task done; runner pulls the next message |
| `check_git_changes` Stop-hook nudges a commit | Runner ends each task with `/git:commit`; commit is part of the run |

**Eliminated:** the tmux send-keys layer, the extended-keys `Enter` encoding hack, the
status-file verify/retry loop, and the "only act when Stopped" cron gating. The queue + serialized
runner give the same "one message at a time, in order" semantics far more robustly.

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

- **Mapping:** `PRSession` CRD keyed by `repo + pr`; pods labeled `codemate.io/repo`,
  `codemate.io/pr`. The gateway/controller find a session by label, never by host.
- **Workspace:** ephemeral by default — git is the source of truth, so a re-provisioned pod just
  re-checks-out the PR branch. (PVC-per-PR is an option if warm caches matter; recommend ephemeral.)
- **State that used to live in `/tmp/pr-monitor-state`** (last-checked time, dedupe, CI-notified
  flags) moves to the queue/Redis or `PRSession.status` — durable and centrally visible.
- **One PR per pod** is retained on purpose: isolated git checkout, independent crash blast radius,
  per-PR CPU/mem limits, trivial routing. Scaling = more pods, scheduled by k8s.

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
| **1** | Stand up the gateway; convert the agent to **headless** runs driven by an HTTP inbox; disable cron. Prove event parity with `monitor-pr.sh`. | Low — reuses image & skills |
| **2** | Add the controller + `PRSession` CRD; auto-provision pods on `pull_request.opened` (no more manual `codemate --pr`). | Medium |
| **3** | Durable queue (Redis Streams/NATS) + idle reaping/scale-to-zero + GitHub App auth. | Medium |
| **4** | Multi-repo, web dashboard of active sessions, requirement-issue → auto-spawn. | — |

The existing `dev:run-image` / `dev:manage-k8s` plugins are natural building blocks for Phase 2's
pod provisioning and operational tooling.

## 9. Open questions

1. **Delivery transport:** direct HTTP to a per-pod sidecar vs. a shared durable queue.
   *Recommendation: queue* — durability, replay, and scale-to-zero outweigh the added component.
2. **Workspace persistence:** ephemeral re-checkout vs. PVC-per-PR. *Recommendation: ephemeral.*
3. **Operator implementation:** full CRD + controller-runtime/kopf vs. a plain service that just
   manages Deployments by label. Start simple (Phase 1/2), formalize the CRD in Phase 3.
4. **Concurrency within a PR:** strictly serial runs (proposed) vs. allowing a quick read-only run
   to interleave. Serial is safest for git; revisit if latency hurts.
5. **Cost controls:** per-repo pod quotas / max concurrent sessions to bound spend.

## 10. Summary

Move the GitHub-watching logic *out* of every container and into a single **webhook gateway**
(the middle layer), route events through a **durable per-PR queue**, and let a **Kubernetes
operator** run **one headless Claude pod per PR**. This removes the per-container polling loop and
the fragile TUI keystroke injection, scales to many concurrent PRs, and lets requirements be
expressed naturally in PR/issue descriptions and comments.
