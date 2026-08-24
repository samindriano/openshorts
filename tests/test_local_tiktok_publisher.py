import hashlib
import json
from pathlib import Path

import pytest

import local_tiktok_publisher as contract
import publisher.app as publisher


def _write(path: Path, value: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return hashlib.sha256(value).hexdigest()


def test_authoritative_pointer_beats_clean_file_and_mtime(tmp_path):
    job_id = "job-1"
    root = tmp_path / "output"
    clean = root / job_id / "source_clip_1.mp4"
    edited = root / job_id / "subtitled_2_source_clip_1.mp4"
    _write(clean, b"clean")
    digest = _write(edited, b"edited-current")
    job = {"result": {"clips": [{"video_url": f"/videos/{job_id}/{edited.name}?v=2"}]}}

    result = contract.resolve_authoritative_clip(job, root, job_id, 0)

    assert result.filename == edited.name
    assert result.sha256 == digest


def test_authoritative_resolver_rejects_client_or_cross_job_path(tmp_path):
    job_id = "job-1"
    root = tmp_path / "output"
    _write(root / job_id / "clip.mp4", b"x")
    job = {"result": {"clips": [{"video_url": "/videos/other/clip.mp4"}]}}

    with pytest.raises(Exception) as exc:
        contract.resolve_authoritative_clip(job, root, job_id, 0)
    assert getattr(exc.value, "status_code", None) == 409


def test_caption_mapping_keeps_caption_and_adds_hashtags():
    caption = contract.build_caption({"video_description_for_tiktok": "A useful idea"}, "Edited caption", ["#fyp", "finance"])
    assert caption == "Edited caption #fyp #finance"


def test_dry_run_verifies_exact_hash_and_does_not_need_cookie(tmp_path, monkeypatch):
    root = tmp_path / "output"
    data = b"edited server current file"
    path = root / "job-1" / "subtitled_current.mp4"
    digest = _write(path, data)
    data_dir = tmp_path / "publisher-data"
    monkeypatch.setattr(publisher, "OUTPUT_DIR", root)
    monkeypatch.setattr(publisher, "DATA_DIR", data_dir)
    monkeypatch.setattr(publisher, "STATE_FILE", data_dir / "state.json")
    monkeypatch.setattr(publisher, "COOKIE_FILE", data_dir / "tiktok-cookies.txt")

    req = publisher.PublishRequest(
        request_id="dry-1", job_id="job-1", clip_index=0,
        video_path="/output/job-1/subtitled_current.mp4",
        source_identity="/videos/job-1/subtitled_current.mp4",
        source_sha256=digest, caption="Caption", hashtags=["fyp"], dry_run=True,
    )
    result = publisher.publish(req)

    assert result["state"] == "success"
    assert result["mode"] == "dry_run"
    assert result["evidence"]["sha256"] == digest
    assert "tiktok-cookies" not in json.dumps(result)


def test_changed_file_is_rejected_before_publish(tmp_path, monkeypatch):
    root = tmp_path / "output"
    path = root / "job-1" / "clip.mp4"
    _write(path, b"actual")
    data_dir = tmp_path / "publisher-data"
    monkeypatch.setattr(publisher, "OUTPUT_DIR", root)
    monkeypatch.setattr(publisher, "DATA_DIR", data_dir)
    monkeypatch.setattr(publisher, "STATE_FILE", data_dir / "state.json")
    req = publisher.PublishRequest(
        request_id="mismatch", job_id="job-1", clip_index=0,
        video_path="/output/job-1/clip.mp4", source_identity="/videos/job-1/clip.mp4",
        source_sha256="0" * 64, dry_run=True,
    )
    with pytest.raises(Exception) as exc:
        publisher.publish(req)
    assert getattr(exc.value, "status_code", None) == 409


def test_unknown_is_persisted_and_duplicate_does_not_retry(tmp_path, monkeypatch):
    root = tmp_path / "output"
    path = root / "job-1" / "clip.mp4"
    digest = _write(path, b"actual")
    data_dir = tmp_path / "publisher-data"
    cookie = data_dir / "tiktok-cookies.txt"
    cookie.parent.mkdir(parents=True)
    cookie.write_text("secret-cookie", encoding="utf-8")
    monkeypatch.setattr(publisher, "OUTPUT_DIR", root)
    monkeypatch.setattr(publisher, "DATA_DIR", data_dir)
    monkeypatch.setattr(publisher, "STATE_FILE", data_dir / "state.json")
    monkeypatch.setattr(publisher, "COOKIE_FILE", cookie)
    calls = []

    def fail_once(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("browser failure")

    monkeypatch.setattr(publisher, "_adapter_upload", fail_once)
    req = publisher.PublishRequest(
        request_id="unknown-1", job_id="job-1", clip_index=0,
        video_path="/output/job-1/clip.mp4", source_identity="/videos/job-1/clip.mp4",
        source_sha256=digest, dry_run=False,
    )

    first = publisher.publish(req)
    second = publisher.publish(req)

    assert first["state"] == "unknown"
    assert second["state"] == "unknown"
    assert second["duplicate"] is True
    assert len(calls) == 1
    assert "secret-cookie" not in (data_dir / "state.json").read_text(encoding="utf-8")
