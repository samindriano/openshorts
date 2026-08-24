"""Durable clip-state guard for restart-safe media edits.

This module deliberately sits outside app.py so it can protect the existing
pipeline without changing clip-selection/render semantics. It makes the mutable
"current clip" pointer crash-safe and reconciles recovered jobs after a process
restart.
"""
from __future__ import annotations

import copy
import glob
import json
import os
import re
import time
from typing import Any, Dict, Iterable, Optional, Tuple

STATE_FILE = ".clip-current-state.json"
STATE_VERSION = 1

# Only fields that describe the current editable/rendered clip are mirrored.
# AI descriptions/titles and immutable analysis output stay owned by metadata.
_MUTABLE_FIELDS = (
    "video_url",
    "render_revision",
    "subtitle_config",
    "auto_hook",
    "recipe",
    "crop_overrides",
    "start",
    "end",
)

_NS_RE = re.compile(r"(?<!\d)(\d{13,})(?!\d)")
_REMOVE_RE = re.compile(r"^remove-(\d{13,})$")


def _safe_json_load(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _atomic_json_write(path: str, data: dict) -> None:
    """Replace a JSON file atomically, with fsync before the rename.

    A process kill can leave the old version or the new version, but never a
    half-written JSON document. The temp file lives next to the target so
    os.replace is an atomic same-filesystem rename on normal local filesystems.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}-{time.time_ns()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _filename(video_url: Any) -> str:
    if not isinstance(video_url, str) or not video_url:
        return ""
    return os.path.basename(video_url.split("?", 1)[0])


def _usable_file(job_dir: str, filename: str) -> bool:
    if not filename or os.path.basename(filename) != filename:
        return False
    path = os.path.join(job_dir, filename)
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _revision_number(value: Any) -> Optional[int]:
    """Extract a monotonic nanosecond-ish revision from a revision or filename."""
    if not isinstance(value, str):
        return None
    remove = _REMOVE_RE.match(value)
    if remove:
        try:
            return int(remove.group(1))
        except ValueError:
            return None
    numbers = []
    for token in _NS_RE.findall(value):
        try:
            numbers.append(int(token))
        except ValueError:
            pass
    return max(numbers) if numbers else None


def _clip_suffix(base_name: str, index: int) -> str:
    return f"{base_name}_clip_{index + 1}.mp4"


def _artifact_revision(filename: str, clean_suffix: str) -> Optional[int]:
    """Revision carried by derivation prefixes, never by the source title.

    Finance titles commonly contain long raw numbers (e.g. 1000000000000).
    Those digits live inside the canonical suffix and must not be mistaken for
    a render revision when deciding whether an old subtitle beats an edit.
    """
    if not isinstance(filename, str):
        return None
    prefix = filename[:-len(clean_suffix)] if clean_suffix and filename.endswith(clean_suffix) else filename
    return _revision_number(prefix)


def _candidate_files(job_dir: str, base_name: str, index: int) -> Iterable[str]:
    """Yield non-temp MP4 artifacts that belong to one canonical clip.

    Every durable derivation keeps the canonical clip filename as its suffix:
    subtitled_<rev>_, hooked_<rev>_, recut_<rev>_, edited_, translated_, etc.
    Using the suffix rather than an allow-list prevents a new edit feature from
    becoming restart-unsafe just because this guard did not know its prefix yet.
    """
    suffix = _clip_suffix(base_name, index)
    pattern = os.path.join(job_dir, f"*{glob.escape(suffix)}")
    for path in glob.glob(pattern):
        name = os.path.basename(path)
        if name.startswith("temp_") or not name.endswith(".mp4"):
            continue
        if _usable_file(job_dir, name):
            yield name


def _best_fallback(job_dir: str, base_name: str, index: int) -> Optional[str]:
    candidates = list(dict.fromkeys(_candidate_files(job_dir, base_name, index)))
    if not candidates:
        return None

    clean = _clip_suffix(base_name, index)

    def key(name: str) -> Tuple[int, int, int, str]:
        rev = _artifact_revision(name, clean)
        try:
            mtime = os.stat(os.path.join(job_dir, name)).st_mtime_ns
        except OSError:
            mtime = 0
        # A numeric render revision is stronger evidence than mtime (R2 restore
        # can rewrite mtimes). If no revision exists, prefer a derived artifact
        # over the pristine clean clip, then use mtime as a legacy tiebreaker.
        return (
            1 if rev is not None else 0,
            rev or 0,
            (1 if name != clean else 0) * max(mtime, 0),
            name,
        )

    return max(candidates, key=key)


def _copy_mutable(src: dict, dst: dict) -> bool:
    changed = False
    for field in _MUTABLE_FIELDS:
        if field in src and dst.get(field) != src.get(field):
            dst[field] = copy.deepcopy(src.get(field))
            changed = True
    return changed


def _explicit_remove(clip: dict) -> bool:
    revision = clip.get("render_revision")
    return isinstance(revision, str) and bool(_REMOVE_RE.match(revision))


def _maybe_migrate_to_newer_artifact(
    job_dir: str, base_name: str, index: int, clip: dict
) -> Optional[str]:
    """Repair pre-guard stale metadata without resurrecting removed captions.

    Before this guard existed, a successful render could leave a newer derived
    MP4 next to an older persisted pointer. We promote only when the comparison
    is safe: the current pointer is the pristine clip or has a parseable render
    revision, and a candidate carries a strictly newer revision. An explicit
    subtitle removal is authoritative even though old subtitled files remain.
    """
    if _explicit_remove(clip):
        return None

    current_name = _filename(clip.get("video_url"))
    clean = _clip_suffix(base_name, index)
    current_rev = _revision_number(clip.get("render_revision"))
    if current_rev is None:
        current_rev = _artifact_revision(current_name, clean)

    # Unversioned derived files (e.g. legacy edited_*) are authoritative when
    # they still exist; guessing from another file's timestamp could roll them
    # back to an unrelated older render.
    if current_name and current_name != clean and current_rev is None:
        return None

    best = _best_fallback(job_dir, base_name, index)
    if not best or best == current_name:
        return None
    best_rev = _artifact_revision(best, clean)
    if best_rev is None:
        return None
    if current_rev is not None and best_rev <= current_rev:
        return None
    return best


def _journal_path(job_dir: str) -> str:
    return os.path.join(job_dir, STATE_FILE)


def _metadata_path(job_dir: str) -> Optional[str]:
    matches = sorted(glob.glob(os.path.join(job_dir, "*_metadata.json")))
    return matches[0] if matches else None


def _state_from_clips(clips: list) -> list:
    out = []
    for index, clip in enumerate(clips):
        row = {"index": index}
        if isinstance(clip, dict):
            for field in _MUTABLE_FIELDS:
                if field in clip:
                    row[field] = copy.deepcopy(clip[field])
        out.append(row)
    return out


def _write_journal(job_dir: str, clips: list) -> None:
    _atomic_json_write(
        _journal_path(job_dir),
        {
            "version": STATE_VERSION,
            "saved_at_ns": time.time_ns(),
            "clips": _state_from_clips(clips),
        },
    )


def _journal_rows(job_dir: str) -> Dict[int, dict]:
    journal = _safe_json_load(_journal_path(job_dir)) or {}
    rows = journal.get("clips") if journal.get("version") == STATE_VERSION else None
    out: Dict[int, dict] = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("index"), int):
                out[row["index"]] = row
    return out


def snapshot_clip(core: Any, job_id: str, clip_index: int) -> bool:
    """Commit exactly one successful clip mutation to the crash-safe sidecar.

    The journal is intentionally sparse until the next startup repair. A save
    for clip A must never accidentally commit clip B's in-progress in-memory
    state. Existing committed rows are preserved and only the response target
    is replaced.
    """
    job = getattr(core, "jobs", {}).get(job_id)
    if not isinstance(job, dict) or job.get("status") != "completed":
        return False
    mem_clips = ((job.get("result") or {}).get("clips") or [])
    if (not isinstance(mem_clips, list) or clip_index < 0
            or clip_index >= len(mem_clips) or not isinstance(mem_clips[clip_index], dict)):
        return False

    job_dir = job.get("output_dir") or os.path.join(core.OUTPUT_DIR, job_id)
    rows = _journal_rows(job_dir)
    row = {"index": clip_index}
    for field in _MUTABLE_FIELDS:
        if field in mem_clips[clip_index]:
            row[field] = copy.deepcopy(mem_clips[clip_index][field])
    rows[clip_index] = row
    _atomic_json_write(
        _journal_path(job_dir),
        {
            "version": STATE_VERSION,
            "saved_at_ns": time.time_ns(),
            "clips": [rows[i] for i in sorted(rows)],
        },
    )
    return True


def snapshot_job(core: Any, job_id: str) -> bool:
    """Compatibility/test helper: commit every clip of one completed job."""
    job = getattr(core, "jobs", {}).get(job_id)
    mem_clips = ((job or {}).get("result") or {}).get("clips") or []
    if not isinstance(mem_clips, list) or not mem_clips:
        return False
    ok = False
    for clip_index in range(len(mem_clips)):
        ok = snapshot_clip(core, job_id, clip_index) or ok
    return ok


def repair_job(core: Any, job_id: str) -> bool:
    """Reconcile one recovered completed job, preferring the last saved state.

    Priority:
      1. valid sidecar journal (written after successful edit responses),
      2. valid metadata pointer,
      3. safe migration to a strictly newer versioned artifact,
      4. deterministic filesystem fallback when the pointer is missing/broken.
    """
    job = getattr(core, "jobs", {}).get(job_id)
    if not isinstance(job, dict) or job.get("status") != "completed":
        return False
    job_dir = job.get("output_dir") or os.path.join(core.OUTPUT_DIR, job_id)
    meta_path = _metadata_path(job_dir)
    if not meta_path:
        return False
    metadata = _safe_json_load(meta_path)
    if not metadata:
        return False

    base_name = os.path.basename(meta_path).replace("_metadata.json", "")
    meta_clips = metadata.get("shorts") or []
    mem_clips = ((job.get("result") or {}).get("clips") or [])
    if not isinstance(meta_clips, list) or not isinstance(mem_clips, list):
        return False

    journal = _safe_json_load(_journal_path(job_dir)) or {}
    journal_rows = journal.get("clips") if journal.get("version") == STATE_VERSION else None
    by_index: Dict[int, dict] = {}
    if isinstance(journal_rows, list):
        for row in journal_rows:
            if isinstance(row, dict) and isinstance(row.get("index"), int):
                by_index[row["index"]] = row

    changed = False
    for index, meta_clip in enumerate(meta_clips):
        if not isinstance(meta_clip, dict):
            continue
        mem_clip = mem_clips[index] if index < len(mem_clips) and isinstance(mem_clips[index], dict) else None

        journal_clip = by_index.get(index)
        journal_name = _filename((journal_clip or {}).get("video_url"))
        if journal_clip and _usable_file(job_dir, journal_name):
            if _copy_mutable(journal_clip, meta_clip):
                changed = True
            if mem_clip is not None and _copy_mutable(journal_clip, mem_clip):
                changed = True
            continue

        current_name = _filename(meta_clip.get("video_url"))
        if _usable_file(job_dir, current_name):
            migrated = _maybe_migrate_to_newer_artifact(job_dir, base_name, index, meta_clip)
            if migrated:
                meta_clip["video_url"] = f"/videos/{job_id}/{migrated}"
                migrated_rev = _artifact_revision(migrated, _clip_suffix(base_name, index))
                if migrated_rev is not None:
                    meta_clip["render_revision"] = str(migrated_rev)
                changed = True
            if mem_clip is not None and _copy_mutable(meta_clip, mem_clip):
                changed = True
            continue

        fallback = _best_fallback(job_dir, base_name, index)
        if fallback:
            meta_clip["video_url"] = f"/videos/{job_id}/{fallback}"
            fallback_rev = _artifact_revision(fallback, _clip_suffix(base_name, index))
            if fallback_rev is not None:
                meta_clip["render_revision"] = str(fallback_rev)
            changed = True
            if mem_clip is not None and _copy_mutable(meta_clip, mem_clip):
                changed = True

    if changed:
        metadata["shorts"] = meta_clips
        _atomic_json_write(meta_path, metadata)

    # Always seed/refresh the journal after recovery so subsequent restarts do
    # not have to infer state from filenames again.
    canonical_clips = mem_clips if mem_clips else meta_clips
    _write_journal(job_dir, canonical_clips)
    return changed


def repair_all(core: Any) -> int:
    repaired = 0
    for job_id in list(getattr(core, "jobs", {}).keys()):
        try:
            if repair_job(core, job_id):
                repaired += 1
        except Exception as exc:
            print(f"⚠️ [state-guard] recovery failed for {job_id}: {exc}")
    if repaired:
        print(f"🛡️ [state-guard] repaired {repaired} recovered job(s).")
    return repaired

# HTTP mutations that can alter the current durable clip or its editable recipe.
_MUTATION_PATHS = (
    "/api/subtitle",
    "/api/hook",
    "/api/edit",
    "/api/clip/rerender",
    "/api/clip/reframe",
    "/api/translate",
)


def is_state_mutation(path: str, method: str) -> bool:
    if str(method or "GET").upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    return any(path == prefix or path.startswith(prefix + "/") for prefix in _MUTATION_PATHS)


async def _buffer_request_body(receive):
    """Read a small JSON edit body and return bytes + a replay receive callable."""
    messages = []
    body = bytearray()
    while True:
        message = await receive()
        messages.append(message)
        if message.get("type") != "http.request":
            break
        body.extend(message.get("body", b""))
        if not message.get("more_body", False):
            break

    pos = 0

    async def replay():
        nonlocal pos
        if pos < len(messages):
            message = messages[pos]
            pos += 1
            return message
        return {"type": "http.disconnect"}

    return bytes(body), replay


def _edit_target_from_json(body: bytes) -> Tuple[Optional[str], Optional[int]]:
    if not body:
        return None, None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    job_id = payload.get("job_id")
    clip_index = payload.get("clip_index")
    try:
        clip_index = int(clip_index)
    except (TypeError, ValueError):
        clip_index = None
    return (str(job_id) if job_id else None), clip_index


class ClipStateGuard:
    """Transparent ASGI wrapper around the existing FastAPI application.

    Successful edit responses are held for a few milliseconds until the exact
    target job's state journal is fsynced. Therefore "the UI said saved" and
    "the edit survives a backend restart" become the same transaction boundary.
    """

    def __init__(self, inner: Any, core_module: Any):
        import asyncio

        self.inner = inner
        self.core = core_module
        self._asyncio = asyncio
        # Serialize the whole mutation transaction per clip. Different clips
        # remain independent, but two tabs cannot interleave writes to the same
        # current-file pointer and make one response commit the other edit.
        self._mutation_locks: Dict[Tuple[str, int], Any] = {}
        # The journal is one shared file per job, so its read-modify-write
        # commit must still be serialized across different clip mutations.
        # This does not serialize rendering or endpoint work, only the small
        # durable snapshot that follows a successful response.
        self._journal_locks: Dict[str, Any] = {}

    async def _snapshot_clip(self, job_id: str, clip_index: int) -> bool:
        lock = self._journal_locks.setdefault(job_id, self._asyncio.Lock())
        async with lock:
            return await self._asyncio.to_thread(
                snapshot_clip, self.core, job_id, clip_index)

    async def __call__(self, scope, receive, send):
        scope_type = scope.get("type")

        if scope_type == "lifespan":
            async def guarded_lifespan_send(message):
                if message.get("type") == "lifespan.startup.complete":
                    # app.py has already run _recover_jobs_from_disk at this
                    # point. Repair before Uvicorn accepts the first request.
                    await self._asyncio.to_thread(repair_all, self.core)
                await send(message)

            return await self.inner(scope, receive, guarded_lifespan_send)

        if scope_type != "http" or not is_state_mutation(
            scope.get("path", ""), scope.get("method", "GET")
        ):
            return await self.inner(scope, receive, send)

        body, replay_receive = await _buffer_request_body(receive)
        job_id, clip_index = _edit_target_from_json(body)
        target = ((job_id, clip_index)
                  if job_id is not None and clip_index is not None else None)
        lock = (self._mutation_locks.setdefault(target, self._asyncio.Lock())
                if target is not None else self._asyncio.Lock())

        async with lock:
            captured = []

            async def capture_send(message):
                captured.append(message)

            await self.inner(scope, replay_receive, capture_send)

            status_code = next(
                (int(m.get("status", 500)) for m in captured
                 if m.get("type") == "http.response.start"),
                500,
            )

            if status_code < 400:
                persisted = False
                if target is not None:
                    try:
                        persisted = await self._snapshot_clip(job_id, clip_index)
                    except Exception as exc:
                        print(f"⚠️ [state-guard] commit failed for {job_id}/{clip_index}: {exc}")
                if not persisted:
                    # Do not acknowledge a save whose current-file pointer was not
                    # durably committed. The rendered MP4 is left intact; retrying is
                    # safe, but a false success would recreate the restart bug.
                    payload = json.dumps({
                        "detail": "Edit rendered but durable state commit failed. Retry the edit before restarting the backend."
                    }).encode("utf-8")
                    await send({
                        "type": "http.response.start",
                        "status": 500,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(payload)).encode("ascii")),
                        ],
                    })
                    await send({"type": "http.response.body", "body": payload, "more_body": False})
                    return

            for message in captured:
                await send(message)
