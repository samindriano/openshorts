# OpenShorts repository-wide orchestra policy

This file adapts the control-plane rules from `samindriano/codex-orchestra`
and the IDX Trade orchestra snapshot to OpenShorts video-product engineering.
It governs execution topology and integration safety; it does not authorize
new product scope, credentials, publishing, deployment, or production writes.

## Authority and scope

- User instructions, this repository's branch-local requirements, tests,
  security constraints, and the newest verified runtime evidence are
  authoritative.
- A stale orchestration document never overrides current code, tests, or an
  explicit user instruction. Re-check the repository state before relying on
  an old handoff or status entry.
- Never use parallel work to bypass an approval, expose credentials, publish
  content, deploy infrastructure, or overwrite user data.
- Preserve unrelated user changes. Do not reset, clean, force-push, or rewrite
  history unless the user explicitly asks for that exact operation.

## MAIN and the execution frontier

The parent/root task is `MAIN`: the sole control plane and integrator. Before
any meaningful task, MAIN records the **execution frontier**:

1. scopes that can start now without another unfinished result;
2. which scopes are independent and non-overlapping;
3. which coupled or cross-cutting work MAIN retains;
4. which ready scopes should be delegated immediately.

MAIN must not hoard independent critical-path work merely because it can do it
sequentially. Do not manufacture parallelism by splitting one tightly coupled
edit, and do not duplicate work except for an explicitly independent review or
comparison.

## Execution levels

- `DIRECT`: one small or inherently sequential path; MAIN works directly with
  proportional validation. A substantial DIRECT choice records why workers
  would not reduce wall-clock time.
- `LIGHT`: the default for meaningful work with roughly 2–3 independent paths;
  MAIN plus 1–3 bounded workers launched early.
- `HEAVY`: roughly 3–6 independent critical-path paths, a broad separable
  migration/debugging task, or a decision-changing independent review.

De-escalate when the remaining dependency chain becomes sequential. HEAVY is a
parallelism level, not permission to change scope or to use a stronger model.
The user selects the root model; do not hardcode or silently switch it.

## Worker and worktree safety

- Only MAIN creates workers and integrates their results. Workers never spawn
  workers, merge, rebase, force-push, or rewrite history.
- Every concurrent writer gets a separate worktree and branch based on a
  verified base commit. Never let two writers share a worktree, branch, output
  directory, Docker Compose project, or development port.
- Before starting, verify the absolute Git root, branch, HEAD, status, remote,
  and `git worktree list`. A worktree path is part of the task contract.
- Give each runtime a unique job/output directory and, when needed, a unique
  Compose project name and host-port allocation. Do not run two workers against
  the same OpenShorts `output/` job or a shared mutable database.
- Do not switch branches or run checkout/reset commands in a worktree another
  task may be using. Integrate with cherry-pick or a deliberate merge from
  MAIN after checking the diff and tests.
- Read-only workers may inspect the same repository; writers may not overlap
  file ownership. Shared coordination files belong to MAIN.

## Ownership

| Role | Typical owned scope |
|---|---|
| `BACKEND` | FastAPI routes, Python pipeline, FFmpeg contracts, backend tests |
| `FRONTEND` | dashboard React/UI, browser rendering, frontend tests/build |
| `MEDIA` | reframing, subtitles, hooks, Remotion/render-service behavior |
| `RUNTIME` | Docker/Compose, ports, health checks, dependencies, deployment docs |
| `VALIDATION` | regression tests, artifact inspection, runtime verification |
| `MAIN` | integration, shared docs/coordination, cross-cutting fixes, final decision |

Only MAIN edits root `AGENTS.md`, `coordination/`, and final shared decision
records. A worker needing another role's file sends a written handoff instead
of silently taking ownership.

## Task contract and handoff

Every worker receives:

```text
repository/worktree:
base commit:
task id:
parallel group:
role:
question/task:
why this can run now:
owned files/scope:
prohibited changes:
dependencies/assumptions:
deliverable:
validation required:
integration contract:
handoff path:
stopping condition:
```

Every delegated task returns a concise decision-complete handoff under
`coordination/handoffs/`. It records the source commit, branch, files changed,
findings, decisions, blockers, validation, and the smallest safe next action.

## Integration, commit, and push

- MAIN reviews actual diffs, tests, generated-artifact scope, and runtime
  evidence before accepting a worker result.
- Stage only named files or hunks. Never use `git add .` or `git add -A`
  blindly.
- MAIN is expected to create a local commit for a complete coherent task after
  proportional validation; the user does not need to create that commit by
  hand.
- Pushing is a separate external mutation. Do not push, open a PR, merge, or
  modify a remote unless the user explicitly requests that action. Before a
  requested push, fetch/check divergence, refuse non-fast-forward surprises,
  and never force-push without explicit authorization.
- A worker hands off a commit or clean diff; MAIN owns cherry-pick/merge and
  final branch integration.

## OpenShorts runtime guardrails

- Treat API keys, cookies, S3 objects, user videos, social accounts, and
  publishing as protected resources. Use placeholders/mocks or existing local
  fixtures unless the user authorizes live access.
- Keep generated media and caches outside Git. Never commit `output/`, model
  weights, credentials, downloaded sources, or browser/session state.
- Prefer a focused render/test artifact in a task-isolated temporary directory;
  record its provenance and clean it up when it is no longer needed.
- Validate backend, dashboard, renderer, and player contracts at their actual
  boundary. A successful HTTP response is not enough if the returned video,
  metadata, download URL, or browser player still points to stale output.
