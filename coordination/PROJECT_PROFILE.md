# OpenShorts project profile

Only `MAIN` edits this file.

## Identity

- **Project ID:** `openshorts`
- **Primary repository:** `samindriano/openshorts`
- **Authoritative branch:** `main`
- **Fork remote:** `origin`
- **Upstream remote:** `upstream`
- **Control-plane source:** `samindriano/codex-orchestra`, adapted from its
  `orchestra/idx-trade` snapshot
- **OpenShorts origin/main observed at adoption:** `e0fa3fd591bbed235027a7b683fb3e19bdb0c683`
- **Adoption date:** `2026-08-23`

This is a repository-local orchestration policy, not a live mirror of the
orchestra repository. Current OpenShorts code, tests, user instructions, and
runtime evidence win over stale control-plane text.

## Product boundary

OpenShorts is a video-processing product: ingestion, transcription, clip
selection, reframing, subtitles, hooks, rendering, dashboard controls, and
optional integrations. The default engineering mode is local/test-safe.

No orchestration task automatically authorizes credential reads, live social
publishing, production deployment, destructive media cleanup, or external
data acquisition. Those require explicit scope.

## Default topology

- **MAIN:** parent/root control plane, integration, shared coordination, final
  validation, and stop decision.
- **DIRECT:** one small or inherently sequential task.
- **LIGHT:** default meaningful task; MAIN plus 1–3 independent workers.
- **HEAVY:** 3–6 genuinely independent critical-path workers or a material
  independent review.
- **Nested workers:** prohibited.
- **Worker isolation:** separate worktree/branch, output directory, runtime
  project, and ports for concurrent writers.
- **Model routing:** user-selected; orchestration level controls delegation
  intensity, not root-model selection.

## Typical roles

`BACKEND`, `FRONTEND`, `MEDIA`, `RUNTIME`, `VALIDATION`, and `MAIN`. Ownership
must be explicit for each task; MAIN retains shared coordination and integrates
only verified handoffs.
