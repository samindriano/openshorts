# OpenShorts orchestration

The parent/root task is `MAIN`, the sole control plane and integrator. This
document is the OpenShorts adaptation of the control-plane pattern maintained
in `samindriano/codex-orchestra` and its IDX Trade orchestration snapshot. It
keeps the useful concurrency and integration rules without importing IDX
research state, model identities, market data, or sealed-outcome policies.

## Operating loop

1. Verify the absolute Git root, remote, branch, HEAD, status, worktree list,
   relevant instructions, and the current runtime state.
2. Define the acceptance criteria, protected boundaries, and validation needed
   before editing.
3. Build the execution frontier: all useful work that can start without an
   unfinished dependency.
4. Remove overlapping ownership, duplicate work, and dependent future work.
5. Choose `DIRECT`, `LIGHT`, or `HEAVY` from the width of the ready frontier.
6. Spawn independent workers before MAIN starts doing those same scopes.
7. Keep MAIN on coupling, integration, shared files, safety gates, and final
   verification.
8. Collect written handoffs and inspect the real diff, tests, and artifacts.
9. Integrate only verified results, create the local commit, and stop when the
   acceptance criteria are met.

## Execution levels

| Level | Use when | OpenShorts pattern |
|---|---|---|
| `DIRECT` | one small or inherently sequential path | MAIN works directly + focused validation |
| `LIGHT` | 2–3 independent ready paths | MAIN + 1–3 isolated workers launched early |
| `HEAVY` | 3–6 independent paths, broad migration/debugging, or material review | MAIN + 3–6 isolated workers with disjoint ownership |

A substantial `DIRECT` task must state why worker startup would not reduce
wall-clock time. De-escalate as soon as the remaining work becomes sequential.

## Safe parallel decomposition

Good OpenShorts parallel scopes include:

- backend implementation + independent regression tests;
- dashboard/API contract inspection + backend artifact verification;
- subtitle/hook/reframe implementation + real-media output inspection;
- Docker/runtime health audit + source-level fix;
- independent root-cause investigations when a local render fails.

Do not parallelize:

- overlapping edits to the same file or contract;
- two workers writing the same `output/` job or using the same mutable service;
- a dependent integration before its upstream interface is settled;
- duplicated implementation without an explicit comparison purpose.

## Worktree recipe on Windows

Use an absolute path and verify it before work:

```powershell
$repo = '<absolute-path-to-openshorts>'
$worktree = '<absolute-path-to-openshorts-worktree>'

git -C $repo rev-parse --show-toplevel
git -C $repo status --short --branch
git -C $repo worktree list
git -C $repo fetch origin
git -C $repo worktree add $worktree -b codex/<task> origin/main
```

Each writer gets a different `<task>` path and branch. Before running Docker,
assign a unique Compose project, output location, and host ports. Never mount a
shared mutable `output/` directory into two concurrent writers.

## Worker contract

Every worker prompt names the repository/worktree, base commit, task ID,
parallel group, role, one bounded question, owned files, prohibited changes,
dependencies, deliverable, validation, integration contract, handoff path, and
stopping condition. Workers do not spawn workers. Concurrent writers never
share a worktree or file ownership.

## Handoffs and shared state

Worker handoffs live in `coordination/handoffs/`. MAIN alone updates
`coordination/TEAM_STATUS.md`, `PROJECT_PROFILE.md`, `TASK_REGISTRY.md`, and
`DECISIONS.md`. The status file is a coordination ledger, not a replacement
for current code, tests, or runtime evidence.

## Commit and push boundary

MAIN creates local commits after the task is complete and validated; the user
does not have to commit manually. Push, PR creation, merge, and other remote
mutations require an explicit user request. Before a requested push, fetch and
inspect divergence, then use a normal fast-forwardable push—never force-push by
default.
