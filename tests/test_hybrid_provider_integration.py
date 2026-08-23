"""Focused API/provider contract tests for hybrid clip generation."""

import asyncio
import json

import httpx
import pytest

app_module = pytest.importorskip("app")


def _request(headers=None):
    return httpx.Request("POST", "http://testserver/api/process", headers=headers or {})


def _post_json(payload, headers=None):
    async def _do():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/api/process", json=payload, headers=headers or {})
    return asyncio.run(_do())


@pytest.fixture()
def process_dirs(tmp_path, monkeypatch):
    output = tmp_path / "output"
    uploads = tmp_path / "uploads"
    output.mkdir()
    uploads.mkdir()
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(output))
    monkeypatch.setattr(app_module, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(app_module, "_enqueue_job", lambda *_args: None)
    monkeypatch.setattr(app_module, "BILLING_ENABLED", False)
    return output, uploads


@pytest.fixture()
def thumbnail_session(process_dirs, monkeypatch):
    output, uploads = process_dirs
    source = uploads / "thumb_hybrid_source.mp4"
    source.write_bytes(b"fake-video")
    monkeypatch.setitem(app_module.thumbnail_sessions, "hybrid", {
        "user_id": None,
        "video_path": str(source),
        "transcript_ready": False,
        "transcript": None,
    })
    return output, uploads


def _job_from_response(response):
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    return job_id, app_module.jobs[job_id]


def test_json_provider_defaults_to_gemini_and_exports_selected_key(
        thumbnail_session, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = _post_json(
        {"thumbnail_session_id": "hybrid", "acknowledged": True},
        {"X-Gemini-Key": "gemini-header-test"},
    )
    job_id, job = _job_from_response(response)
    try:
        assert job["ai_provider"] == "gemini"
        assert job["env"]["AI_PROVIDER"] == "gemini"
        assert job["env"]["GEMINI_API_KEY"] == "gemini-header-test"
        assert "OPENAI_API_KEY" not in job["env"]
    finally:
        app_module.jobs.pop(job_id, None)


def test_openai_provider_works_for_thumbnail_json_without_gemini(
        thumbnail_session, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = _post_json(
        {
            "thumbnail_session_id": "hybrid",
            "acknowledged": True,
            "ai_provider": "openai",
        },
        {"X-OpenAI-Key": "openai-header-test"},
    )
    job_id, job = _job_from_response(response)
    try:
        assert job["ai_provider"] == "openai"
        assert job["env"]["AI_PROVIDER"] == "openai"
        assert job["env"]["OPENAI_API_KEY"] == "openai-header-test"
        assert "GEMINI_API_KEY" not in job["env"]
        manifest = json.loads((thumbnail_session[0] / job_id / ".resume.json").read_text())
        assert manifest["ai_provider"] == "openai"
        assert "openai-header-test" not in json.dumps(manifest)
    finally:
        app_module.jobs.pop(job_id, None)


def test_multipart_provider_is_forwarded(process_dirs, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def _do():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post(
                "/api/process",
                data={"acknowledged": "true", "ai_provider": "openai"},
                files={"file": ("source.mp4", b"fake-video", "video/mp4")},
                headers={"X-OpenAI-Key": "openai-multipart-test"},
            )

    response = asyncio.run(_do())
    job_id, job = _job_from_response(response)
    try:
        assert job["ai_provider"] == "openai"
        assert job["env"]["AI_PROVIDER"] == "openai"
        assert job["env"]["OPENAI_API_KEY"] == "openai-multipart-test"
    finally:
        app_module.jobs.pop(job_id, None)


def test_invalid_provider_fails_before_key_resolution(process_dirs):
    response = _post_json({"url": "https://example.com/video", "acknowledged": True,
                           "ai_provider": "not-a-provider"})
    assert response.status_code == 400
    assert "Unsupported AI provider" in response.text


def test_openai_does_not_enable_gemini_only_screencast_layout(process_dirs):
    assert app_module.layout_env(["screencast"], "openai") == {}


def test_hosted_mode_rejects_openai_byok(process_dirs, monkeypatch):
    monkeypatch.setattr(app_module, "BILLING_ENABLED", True)
    response = _post_json({
        "url": "https://example.com/video",
        "acknowledged": True,
        "ai_provider": "openai",
    }, {"X-OpenAI-Key": "openai-hosted-test"})
    assert response.status_code == 400
    assert "not available in hosted mode" in response.text


def test_selected_provider_env_fallback_and_secret_scrubbing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-env-test")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    key = asyncio.run(app_module.resolve_ai_key(_request(), "openai"))
    assert key == "openai-env-test"

    secret = "sk-proj-1234567890abcdef1234567890abcdef"
    scrubbed = app_module._scrub_secrets(f"provider error: {secret}")
    assert secret not in scrubbed
    assert "REDACTED_API_KEY" in scrubbed
