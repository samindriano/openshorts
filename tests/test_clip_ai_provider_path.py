"""Production selector path tests without making provider network calls."""

import pytest

try:
    import clip_ai
except ImportError as exc:
    pytest.skip(f"provider SDK dependencies unavailable: {exc}", allow_module_level=True)


def test_openai_uses_same_score_detail_pipeline_and_metadata(monkeypatch):
    calls = []

    class FakeRunner:
        provider = "openai"
        model_name = "gpt-5.6-luna"

        class Spec:
            label = "OpenAI"

        spec = Spec()

        def __init__(self, provider):
            assert provider == "openai"

        def run(self, prompt, schema, label):
            calls.append((schema.__name__, label, prompt))
            if schema.__name__ == "ScoreResponse":
                parsed = {"windows": [{"id": "w0", "score": 95}]}
            else:
                parsed = {"shorts": [{
                    "start": 20.0,
                    "end": 50.0,
                    "video_description_for_tiktok": "follow",
                    "video_description_for_instagram": "follow",
                    "video_title_for_youtube_short": "title",
                }]}
            return parsed, {
                "provider": "openai",
                "model": "gpt-5.6-luna",
                "input_tokens": 10,
                "cached_input_tokens": 2,
                "output_tokens": 4,
                "reasoning_tokens": 0,
                "total_cost": 0.00001,
            }

    monkeypatch.setattr(clip_ai, "_ProviderRunner", FakeRunner)
    monkeypatch.setattr(clip_ai, "clip_duration_bounds", lambda: (15.0, 60.0))
    monkeypatch.setattr(clip_ai, "clip_count_targets", lambda _n: (1, 3))
    monkeypatch.setattr(clip_ai, "build_transcript_windows", lambda *_args, **_kwargs: [{
        "id": "w0", "start": 0.0, "end": 60.0, "text": "hello world",
    }])
    monkeypatch.setattr(clip_ai, "snap_clip_to_words", lambda start, end, *_args, **_kwargs: (start, end))

    result = clip_ai.get_viral_clips(
        {"language": "en", "segments": [{"words": [
            {"word": "hello", "start": 20.0, "end": 20.5},
            {"word": "world", "start": 49.0, "end": 49.5},
        ]}]},
        60.0,
        provider="openai",
    )

    assert [name for name, _label, _prompt in calls] == ["ScoreResponse", "DetailResponse"]
    assert result["ai_provider"] == "openai"
    assert result["ai_model"] == "gpt-5.6-luna"
    assert result["cost_analysis"]["provider"] == "openai"
    assert result["cost_analysis"]["input_tokens"] == 20


def test_openai_silent_video_fails_without_calling_gemini(monkeypatch):
    try:
        import main
    except ImportError as exc:
        pytest.skip(f"main dependencies unavailable: {exc}")

    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setattr(main.genai, "Client", lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("Gemini must not be called for OpenAI silent-video selection")))

    try:
        main.get_visual_clips("silent.mp4", 60.0, provider="openai")
    except RuntimeError as exc:
        assert str(exc) == (
            "OpenAI provider currently requires a transcript for clip selection. "
            "Choose Gemini for silent-video visual analysis."
        )
    else:
        raise AssertionError("OpenAI silent-video selection should fail clearly")
