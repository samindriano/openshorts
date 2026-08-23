import json
import os
from types import SimpleNamespace

import state_guard as sg


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def touch_nonempty(path, mtime_ns=None):
    with open(path, "wb") as f:
        f.write(b"x")
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))


def make_core(tmp_path, clip, *, base="video"):
    job_id = "job-1"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    meta = {"shorts": [dict(clip)]}
    meta_path = job_dir / f"{base}_metadata.json"
    write_json(meta_path, meta)
    core = SimpleNamespace(
        OUTPUT_DIR=str(tmp_path),
        jobs={job_id: {
            "status": "completed",
            "output_dir": str(job_dir),
            "result": {"clips": [dict(clip)]},
        }},
    )
    return core, job_id, job_dir, meta_path


def test_journal_wins_after_restart(tmp_path):
    base = "video"
    clean = f"{base}_clip_1.mp4"
    old = f"subtitled_1780000000000000000_{clean}"
    new = f"subtitled_1790000000000000000_{clean}"
    clip = {"video_url": f"/videos/job-1/{old}", "render_revision": "1780000000000000000"}
    core, job_id, job_dir, meta_path = make_core(tmp_path, clip, base=base)
    touch_nonempty(job_dir / clean)
    touch_nonempty(job_dir / old)
    touch_nonempty(job_dir / new)
    write_json(job_dir / sg.STATE_FILE, {
        "version": sg.STATE_VERSION,
        "saved_at_ns": 1790000000000000001,
        "clips": [{"index": 0, "video_url": f"/videos/{job_id}/{new}",
                   "render_revision": "1790000000000000000",
                   "subtitle_config": {"fontSize": 20}}],
    })

    assert sg.repair_job(core, job_id)
    assert core.jobs[job_id]["result"]["clips"][0]["video_url"].endswith(new)
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["shorts"][0]["video_url"].endswith(new)
    assert meta["shorts"][0]["subtitle_config"] == {"fontSize": 20}


def test_pre_guard_stale_revision_promotes_newer_artifact(tmp_path):
    base = "video"
    clean = f"{base}_clip_1.mp4"
    old = f"subtitled_1780000000000000000_{clean}"
    new = f"subtitled_1790000000000000000_{clean}"
    clip = {"video_url": f"/videos/job-1/{old}", "render_revision": "1780000000000000000"}
    core, job_id, job_dir, _ = make_core(tmp_path, clip, base=base)
    for name in (clean, old, new):
        touch_nonempty(job_dir / name)

    assert sg.repair_job(core, job_id)
    assert core.jobs[job_id]["result"]["clips"][0]["video_url"].endswith(new)


def test_explicit_remove_never_resurrects_old_subtitles(tmp_path):
    base = "video"
    clean = f"{base}_clip_1.mp4"
    old = f"subtitled_1790000000000000000_{clean}"
    clip = {"video_url": f"/videos/job-1/{clean}",
            "render_revision": "remove-1800000000000000000",
            "subtitle_config": None}
    core, job_id, job_dir, _ = make_core(tmp_path, clip, base=base)
    touch_nonempty(job_dir / clean)
    touch_nonempty(job_dir / old)

    sg.repair_job(core, job_id)
    current = core.jobs[job_id]["result"]["clips"][0]
    assert current["video_url"].endswith(clean)
    assert current["render_revision"].startswith("remove-")
    assert current["subtitle_config"] is None


def test_invalid_pointer_uses_revision_not_restored_mtime(tmp_path):
    base = "video"
    clean = f"{base}_clip_1.mp4"
    older = f"subtitled_1780000000000000000_{clean}"
    newer = f"subtitled_1790000000000000000_{clean}"
    clip = {"video_url": "/videos/job-1/missing.mp4"}
    core, job_id, job_dir, _ = make_core(tmp_path, clip, base=base)
    touch_nonempty(job_dir / clean, 300)
    # Deliberately give the older revision the newest filesystem mtime, as an
    # archive restore can do. Revision identity must still win.
    touch_nonempty(job_dir / older, 900)
    touch_nonempty(job_dir / newer, 100)

    assert sg.repair_job(core, job_id)
    assert core.jobs[job_id]["result"]["clips"][0]["video_url"].endswith(newer)


def test_snapshot_atomically_mirrors_memory_to_metadata_and_journal(tmp_path):
    base = "video"
    clean = f"{base}_clip_1.mp4"
    new = f"subtitled_1790000000000000000_{clean}"
    old_clip = {"video_url": f"/videos/job-1/{clean}"}
    core, job_id, job_dir, meta_path = make_core(tmp_path, old_clip, base=base)
    touch_nonempty(job_dir / clean)
    touch_nonempty(job_dir / new)
    core.jobs[job_id]["result"]["clips"][0].update({
        "video_url": f"/videos/{job_id}/{new}",
        "render_revision": "1790000000000000000",
        "subtitle_config": {"captions": [{"text": "baru", "startMs": 0, "endMs": 500}]},
    })

    assert sg.snapshot_job(core, job_id)
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    with open(job_dir / sg.STATE_FILE, encoding="utf-8") as f:
        journal = json.load(f)
    assert meta["shorts"][0]["video_url"].endswith(new)
    assert journal["clips"][0]["video_url"].endswith(new)
    assert journal["clips"][0]["subtitle_config"]["captions"][0]["text"] == "baru"


def test_unversioned_valid_derived_file_is_not_overridden(tmp_path):
    base = "video"
    clean = f"{base}_clip_1.mp4"
    edited = f"edited_{clean}"
    old_caption = f"subtitled_1790000000000000000_{clean}"
    clip = {"video_url": f"/videos/job-1/{edited}"}
    core, job_id, job_dir, _ = make_core(tmp_path, clip, base=base)
    for name in (clean, edited, old_caption):
        touch_nonempty(job_dir / name)

    sg.repair_job(core, job_id)
    assert core.jobs[job_id]["result"]["clips"][0]["video_url"].endswith(edited)


def test_state_mutation_filter_is_narrow():
    assert sg.is_state_mutation("/api/subtitle", "POST")
    assert sg.is_state_mutation("/api/subtitle/remove", "POST")
    assert sg.is_state_mutation("/api/clip/rerender", "POST")
    assert not sg.is_state_mutation("/api/status/job", "GET")
    assert not sg.is_state_mutation("/api/subtitle", "GET")


def test_asgi_guard_snapshots_before_success_body(monkeypatch):
    import asyncio

    events = []

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    core = SimpleNamespace()

    def fake_snapshot(_core):
        events.append("snapshot")
        return 1

    monkeypatch.setattr(sg, "snapshot_all", fake_snapshot)
    guard = sg.ClipStateGuard(inner, core)

    async def send(message):
        if message["type"] == "http.response.body":
            events.append("body")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    asyncio.run(guard(
        {"type": "http", "path": "/api/subtitle", "method": "POST"}, receive, send
    ))
    assert events == ["snapshot", "body"]


def test_asgi_guard_does_not_snapshot_failed_mutation(monkeypatch):
    import asyncio

    calls = []

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 500, "headers": []})
        await send({"type": "http.response.body", "body": b"no", "more_body": False})

    monkeypatch.setattr(sg, "snapshot_all", lambda core: calls.append(1))
    guard = sg.ClipStateGuard(inner, SimpleNamespace())

    async def send(message):
        pass

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    asyncio.run(guard(
        {"type": "http", "path": "/api/subtitle", "method": "POST"}, receive, send
    ))
    assert calls == []


def test_asgi_guard_repairs_before_startup_complete(monkeypatch):
    import asyncio

    events = []

    async def inner(scope, receive, send):
        await send({"type": "lifespan.startup.complete"})

    def fake_repair(_core):
        events.append("repair")
        return 1

    monkeypatch.setattr(sg, "repair_all", fake_repair)
    guard = sg.ClipStateGuard(inner, SimpleNamespace())

    async def send(message):
        events.append("startup-complete")

    async def receive():
        return {"type": "lifespan.startup"}

    asyncio.run(guard({"type": "lifespan"}, receive, send))
    assert events == ["repair", "startup-complete"]
