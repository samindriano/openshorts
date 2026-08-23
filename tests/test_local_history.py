"""Contract tests for the self-hosted local clip library."""

import asyncio
import json

import httpx
import pytest

app_module = pytest.importorskip("app")

JOB_ID = "11111111-1111-4111-8111-111111111111"


def _request(method, path):
    async def _do():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver") as client:
            return await client.request(method, path)
    return asyncio.run(_do())


@pytest.fixture()
def local_job(tmp_path, monkeypatch):
    output_root = tmp_path / "output"
    uploads_root = tmp_path / "uploads"
    job_dir = output_root / JOB_ID
    job_dir.mkdir(parents=True)
    uploads_root.mkdir()
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(output_root))
    monkeypatch.setattr(app_module, "UPLOAD_DIR", str(uploads_root))
    monkeypatch.setattr(app_module, "BILLING_ENABLED", False)

    clip = {
        "start": 0,
        "end": 10,
        "video_title_for_youtube_short": "Test local clip",
        "video_url": f"/videos/{JOB_ID}/source_clip_1.mp4",
    }
    (job_dir / "source_metadata.json").write_text(
        json.dumps({"shorts": [clip], "source_video": "source.mp4"}),
        encoding="utf-8",
    )
    (job_dir / "source_clip_1.mp4").write_bytes(b"clip-bytes")
    (job_dir / "source.mp4").write_bytes(b"source-in-job")
    upload = uploads_root / f"{JOB_ID}_upload.mp4"
    upload.write_bytes(b"source-upload")
    app_module.jobs[JOB_ID] = {
        "status": "completed",
        "logs": [],
        "user_id": None,
        "result": {"clips": [clip]},
    }
    try:
        yield output_root, uploads_root, upload
    finally:
        app_module.jobs.pop(JOB_ID, None)


def test_local_history_lists_current_clip_with_cache_version(local_job):
    response = _request("GET", "/api/local/history")

    assert response.status_code == 200
    data = response.json()
    assert data["projects"][0]["job_id"] == JOB_ID
    assert data["projects"][0]["clip_count"] == 1
    assert data["videos"][0]["job_id"] == JOB_ID
    assert data["videos"][0]["view_url"].startswith(
        f"/videos/{JOB_ID}/source_clip_1.mp4?v="
    )


def test_local_history_delete_removes_only_owned_job_artifacts(local_job):
    output_root, _uploads_root, upload = local_job

    response = _request("DELETE", f"/api/local/history/{JOB_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["source_upload_deleted"] is True
    assert body["deleted_bytes"] > 0
    assert not (output_root / JOB_ID).exists()
    assert not upload.exists()
    assert JOB_ID not in app_module.jobs


def test_local_history_does_not_delete_active_job(local_job):
    app_module.jobs[JOB_ID]["status"] = "processing"

    response = _request("DELETE", f"/api/local/history/{JOB_ID}")

    assert response.status_code == 409
    assert (local_job[0] / JOB_ID).exists()
    assert local_job[2].exists()


def test_local_history_rejects_invalid_job_id(local_job):
    response = _request("DELETE", "/api/local/history/../../.env")

    assert response.status_code in (400, 404)
    assert (local_job[0] / JOB_ID).exists()
