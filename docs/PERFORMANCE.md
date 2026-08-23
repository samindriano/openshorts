# Local performance path

This document covers performance changes that preserve the existing clip-selection, reframe, subtitle, provider, and persistence behavior.

## Why this is opt-in

`docker-compose.yml` remains the compatibility/default stack. GPU acceleration lives in `docker-compose.gpu.yml` so a Docker/NVIDIA problem cannot silently change or break the normal self-host path.

The overlay changes compute engines only:

- faster-whisper: CPU int8 -> CUDA float16
- YOLO fallback detection and TransNetV2 scene detection -> CUDA
- ffmpeg video encode: x264 -> `auto` (NVENC when the runtime probe succeeds, otherwise x264)

The MediaPipe BlazeFace detector still uses its CPU TFLite delegate on this
stack; that is expected and is separate from the Whisper/YOLO/scene/encode GPU
paths.

It does **not** change:

- Gemini/OpenAI provider selection
- transcript/window prompts
- clip ranking or duration rules
- word timestamp contract
- reframe strategy
- subtitle style/edit persistence
- current output/upload directories

For Indonesian speech, keep `TRANSCRIBE_BACKEND=whisper`. The bundled Parakeet path does not support Indonesian and would fall back to Whisper after doing wasted work.

## Safe activation on Windows + Docker Desktop

Do not stop a running job. From the repository root, after pulling the performance branch:

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml build backend
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d backend
```

The frontend and renderer do not need to be rebuilt for this change.

Then verify the backend before submitting a real video:

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml exec backend python scripts/check_acceleration.py
```

Best result:

```text
Acceleration status: FULL GPU FAST PATH READY
```

`GPU ASR READY; video encode will use CPU fallback` is also safe. Transcription is still accelerated and ffmpeg continues with the existing x264 path.

If the probe says GPU ASR is not ready, return to the normal stack instead of troubleshooting inside a real clipping job.

## Rollback

The normal stack remains unchanged. Roll back by recreating the backend without the overlay:

```powershell
docker compose up -d --build backend
```

No output, upload, metadata, or `.env` deletion is part of either command.

## What to benchmark

Use the same long talking-head/podcast source before and after. Compare at least:

1. transcription wall time;
2. time from AI selection complete to all clips ready;
3. total execution time;
4. output resolution/duration;
5. one preview/download pair to make sure the current file and subtitle revision still agree.

Do not optimize clip-selection prompts or timestamp behavior based only on wall-clock speed. Those are quality-sensitive and should be changed separately with artifact-level regression evidence.
