# OpenShorts fork integration notes

Status date: 2026-08-23

## Purpose

This fork is the working OpenShorts implementation for a personal Indonesian
finance/podcast clipping project. The current integration combines selectable
Gemini/OpenAI clip analysis, output-quality and durable editor changes, and the
local light-neutral dashboard palette. Upstream behavior, provider boundaries,
and publishing safeguards remain explicit rather than being silently changed.

## Dashboard palette update

The dashboard now has a palette-only light-neutral treatment on
`custom/tiktok-finance`. The existing layout, navigation, typography,
controls, animations, API calls, and video/render-specific colors were
preserved. Shared tokens in `dashboard/src/tokens.css` and
`dashboard/tailwind.config.js` now use warm off-white surfaces, graphite text,
neutral borders, semantic state colors, and a restrained burnt-orange accent.
Cards no longer use the previous radial orange glow. The initial palette pass
intentionally preserved the layout; the follow-up readability pass is recorded
below.

## Dashboard readability refinement

After the palette review, the dashboard received a small readability pass on
`custom/tiktok-finance`: the neutral surfaces are slightly darker, secondary
text and micro labels are darker and heavier, and visible controls use normal
sentence/title casing instead of forcing all text to lowercase. The generated
shorts results now use one card per row at the normal desktop breakpoint, with
more room for the preview, metadata, and edit actions. This changes presentation
only; clipping selection, subtitle rendering behavior, editing endpoints, and
the rest of the application flow are unchanged.

The palette was then softened again to avoid a flashbang-white canvas: surfaces
now use a subtle sage-slate tint, borders are less stark, and the orange accent
is slightly muted. A restrained teal is reserved for informational state colors
so the interface is not only white, gray, and orange.

During processing, the divider between `Live Analysis` and `Generated Shorts`
is adjustable on desktop. Drag the divider, or focus it and use the left/right
arrow keys. The selected split is stored locally in the browser under
`openshorts_analysis_pane_ratio`; mobile remains a stacked layout.

## Repository and branch setup

- Upstream repository: `https://github.com/mutonby/openshorts.git`
- Fork/origin: `https://github.com/samindriano/openshorts.git`
- Local path: `D:\Projects\Clip\openshorts`
- Integration starting commit: `4e5d450c15f7dfbb2f9815897f716dd25f4a3a3b`
- Canonical orchestration policy: `00494d4d826d17ad92e5bc67ba8212eef5bcb3f8`
- Working branch: `custom/tiktok-finance`
- Upstream-compatible branch: `main`

The local `main` branch remains the fork sync branch. To sync it later:

```powershell
git fetch upstream main
git switch main
git merge --ff-only upstream/main
git push origin main
git switch custom/tiktok-finance
```

Do project-specific work only on `custom/tiktok-finance`.

## Installation method

The upstream self-hosted Docker Compose workflow is:

```powershell
cd D:\Projects\Clip\openshorts
docker compose build
docker compose up -d
```

The Compose file defines `backend` (`8000`), `frontend` (`5175`), and
`renderer` (`3100`). The intended dashboard is
`http://localhost:5175`. The backend liveness endpoint is
`http://localhost:8000/health`, and the renderer liveness endpoint is
`http://localhost:3100/health`.

## Credentials

For the self-hosted Clip Generator:

- Configure at least one analysis provider in the local file
  `D:\Projects\Clip\openshorts\.env`: `GEMINI_API_KEY` and/or
  `OPENAI_API_KEY`. The dashboard can also hold browser-local keys and sends
  only the selected provider header for a job.
- `GET /api/config` exposes only the boolean fields
  `gemini_configured` and `openai_configured` in self-host mode. It never
  returns key material; hosted mode omits those self-host fields.
- Optional for clipping: no fal.ai, ElevenLabs, Upload-Post, or AWS key is
  required just to run the local dashboard, ingest/transcribe/reframe/render a
  clip, and review it locally.
- Optional feature credentials:
  - `ELEVENLABS_API_KEY` for voice dubbing.
  - `UPLOAD_POST_API_KEY` for social posting; TikTok publishing is deliberately
    not being configured in this local setup.
  - `FAL_KEY` for the separate AI Shorts/UGC actor-generation feature, not the
    core Clip Generator.
  - `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`,
    `AWS_S3_BUCKET`, and `AWS_S3_PUBLIC_BUCKET` for optional S3 backup/gallery.

The exact local key values are runtime state and must never be pasted into
chat, logs, test fixtures, documentation, or commits. Git ignores `.env`.

## Operating rules for implementation work

`AGENTS.md` at the repository root is the canonical working policy. In
particular:

- inspect branch, HEAD, status, and all worktrees before consequential work;
- use isolated worktrees for parallel writers and let only the integration
  owner merge or resolve conflicts;
- never restart or tear down another worktree's Docker stack, and use unique
  project names/ports for isolated runtime checks;
- never print, stage, commit, or copy API-key values into tracked files;
- treat provider selection as explicit: an OpenAI job must not silently call
  Gemini, and Gemini limiter protection must survive provider refactors;
- treat subtitles and titles as durable editable layers. A successful HTTP
  response is not proof of a fix: verify the current player/download/generated
  file or rendered frame;
- final reports must identify the branch/SHA, tests actually run, runtime
  evidence, limitations, and the integration/push result.

## Runtime artifacts and storage

The Compose bind mounts keep runtime data beside this checkout:

- Generated job output and clips: `output\\<job_id>\\`
- Uploaded source files: `uploads\\`
- Model/cache artifacts may be under `.cache\\`; the reframe model is stored in
  the Docker image at `/models/yolov8n.pt`, outside the `/app` source bind
  mount.

The upstream `.gitignore` excludes `.env`, `output/`, `uploads/`, `.cache/`,
`*.pt`, video extensions, and `*_metadata.json`.

## Local Library and cleanup

Self-hosted mode now exposes a `Local Library` item in the dashboard sidebar.
It lists playable clips recovered from `output\\<job_id>\\` and adds a
`Delete Files` action per generated job. The action is intentionally scoped to
one UUID job directory and the upload files beginning with that exact job ID;
it does not accept arbitrary paths, touch `output\\thumbnails\\`, or delete
files from cloud/R2 history. Because a source upload is owned by its job, it is
removed together with that job after confirmation. The clean/original clip and
all derived subtitle/edit artifacts inside the job directory are deleted as a
single reversible-in-the-UI-but-not-on-disk cleanup operation, so download
anything that must be kept first.

The local API used by the page is:

```text
GET    /api/local/history
DELETE /api/local/history/<job_id>
```

Active or resumable jobs are rejected with a conflict response. The existing
age-based `JOB_RETENTION_SECONDS` and size-cap cleanup remain available as
automatic background cleanup; the page is the manual option when disk space is
needed immediately.

## Docker storage recovery checkpoint

The previous baseline build exhausted the available `C:` headroom while
assembling the backend image. Docker Desktop's supported move was then
completed through Settings > Resources > Advanced > Disk image location.
The active Docker Desktop Linux data disk is now:

`D:\DockerData\DockerDesktopWSL\disk\docker_data.vhdx`

Immediately before the successful move, `C:` had approximately 0.07 GiB free
and `D:` had approximately 220.86 GiB free. After the move, `C:` recovered to
approximately 28.77 GiB free and the VHDX was confirmed at the D: path. After
the successful image build and startup, the latest check recorded
approximately 6.14 GiB free on `C:` and 177.92 GiB free on `D:`.

The old C: VHDX is absent, and the Docker API, WSL distribution, and Docker
Desktop status all recovered after the move. A targeted BuildKit cleanup
removed approximately 1.68 GB of reclaimable cache records from the failed
OpenShorts build. No images, volumes, containers, unrelated files, or broad
prune operation were removed.

## Repository relocation and runtime model fix

The repository was moved from
`C:\Users\Sam\OneDrive\Documents\Project\Clip\openshorts` to
`D:\Projects\Clip\openshorts` because the source checkout stores uploads and
generated output beside the application and C: had approximately 6 GiB free.
The D: checkout preserves the full Git history, the `custom/tiktok-finance`
branch, the `origin` and `upstream` remotes, and the local setup commit. The
old source path was removed after validating that the D: checkout was complete
and clean.

The original Dockerfile downloaded `yolov8n.pt` under `/app` during image
build, but Compose mounts the repository over `/app` at runtime, hiding that
file. The maintainable resolution is to download the model during the image
build to `/models/yolov8n.pt` and set the backend environment variable
`YOLO_MODEL_PATH=/models/yolov8n.pt`. The existing reframe code already honors
that variable, so no clip-selection or reframe algorithm was changed.

## Historical baseline verification

Passed before the Docker build:

- GitHub fork created and cloned from `samindriano/openshorts`, not directly
  from upstream.
- `origin` and `upstream` remotes configured as listed above.
- `main` and `upstream/main` matched at the baseline SHA.
- Working branch created as `custom/tiktok-finance`.
- Docker client `29.7.2`, Docker Compose `v5.1.1`, Git, Node, npm, and Python
  were present. Docker Desktop was started successfully initially.
- `docker compose config --quiet` passed.
- Static source inspection confirmed the declared Clip Generator pipeline:
  FFmpeg, faster-whisper, Gemini analysis, PySceneDetect/TransNetV2 scene
  detection, MediaPipe/YOLO reframing, subtitle rendering, and Remotion
  renderer service.
- Ignore-rule checks confirmed secrets and runtime artifacts are excluded.

Historical incident (resolved):

- `docker compose up --build -d` reached the final backend image assembly but
  stalled during `COPY --from=builder /opt/venv /opt/venv`. The Docker Linux
  engine then returned HTTP 500/timeouts. A targeted Docker Desktop restart
  did not restore the engine.
- Host `C:` had approximately 7.1--8.0 GB free during the build and was at
  approximately 98% usage. No unrelated files or Docker data were deleted.
- Docker Desktop is currently reporting `starting`; `docker-desktop` WSL is
  running, but the engine API still returns HTTP 500. Build-cache usage could
  not be inspected until the engine recovers.
- No OpenShorts containers were created, so backend/dashboard/renderer health,
  Clip Generator reachability, full pipeline execution, and the repository
  test suite remain unverified. The required next action is to free or provide
  substantially more Docker/host storage, then rerun the launch command.

The above records the pre-remediation state. It is superseded by the final
runtime results below. No application code or tests were changed to work
around the failure.

## Historical final baseline results

- `docker compose build` passed for backend, frontend, and renderer.
- `docker compose up -d` passed; backend, frontend, and renderer are all Up.
- `http://localhost:8000/health` returned HTTP 200 and `{"status":"ok"}`.
- `http://localhost:3100/health` returned HTTP 200 and `{"ok":true}`.
- `http://localhost:5175/` returned HTTP 200 HTML with a browser-style request.
- `GET /api/process` returned HTTP 405 with `Allow: POST`, confirming the Clip
  Generator route is reachable without starting a job.
- `GET /api/config` returned HTTP 200.
- Renderer composition test passed: 2 tests, 2 passed.
- Backend imports passed for FFmpeg bindings, faster-whisper, `google.genai`,
  PySceneDetect, TransNetV2, MediaPipe, and Ultralytics.
- FFmpeg, the Anton font, and the Remotion bundle were operational in the
  running containers. Backend Python AST parsing and `compileall` passed using
  a temporary pycache prefix, without changing the source-mounted cache.
- `YOLO_MODEL_PATH=/models/yolov8n.pt` was present in the backend; the file was
  readable by `appuser` and `YOLO('/models/yolov8n.pt')` loaded successfully as
  a detect model. This verifies the bind-mount-safe reframe model path.

Remaining limitations and reported failures:

- The repository image does not include pytest, so the Python pytest suite was
  not run; no test dependency was added.
- The upstream `npm run lint` script fails before linting because its `--ext`
  option is incompatible with the repository's flat `eslint.config.js`. Direct
  `npx eslint .` also fails because ESLint 8.57.1 cannot resolve the config's
  `eslint/config` export. Tests and source were not changed to hide this.
- The historical baseline did not have a configured Gemini key, so actual AI
  moment analysis and a full Clip Generator job were not run at that time.
- No real source video was uploaded, so Whisper inference, scene detection,
  subtitle rendering, reframe/face tracking, and final clip output were not
  end-to-end exercised. The installed/importable components and model
  readiness were verified without starting a job.

This section is retained as historical provenance for the pre-integration
baseline. The current integration validation is recorded below.

## Integration validation (2026-08-23)

- The isolated branch `integration/2026-08-23` was built from
  `custom/tiktok-finance` at `4e5d450` and merged the canonical policy,
  hybrid provider, and output-quality branches in that order. The existing
  light palette commits were already present on the starting branch.
- An isolated Compose project built backend, frontend, and renderer and passed
  `8001/health`, `3101/health`, and the dashboard entry at
  `http://localhost:5176/index.html`. The standard `8000/5175/3100` stack was
  left running and untouched.
- `/api/config` was checked for no-key, Gemini-only, OpenAI-only, both-key, and
  hosted-mode cases. Self-host returns only `gemini_configured` and
  `openai_configured` booleans; hosted mode omits them and no key value appears
  in the response.
- The real self-host UI showed both provider choices as `Configured on server`
  and Settings showed the same status without rendering key values.
- A real multipart OpenAI job reached `gpt-5.6-luna` and the two-pass scoring /
  detail path. It failed closed afterward because the bundled 41-second demo
  has zero transcript segments and returned no usable clips; it did not fall
  back to Gemini. Gemini selection, selected-key isolation, and limiter paths
  are covered by the focused tests below.
- Passed: dashboard production build; provider availability tests (4); video
  source tests (3); Remotion TypeScript build; Python compile; and 189 focused
  backend/provider/output-quality tests.
- Known tooling limitation: the repository `npm run lint` script stops before
  linting because its `--ext` flag is incompatible with the checked-in flat
  ESLint configuration. The Vite dev server's root `/` 404 is also present on
  the untouched baseline; `/index.html` is the working dev entry.

## Gemini rate-limit handling

The Clip Generator uses two transcript-analysis passes: one Gemini request per
scoring batch of eight transcript windows, followed by one detail-selection
request. A source with `N` scoring batches therefore normally makes `N + 1`
transcript-analysis requests. Optional automatic layout/content analysis adds
at most one request per enabled module; these calls use the same limiter.

Set the published project ceilings in the local `.env`:

```dotenv
GEMINI_TPM_LIMIT=250000
GEMINI_RPM_LIMIT=15
GEMINI_RATE_HEADROOM=0.90
```

The default 90% headroom makes the effective local budget approximately
225,000 tokens per rolling minute and 13 requests per rolling minute. Requests
are reserved using a conservative prompt/contents estimate, then corrected
with Gemini `usage_metadata` when the SDK provides it; no separate token-count
API call is made. Uploaded-video and sampled-image calls add a documented
conservative contents estimate because character counting cannot represent
those parts.

The limiter is shared by jobs in the same backend deployment, including the
separate `main.py` subprocesses used by the current job queue, through a small
rolling ledger at the system temporary path. Override it with
`GEMINI_RATE_LIMIT_STATE_PATH` when the deployment needs a different writable
location. A missing TPM/RPM configuration leaves proactive budgeting disabled
for upstream compatibility, but bounded transient retries remain active.

Gemini 429/resource-exhausted responses use Google `Retry-After`/`RetryInfo`
timing when present; otherwise retries use exponential backoff with jitter.
Retries are bounded by `GEMINI_MAX_RETRIES` (default `5`) and do not retry
authentication or invalid-key errors. The no-config default is two retries
(three total attempts) for upstream compatibility; the local example sets
`GEMINI_MAX_RETRIES=5`. A retry stays inside the current analysis stage, so
completed scoring batches, transcription, and source downloads are not
repeated.

## Output-quality polish (`improve/output-quality-v1`)

This pass keeps the existing clip-selection and publishing boundaries intact
and focuses on the delivered video and its editor controls.

- Source downloads may select an available AVC video stream up to 1440p when
  the paid-quality cap is not active. A 1080p source still has a hard detail
  ceiling; the pipeline cannot recover detail that is absent upstream.
- Vertical and general-layout scaling now uses Lanczos for delivery frames,
  while the low-resolution tracking analysis uses area scaling to reduce
  detector noise. The legacy OpenCV fallback also uses Lanczos.
- TRACK camera motion keeps its safe zone but filters confirmed target
  positions and eases toward a bounded speed. This removes the former abrupt
  slow/fast step changes while retaining jump confirmation and edge clamping.
  `TARGET_SMOOTHING`, `CAMERA_MAX_SPEED`, and `CAMERA_ACCELERATION` are
  environment overrides for controlled tuning.
- Auto captions are smaller and less dense by default (`36px`, up to 20
  characters, up to 1.6 seconds), with the browser preview aligned to the same
  lower, narrower treatment.
- The classic hook is a restrained warm-accent title card with smaller serif
  type, softer spacing, and a shadow that matches the Remotion preview and
  server-rendered bitmap.
- Hook saves always go through `/api/hook`, creating a durable derived file.
  Replacing or removing a hook still walks back to the clean clip and reapplies
  captions without stacking. The updated file and `auto_hook` metadata are
  returned to the result card, so preview, download, and a reopened modal use
  the same current version. Nanosecond filename IDs avoid same-second edit
  collisions during rapid successive saves.
- Subtitle “apply to this clip” now uses the same durable `/api/subtitle` path
  for every style, including the non-karaoke styles that previously could stop
  at a browser-only Remotion blob. “Apply to all” omits stale per-card
  filenames, lets the backend resolve each clip's current canonical file, and
  reports progress plus the first failed clip instead of silently swallowing
  non-2xx responses.
- The modal's `none`, `pop`, `word-highlight`, and `karaoke` animation choices
  are now sent to the backend and mapped to the matching durable ASS renderer;
  the server no longer falls back to a plain SRT burn after a styled preview.
  ASS sizing is also scaled to the 1080x1920 preview coordinate system so
  applied captions remain compact instead of rendering as oversized text.

The clean clip remains beside every derived output. Hook entrance animation is
still a browser-preview control; server-persisted hook output is static by
design. Sources below 1080p, low-bitrate uploads, and aggressive vertical
cropping remain genuine source-quality limitations rather than problems a
filter can eliminate.

Real-source validation for this pass used a 46.25-second talking-head segment
from the repository's horizontal source, rendered to 1080x1920 with the same
default v2 path before and after the changes. The baseline render was
17,175,905 bytes at 2.837 Mbps; the updated render was 17,413,408 bytes at
2.878 Mbps, with different SHA-256 hashes. Matched frames at 5s, 20s, and 35s
showed the subject staying framed while the updated crop followed the motion
with the new easing and delivery scaling. The vertical source sanity check was
byte-identical by design because it already filled the delivery canvas and had
no horizontal crop travel to exercise.
