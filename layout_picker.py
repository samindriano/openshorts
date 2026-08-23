"""Ask the selected clip-analysis AI which layout a video needs, once per video.

The picker samples frames instead of uploading the full source. That keeps the
visual routing call small for hour-long podcasts and lets both Gemini and OpenAI
use the same evidence. AUTO_LAYOUT remains opt-in and a failure always degrades
to the existing default layout rather than breaking a clip job.
"""
import json
import os

from ai_provider import normalize_provider, spec_for

# AUTO_LAYOUT=1 decides and applies. AUTO_LAYOUT=shadow decides, logs, and
# applies NOTHING: the render is byte-for-byte what it would have been.
_MODE = os.environ.get("AUTO_LAYOUT", "0").strip().lower()
SHADOW = _MODE == "shadow"
ENABLED = _MODE == "1" or SHADOW

# 12 frames at 1024px wide. Both values come from the upstream layout eval.
SAMPLE_FRAMES = int(os.environ.get("LAYOUT_SAMPLE_FRAMES", "12"))
SAMPLE_WIDTH = int(os.environ.get("LAYOUT_SAMPLE_WIDTH", "1024"))

DECISION_FLAGS = {
    "none": [],
    "screencast": ["screencast_layout"],
    "split": ["split_layout", "active_speaker"],
}
VALID = set(DECISION_FLAGS)


def _module_flags(decision):
    return DECISION_FLAGS.get(str(decision or "none").strip().lower(), [])


def apply(decision):
    """Switch on the modules a decision needs without undoing manual choices."""
    import active_speaker
    import screencast_layout
    import split_layout

    modules = {
        "split_layout": split_layout,
        "screencast_layout": screencast_layout,
        "active_speaker": active_speaker,
    }
    touched = []
    for name in _module_flags(decision):
        module = modules.get(name)
        if module is not None and not getattr(module, "ENABLED", False):
            module.ENABLED = True
            touched.append(name)
    return touched


def sample_frames(video_path, n=None, width=None):
    """JPEG bytes for ``n`` frames spread evenly across the video."""
    import cv2

    n = n or SAMPLE_FRAMES
    width = width or SAMPLE_WIDTH
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out = []
    try:
        if total <= 0:
            return out
        for i in range(n):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(i * total / n))
            ok, frame = cap.read()
            if not ok:
                continue
            h, w = frame.shape[:2]
            scaled = cv2.resize(
                frame, (width, max(2, int(h * width / w))),
                interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(
                ".jpg", scaled, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                out.append(buf.tobytes())
    finally:
        cap.release()
    return out


def _pick_gemini(api_key, model_name, frames):
    from google import genai
    from google.genai import types as genai_types
    import gemini_worker

    client = genai.Client(api_key=api_key)
    parts = [genai_types.Part.from_bytes(data=b, mime_type="image/jpeg")
             for b in frames]
    response = client.models.generate_content(
        model=model_name,
        contents=parts + [gemini_worker.LAYOUT_CHOICE_PROMPT],
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=gemini_worker.LayoutChoice,
        ))
    gemini_worker.raise_if_blocked(response)
    parsed_obj = getattr(response, "parsed", None)
    if parsed_obj is not None:
        return parsed_obj.model_dump() if hasattr(parsed_obj, "model_dump") else parsed_obj
    return json.loads(response.text) or {}


def _pick_openai(api_key, model_name, frames):
    import gemini_worker
    import openai_worker

    answer, _cost = openai_worker.run_image_stage(
        api_key,
        model_name,
        gemini_worker.LAYOUT_CHOICE_PROMPT,
        frames,
        gemini_worker.LayoutChoice,
        base_url=os.getenv("OPENAI_BASE_URL"),
        reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT", "none"),
    )
    return answer


def pick(video_path, video_duration):
    """The selected provider's layout decision, or ``none`` on any failure."""
    if not ENABLED:
        return "none"

    try:
        provider = normalize_provider(os.getenv("AI_PROVIDER") or "gemini")
    except ValueError as exc:
        print(f"   ⚠️ {exc} — keeping the default layout.")
        return "none"
    spec = spec_for(provider)
    api_key = os.getenv(spec.key_env)
    if not api_key:
        return "none"
    model_name = os.getenv(spec.model_env) or spec.default_model

    print(f"🎛️  Choosing a layout with {spec.label}/{model_name}…")
    try:
        frames = sample_frames(video_path)
        if not frames:
            print("   ⚠️ No readable frames — keeping the default layout.")
            return "none"
        if provider == "openai":
            answer = _pick_openai(api_key, model_name, frames)
        else:
            answer = _pick_gemini(api_key, model_name, frames)
    except Exception as exc:
        print(f"   ⚠️ Layout choice failed ({exc}) — keeping the default layout.")
        return "none"

    decision = str(answer.get("layout", "none")).strip().lower()
    if decision not in VALID:
        print(f"   ⚠️ Unknown layout '{decision}' — keeping the default layout.")
        return "none"

    why = str(answer.get("why", ""))[:80]
    confidence = answer.get("confidence")
    print(f"   🎬 Layout: {decision} (confidence {confidence}) — {why}")
    return decision


def pick_and_apply(video_path, video_duration):
    """Decide, switch on (unless shadowing), report what changed."""
    decision = pick(video_path, video_duration)

    if SHADOW:
        would = _module_flags(decision)
        print(f"[layout-shadow] decision={decision} "
              f"would_enable={','.join(would) if would else 'none'} "
              f"duration={video_duration:.0f}s")
        return decision

    touched = apply(decision)
    if touched:
        print(f"   ✅ Enabled: {', '.join(touched)}")
    return decision
