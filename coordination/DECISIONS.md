# OpenShorts orchestration decisions

Only `MAIN` edits this file.

## 2026-08-23 — Adopt isolated, parallel-first control plane

- **Task:** `OPENS-ORCH-001`
- **Decision:** Adopt the reusable orchestration topology from
  `samindriano/codex-orchestra` / `orchestra/idx-trade`: MAIN control plane,
  execution frontier preflight, DIRECT/LIGHT/HEAVY levels, bounded workers,
  explicit ownership, isolated worktrees, written handoffs, and verified
  integration.
- **Adaptation:** Replace IDX research/model/sealed-outcome rules with
  OpenShorts media, backend, frontend, runtime, and artifact safety boundaries.
- **Explicitly excluded:** IDX market data, model hashes, research decisions,
  credentials, generated artifacts, and any automatic publish/deploy authority.
- **Commit/push:** MAIN may create validated local commits; remote push remains
  a separate action requiring explicit user authorization.
- **Rationale:** Prevent concurrent worktree/output/port collisions while
  making parallel work understandable and recoverable.
