# OpenShorts orchestra status

Only `MAIN` may edit this file.

- **Phase:** `ORCHESTRA_ADOPTION`
- **Operating mode:** `LOCAL_TEST_SAFE_PRODUCT_ENGINEERING`
- **Primary repository:** `samindriano/openshorts`
- **Authoritative branch:** `main`
- **Control-plane source:** `samindriano/codex-orchestra` + adapted IDX Trade
  orchestration policy
- **Active task:** `OPENS-ORCH-001`
- **Task state:** `IN_PROGRESS_UNTIL_COMMITTED`
- **Execution policy:** `DIRECT/LIGHT/HEAVY` chosen from the ready execution
  frontier; no artificial parallelism
- **Worker safety:** isolated worktrees/branches, disjoint file ownership,
  unique runtime outputs and ports
- **Integration owner:** `MAIN`
- **Shared-file owner:** `MAIN`
- **Commit policy:** MAIN creates validated local commits
- **Push policy:** explicit user request required; no force-push by default
- **Next milestone:** commit and verify the orchestration files, then use the
  policy for future OpenShorts tasks

## Current task boundary

`OPENS-ORCH-001` ports orchestration topology and safety rules only. It does
not copy IDX model state, market data, credentials, generated artifacts, or
domain-specific research gates into OpenShorts.
