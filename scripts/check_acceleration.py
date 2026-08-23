"""Quick, non-destructive acceleration probe for the self-host backend.

Run inside the GPU Compose backend before a real job:

    python scripts/check_acceleration.py

The probe does not load Whisper weights or touch outputs/uploads. It checks the
three things the fast path depends on: NVIDIA runtime visibility, CTranslate2
CUDA support for faster-whisper, and ffmpeg NVENC availability.
"""
from __future__ import annotations

import subprocess
import sys


def _nvidia_runtime_visible() -> bool:
    try:
        result = subprocess.run(
            ["nvidia-smi", "-L"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            print(f"GPU runtime: OK ({result.stdout.strip().splitlines()[0]})")
            return True
    except Exception:
        pass
    print("GPU runtime: NOT VISIBLE")
    return False


def _ctranslate2_cuda() -> bool:
    try:
        import ctranslate2

        compute_types = sorted(ctranslate2.get_supported_compute_types("cuda"))
        ok = bool(compute_types)
        print(
            "faster-whisper CUDA: "
            + (f"OK ({', '.join(compute_types)})" if ok else "UNAVAILABLE")
        )
        return ok
    except Exception as exc:
        print(f"faster-whisper CUDA: UNAVAILABLE ({type(exc).__name__}: {exc})")
        return False


def _nvenc() -> bool:
    try:
        from ffmpeg_utils import nvenc_available

        ok = bool(nvenc_available())
        print(f"FFmpeg NVENC: {'OK' if ok else 'UNAVAILABLE; x264 fallback will be used'}")
        return ok
    except Exception as exc:
        print(f"FFmpeg NVENC: UNAVAILABLE ({type(exc).__name__}: {exc})")
        return False


def main() -> int:
    runtime_ok = _nvidia_runtime_visible()
    asr_ok = _ctranslate2_cuda()
    nvenc_ok = _nvenc()

    print()
    if runtime_ok and asr_ok and nvenc_ok:
        print("Acceleration status: FULL GPU FAST PATH READY")
        return 0
    if runtime_ok and asr_ok:
        print("Acceleration status: GPU ASR READY; video encode will use CPU fallback")
        return 0

    print("Acceleration status: GPU ASR NOT READY; keep the normal Compose stack")
    return 2


if __name__ == "__main__":
    sys.exit(main())
