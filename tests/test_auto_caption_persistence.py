"""Regression tests for the initial automatic-caption artifact contract."""

import json

import pytest

main = pytest.importorskip("main")


TRANSCRIPT = {
    "language": "id",
    "segments": [{
        "start": 0.0,
        "end": 2.0,
        "words": [
            {"word": " halo", "start": 0.1, "end": 0.5},
            {"word": " dunia", "start": 0.6, "end": 1.0},
        ],
    }],
}


def test_auto_caption_returns_structured_recipe_and_exact_file(tmp_path, monkeypatch):
    source = tmp_path / "clean_clip.mp4"
    source.write_bytes(b"video")
    generated = {}

    def fake_ass(transcript, start, end, path, **kwargs):
        generated["ass"] = {"transcript": transcript, "kwargs": kwargs}
        open(path, "w", encoding="utf-8").write("ass")
        return True

    def fake_burn(input_path, subtitle_path, output_path, **kwargs):
        generated["burn"] = {"input": input_path, "subtitle": subtitle_path,
                              "kwargs": kwargs}
        open(output_path, "wb").write(b"captioned")

    monkeypatch.setattr(main.time, "time_ns", lambda: 123456789)
    monkeypatch.setattr(main, "uuid", type("U", (), {
        "uuid4": staticmethod(lambda: type("X", (), {"hex": "abcdef123456"})()),
    }))
    monkeypatch.setattr("subtitles.generate_ass", fake_ass)
    monkeypatch.setattr("subtitles.burn_subtitles", fake_burn)

    result = main.auto_caption_clip(str(source), TRANSCRIPT, 0.0, 2.0)

    assert result["path"].endswith("subtitled_123456789_clean_clip.mp4")
    assert result["server_file"] == "subtitled_123456789_clean_clip.mp4"
    assert result["revision"] == "123456789"
    assert result["subtitle_config"]["fontName"] == "Anton"
    assert result["subtitle_config"]["style"] == "karaoke"
    assert generated["burn"]["input"] == str(source)
    assert generated["ass"]["kwargs"]["effect"] == "pop"


def test_canonical_config_migrates_legacy_snake_case_without_hidden_ui_rewrite():
    from subtitles import canonical_subtitle_config

    config = canonical_subtitle_config({
        "font_size": 24,
        "font_name": "Arial",
        "style": "classic",
        "animation": "none",
        "highlight_color": "#00ff00",
    })
    assert config == {
        "position": "bottom",
        "fontSize": 24,
        "fontName": "Arial",
        "fontColor": "#FFFFFF",
        "highlightColor": "#00FF00",
        "borderColor": "#000000",
        "borderWidth": 3,
        "bgColor": "#000000",
        "bgOpacity": 0.0,
        "style": "classic",
        "animation": "none",
        "effect": "none",
        "baseOpacity": 1.0,
        "uppercase": False,
    }
