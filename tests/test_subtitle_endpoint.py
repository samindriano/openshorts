"""Regression tests for durable subtitle edits and version adoption.

These tests stub only the SRT/FFmpeg seams. The real ASGI endpoint still owns
the input-chain selection, metadata persistence, render identity, and response
contract that the dashboard uses to replace its player source.
"""

import asyncio
import json

import httpx
import pytest

app_module = pytest.importorskip("app")

JOB_ID = "subtitle-endpoint-test-job"

TRANSCRIPT = {
    "language": "en",
    "segments": [{
        "start": 0.0,
        "end": 10.0,
        "text": "hello world",
        "words": [
            {"word": " hello", "start": 1.0, "end": 1.5},
            {"word": " world", "start": 2.0, "end": 2.5},
        ],
    }],
}


def _request(method, path, json_body=None):
    async def _do():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver") as client:
            return await client.request(method, path, json=json_body)

    return asyncio.run(_do())


@pytest.fixture()
def job(tmp_path, monkeypatch):
    out_root = tmp_path / "output"
    job_dir = out_root / JOB_ID
    job_dir.mkdir(parents=True)

    clip = {
        "start": 0.0,
        "end": 10.0,
        "video_url": f"/videos/{JOB_ID}/clean_clip_1.mp4",
    }
    metadata = {
        "shorts": [clip],
        "transcript": TRANSCRIPT,
        "source_video": "source.mp4",
    }
    meta_path = job_dir / "test_metadata.json"
    meta_path.write_text(json.dumps(metadata), encoding="utf-8")
    (job_dir / "clean_clip_1.mp4").write_bytes(b"clean")

    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(out_root))
    monkeypatch.setattr(app_module, "_archive_clip_edit_bg", lambda *args: None)
    app_module.jobs[JOB_ID] = {
        "status": "completed",
        "logs": [],
        "result": {"clips": [dict(clip)], "cost_analysis": {}},
        "user_id": None,
        "watermark": False,
    }
    try:
        yield {"dir": job_dir, "meta_path": meta_path}
    finally:
        app_module.jobs.pop(JOB_ID, None)


@pytest.fixture()
def fake_render(monkeypatch):
    calls = {"srt": [], "burn": []}

    def fake_generate_srt(transcript, start, end, output_path):
        calls["srt"].append({"transcript": transcript, "start": start, "end": end})
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(transcript))
        return True

    def fake_burn(input_path, srt_path, output_path, **kwargs):
        calls["burn"].append({
            "input_path": input_path,
            "srt_path": srt_path,
            "kwargs": kwargs,
        })
        with open(output_path, "wb") as handle:
            handle.write(b"rendered")

    monkeypatch.setattr(app_module, "generate_srt", fake_generate_srt)
    monkeypatch.setattr(app_module, "burn_subtitles", fake_burn)
    return calls


def _style_body(input_filename=None, words=None, font_color="#FF0000"):
    body = {
        "job_id": JOB_ID,
        "clip_index": 0,
        "position": "middle",
        "font_size": 28,
        "font_name": "Arial",
        "font_color": font_color,
        "border_color": "#000000",
        "border_width": 1,
        "bg_color": "#000000",
        "bg_opacity": 0.0,
        "style": "classic",
        "animation": "none",
        "highlight_color": "#FFD700",
        "effect": "none",
        "base_opacity": 1.0,
        "uppercase": False,
    }
    if input_filename:
        body["input_filename"] = input_filename
    if words is not None:
        body["words"] = words
    return body


def test_apply_twice_uses_clean_source_and_persists_edited_caption(job, fake_render):
    edited_words = [
        {"text": "SUBTITLE_TEST_123", "startMs": 1000, "endMs": 1500},
        {"text": "world", "startMs": 2000, "endMs": 2500},
    ]
    first = _request("POST", "/api/subtitle", _style_body(words=edited_words))
    assert first.status_code == 200, first.text
    first_data = first.json()
    assert first_data["server_file"].startswith("subtitled_")
    assert first_data["server_file"].endswith("_clean_clip_1.mp4")
    assert first_data["revision"].isdigit()
    assert first_data["subtitle_config"]["captions"] == edited_words
    assert "SUBTITLE_TEST_123" in fake_render["srt"][0]["transcript"]["segments"][0]["text"]
    assert fake_render["burn"][0]["kwargs"]["font_color"] == "#FF0000"

    second = _request("POST", "/api/subtitle", _style_body(
        input_filename=first_data["server_file"], font_color="#00FF00",
        words=edited_words,
    ))
    assert second.status_code == 200, second.text
    second_data = second.json()
    assert second_data["server_file"] != first_data["server_file"]
    assert second_data["server_file"].endswith("_clean_clip_1.mp4")
    assert second_data["subtitle_config"]["captions"] == edited_words

    # Both burns must read the pristine clip, never the previous burned file.
    assert [call["input_path"] for call in fake_render["burn"]] == [
        str(job["dir"] / "clean_clip_1.mp4"),
        str(job["dir"] / "clean_clip_1.mp4"),
    ]
    metadata = json.loads(job["meta_path"].read_text(encoding="utf-8"))
    assert metadata["shorts"][0]["video_url"] == second_data["new_video_url"]
    assert metadata["shorts"][0]["render_revision"] == second_data["revision"]
    assert metadata["shorts"][0]["subtitle_config"]["fontColor"] == "#00FF00"
    assert app_module.jobs[JOB_ID]["result"]["clips"][0]["video_url"] == second_data["new_video_url"]


def test_remove_returns_clean_file_and_clears_recipe(job, fake_render):
    rendered = _request("POST", "/api/subtitle", _style_body())
    assert rendered.status_code == 200, rendered.text
    rendered_file = rendered.json()["server_file"]

    removed = _request("POST", "/api/subtitle/remove", {
        "job_id": JOB_ID,
        "clip_index": 0,
        "input_filename": rendered_file,
    })
    assert removed.status_code == 200, removed.text
    removed_data = removed.json()
    assert removed_data["server_file"] == "clean_clip_1.mp4"
    assert removed_data["subtitle_config"] is None
    assert removed_data["revision"].startswith("remove-")

    metadata = json.loads(job["meta_path"].read_text(encoding="utf-8"))
    assert metadata["shorts"][0]["video_url"] == removed_data["new_video_url"]
    assert metadata["shorts"][0]["subtitle_config"] is None
    assert app_module.jobs[JOB_ID]["result"]["clips"][0]["video_url"] == removed_data["new_video_url"]
