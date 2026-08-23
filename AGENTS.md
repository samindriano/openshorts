# OpenShorts repository-wide Codex orchestration policy

This root instruction file defines the default working policy for the entire `samindriano/openshorts` repository.

The purpose is to make Codex work consistently across chats, branches, and worktrees while preserving the actual OpenShorts runtime, the user's local customizations, and the integrity of generated media outputs.

Orchestration changes **how work is executed**, not what product changes are authorized.

## 1. Authority and project truth

The following sources control project state, in this order:

1. the user's latest explicit request;
2. the actual current local worktree/branch being modified;
3. the current source code and tests on that branch;
4. `docs/CUSTOMIZATION.md` and other branch-local implementation notes;
5. committed Git history and GitHub branch/PR state;
6. older chat summaries or assumptions only as background.

Never overwrite current local behavior because an older prompt, upstream file, public GitHub branch, or stale chat says something different.

The user's fork contains local fixes and experiments that may not exist on public upstream. **Inspect the actual target branch before coding.**

Preserve unrelated user changes.

## 2. Mandatory preflight before material work

Before starting, continuing, or proposing any non-trivial implementation task, Codex must inspect the execution context first.

At minimum:

```powershell
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short
git worktree list
```

Then determine:

- exact repository root;
- exact branch and HEAD;
- whether the worktree is clean;
- whether another worktree already owns the same scope;
- whether the current task depends on uncommitted work elsewhere;
- whether the running Docker stack belongs to this worktree or another one.

Stop and report instead of guessing when:

- the branch is not the branch requested;
- the worktree contains unrelated uncommitted edits that would be overwritten;
- the expected base commit is missing;
- the requested scope is already actively being implemented in another worktree;
- a destructive command could affect another active stack or worktree.

Do not silently switch branches, reset changes, clean files, or rewrite history.

## 3. Worktree-first isolation

Meaningful parallel work should use separate branches/worktrees.

Typical scopes include:

- frontend/dashboard redesign;
- Gemini rate-limit handling;
- hybrid Gemini/OpenAI provider support;
- subtitle/editor correctness;
- output-quality/reframe improvements;
- publishing integrations;
- independent audits or regression reviews.

Rules:

- one material concern per branch when practical;
- concurrent writers must use separate worktrees or provably disjoint files;
- do not edit another task's worktree;
- do not merge, cherry-pick, rebase, or delete another worktree's branch unless the user explicitly authorizes integration;
- do not force-push;
- do not use `git reset --hard`, `git clean -fd`, or equivalent destructive commands on a user worktree without explicit authorization.

When creating a new worktree, base it on the exact intended committed state, not on whichever branch happens to be checked out.

## 4. Parallel-first execution

For every non-trivial task, perform a short parallelism preflight.

Identify:

1. the current execution frontier;
2. which scopes are independent;
3. which scope owns integration or architecture;
4. which work must remain sequential because later decisions depend on earlier evidence.

Execution levels:

- **DIRECT** — small, local, or inherently sequential work;
- **LIGHT** — default for meaningful tasks with roughly 2–3 independent scopes;
- **HEAVY** — broad debugging/migration work with several independent critical-path scopes or when independent review is decision-changing.

Do not spawn workers just because capacity exists.

Workers never spawn nested workers.

Do not duplicate implementation unless the purpose is explicitly independent/adversarial review.

## 5. Investigation before implementation

Do not patch from symptoms alone.

For bugs, first trace the actual runtime path and identify the source of truth.

Examples:

- frontend state -> request -> backend endpoint -> generated file -> metadata -> preview -> download;
- source video -> transcription -> transcript windows -> AI provider -> clip selection -> reframe -> subtitles -> render;
- job submission -> environment -> subprocess -> provider-specific client -> result metadata.

Before coding, answer:

- what actually happens now?
- where does the first incorrect state appear?
- which file/object is authoritative?
- is the failure backend, frontend, cache, persistence, render, or orchestration?
- can the bug be reproduced with an existing completed job instead of starting expensive work again?

Prefer a minimal root-cause fix over layering another workaround on top.

## 6. Evidence standard: do not call something fixed just because code ran

A successful HTTP response, build, or unit test is not enough when the reported bug is visual, stateful, or end-to-end.

For media/editor bugs, verify the actual artifact.

Examples:

- inspect the generated filename and metadata;
- compare file hashes, mtimes, sizes, or ffprobe output when useful;
- extract frames before/after for visual changes;
- verify the browser is playing the newly generated file rather than a stale URL;
- verify preview and downloaded MP4 represent the same version;
- refresh/reopen the project when persistence is part of the contract.

If the user reports "the video still looks unchanged", treat that observation as unresolved until the exact displayed artifact is proven.

Do not close a bug with only `200 OK` or `tests passed`.

## 7. Media pipeline invariants

Unless a task explicitly changes these semantics, preserve the pipeline:

```text
source
-> local transcription
-> transcript/window analysis
-> AI clip selection
-> timestamp snapping
-> cut/reframe
-> subtitle/title/effects composition
-> final render
-> preview/download
```

Do not casually change clip-selection prompts, duration rules, ranking semantics, provider, reframe strategy, or caption behavior as collateral damage.

Generated media edits should be non-destructive.

The desired edit model is conceptually:

```text
clean current clip/cut
+ subtitle layer/config
+ title/text overlay layer/config
+ effects
= current rendered revision
```

Repeated edits must not stack old burned content on top of newer content.

Always preserve a recoverable clean/base version for the current cut/reframe when feasible.

## 8. Subtitle and title/text editing rules

Subtitle/title editing is a product surface, not a one-shot generation step.

When editing is in scope:

- text must remain editable after clip generation;
- font, size, color, position, outline/background, and supported animation settings should persist when applicable;
- reopening the editor should reflect the current saved configuration rather than resetting to defaults;
- applying a new style must replace the prior style, not stack it;
- preview, persistence, and download must agree on the same current revision.

The AI-generated hook text is only an initial suggestion. After generation it should be treated as a normal editable **Title / Text Overlay**, not as immutable AI output.

Do not silently regenerate clip selection merely because the user edits subtitles or title text.

## 9. AI provider rules

The project may support more than one AI provider.

Provider selection must be explicit per job when the UI supports it.

Current intended providers include:

- Gemini;
- official OpenAI API.

Rules:

- never silently use Gemini during an OpenAI-selected job;
- never silently use OpenAI during a Gemini-selected job;
- do not use Sub2API or unofficial subscription gateways unless explicitly requested;
- provider-specific retries/limits must remain provider-specific;
- Gemini requests must preserve the shared rate-limit protection when present;
- result metadata must record the actual provider/model used;
- a provider switch must not alter downstream clip schema or render semantics unless intentionally designed.

For A/B tests, use the same transcript/windows/prompts/schema when the goal is comparing provider quality.

## 10. API cost and quota discipline

Use mocks for automated tests whenever possible.

A real paid/free-tier API call is allowed only when it materially proves integration or output quality.

When a real smoke test is needed:

- reuse an existing transcript/job where possible;
- avoid re-downloading video;
- avoid re-running Whisper;
- avoid rendering all clips if one minimal artifact proves downstream compatibility;
- do not run repeated paid calls merely to gather logs;
- never expose API keys in output.

Do not assume a provider is "cheap enough" and burn quota unnecessarily.

## 11. Secrets and `.env`

`.env` is local runtime state and must stay untracked.

Never:

- print API-key values;
- commit `.env`;
- copy real secrets into `.env.example`, docs, fixtures, logs, metadata, or test snapshots;
- include secrets in GitHub issues/PRs/commits.

Presence checks are allowed, for example:

```text
OPENAI_API_KEY present: yes
GEMINI_API_KEY present: yes
```

A separate worktree does not automatically inherit an ignored `.env`. If runtime verification needs credentials, copy the local `.env` into the isolated worktree only after confirming it is ignored.

Treat the main local `.env` as read-only credential source unless the user explicitly asks to change it.

## 12. Docker/runtime isolation

A worktree must not casually control another worktree's running containers.

Before `docker compose up`, `down`, `build`, `restart`, or `rm`, inspect which project/containers/ports are active.

The user's baseline stack may already occupy the standard ports.

When testing an isolated branch:

- prefer a unique Compose project name;
- use alternate host ports when needed;
- do not `docker compose down` the baseline stack;
- do not recreate shared containers unless explicitly authorized;
- do not interrupt a running user job just to verify another branch.

If isolation is not possible, report the collision before changing runtime state.

## 13. Testing ladder

Validation should scale with the change.

Typical ladder:

1. syntax/static checks for changed files;
2. focused unit tests;
3. focused endpoint/component tests;
4. broader relevant regression tests;
5. frontend/backend build where applicable;
6. isolated runtime health check;
7. minimal real end-to-end smoke test only when needed.

Use the strongest existing tests in the repository rather than inventing a fake green signal.

Do not hide known upstream failures. If a test/tool is unavailable or already broken upstream, report that explicitly.

For render/editor changes, add an artifact-level acceptance check in addition to unit tests.

## 14. Frontend/backend source-of-truth discipline

Avoid multiple competing representations of the same current clip state.

When a user action creates a new authoritative video revision, audit all of:

- backend returned URL/file;
- in-memory job result;
- metadata on disk;
- frontend `currentVideoUrl` or equivalent;
- server-side current filename/revision;
- durable/archive copy;
- browser cache behavior;
- downloaded file.

An older durable/archive URL must never override a freshly rendered local revision merely because it loads faster.

Prefer explicit revision/version identity over timestamp-only naming when collisions are possible.

## 15. Upstream vs fork discipline

This repository is a customized fork.

Never assume upstream `mutonby/openshorts` is authoritative for current behavior.

When consulting upstream:

- treat it as reference material;
- compare diffs before porting changes;
- preserve local fixes;
- do not replace local files wholesale when a narrow port is sufficient.

Do not remove project-specific functionality simply to make the fork look more like upstream.

## 16. Scope control

Stay inside the requested task.

Do not opportunistically combine unrelated work such as:

- frontend palette redesign;
- provider migration;
- rate-limit handling;
- subtitle architecture;
- reframe smoothing;
- publishing integrations;
- analytics.

If another issue is discovered, document it and leave it for a separate branch unless it blocks the current task.

When the user asks for an **audit/diagnosis**, do not automatically turn it into a broad remediation unless requested.

## 17. Git discipline

Before committing:

```powershell
git status --short
git diff --check
git diff --stat
git diff
```

Verify that only intended files changed.

Use focused commit messages, for example:

```text
fix: keep fresh subtitle render authoritative
feat: add per-job AI provider selection
improve: smooth reframe motion
style: replace dark dashboard palette
```

Do not include unrelated formatting churn.

Do not merge/cherry-pick across task branches automatically.

Push only the branch being worked on unless the user explicitly requests another ref update.

## 18. Documentation

For material behavior changes, update `docs/CUSTOMIZATION.md` when it exists and the change affects local customization, runtime configuration, architecture, or known limitations.

Documentation must describe what is actually implemented and verified, not intended future behavior.

Do not claim real API/E2E validation if only mocks were used.

## 19. Reporting contract

At the end of a material task, report concisely:

- root cause or implementation goal;
- files changed;
- architecture/behavior changed;
- tests run and exact result;
- real smoke/E2E evidence, if any;
- known limitations or unverified paths;
- branch;
- final commit SHA;
- whether it was pushed;
- exact integration command if the user will integrate later.

For a bug fix, explicitly state what evidence proves the original failure is gone.

Do not say "fixed" when only implementation is complete but the original user-visible behavior has not been reproduced successfully.

## 20. Stop conditions

Stop and ask/report instead of proceeding when:

- an operation risks deleting user data;
- a change would overwrite another worktree's uncommitted edits;
- a test requires exposing a secret;
- the only verification path would consume significant API quota without user need;
- branch/HEAD assumptions do not match reality;
- the running stack belongs to another active task and cannot be isolated;
- an integration would require a merge/cherry-pick the user has not authorized;
- evidence contradicts the proposed root cause.

Fail closed on uncertain destructive actions; continue independently on safe inspection and non-destructive validation.

## 21. Default working style for this repository

The default sequence is:

```text
inspect current branch/worktree
-> trace actual behavior
-> identify root cause / exact goal
-> isolate scope
-> implement minimally
-> run focused tests
-> run broader relevant regression
-> verify real artifact/runtime when necessary
-> document
-> commit
-> push the task branch when requested
-> report evidence and integration command
```

The standard is not "code compiles".

The standard is: **the requested OpenShorts behavior works in the actual pipeline without regressing adjacent functionality or interfering with another active worktree.**
