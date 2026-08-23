# OpenShorts fork baseline

Status date: 2026-08-23

## Purpose

This fork is the vanilla OpenShorts baseline for a personal Indonesian
finance/podcast clipping project. Custom clip-selection logic, prompts, UI
redesign, n8n, TikTok auto-publishing, and affiliate functionality are out of
scope until the baseline is tested manually.

## Dashboard palette update

The dashboard now has a palette-only light-neutral treatment on
`custom/tiktok-finance`. The existing layout, navigation, typography,
controls, animations, API calls, and video/render-specific colors were
preserved. Shared tokens in `dashboard/src/tokens.css` and
`dashboard/tailwind.config.js` now use warm off-white surfaces, graphite text,
neutral borders, semantic state colors, and a restrained burnt-orange accent.
Cards no longer use the previous radial orange glow. This is intentionally a
visual review checkpoint, not a second layout redesign.

## Repository and branch setup

- Upstream repository: `https://github.com/mutonby/openshorts.git`
- Fork/origin: `https://github.com/samindriano/openshorts.git`
- Local path: `D:\Projects\Clip\openshorts`
- Baseline commit: `e0fa3fd591bbed235027a7b683fb3e19bdb0c683`
- Baseline setup commit: `72ff6276b9fdac8192032f7dcbd293df8b6e45ab`
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

For the self-hosted Clip Generator baseline:

- Required: `GEMINI_API_KEY`, stored in the local file
  `D:\Projects\Clip\openshorts\.env`. The exact line is
  `GEMINI_API_KEY=`. Fill the value locally before the first real clipping
  run; never paste it into chat or commit `.env`.
- Optional for clipping: no fal.ai, ElevenLabs, Upload-Post, or AWS key is
  required just to run the local dashboard, ingest/transcribe/reframe/render a
  clip, and review it locally.
- Optional feature credentials:
  - `ELEVENLABS_API_KEY` for voice dubbing.
  - `UPLOAD_POST_API_KEY` for social posting; TikTok publishing is deliberately
    not being configured in this baseline.
  - `FAL_KEY` for the separate AI Shorts/UGC actor-generation feature, not the
    core Clip Generator.
  - `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`,
    `AWS_S3_BUCKET`, and `AWS_S3_PUBLIC_BUCKET` for optional S3 backup/gallery.

No API keys were supplied, created, or printed. The local `.env` was created
from the documented example with optional cloud values left commented out and
`GEMINI_API_KEY=` left empty. Git ignores this file.

## Runtime artifacts and storage

The Compose bind mounts keep runtime data beside this checkout:

- Generated job output and clips: `output\\<job_id>\\`
- Uploaded source files: `uploads\\`
- Model/cache artifacts may be under `.cache\\`; the reframe model is stored in
  the Docker image at `/models/yolov8n.pt`, outside the `/app` source bind
  mount.

The upstream `.gitignore` excludes `.env`, `output/`, `uploads/`, `.cache/`,
`*.pt`, video extensions, and `*_metadata.json`.

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

## Baseline verification

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

## Final baseline results

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
- `GEMINI_API_KEY` is not configured. Gemini SDK import/readiness is present,
  but actual AI moment analysis and a full Clip Generator job were not run.
- No real source video was uploaded, so Whisper inference, scene detection,
  subtitle rendering, reframe/face tracking, and final clip output were not
  end-to-end exercised. The installed/importable components and model
  readiness were verified without starting a job.

This is a reproducible vanilla runtime baseline for manual testing with the
credential/model limitations above. No prompts, UI, n8n, TikTok, or affiliate
functionality was changed.
