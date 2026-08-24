"""Small, isolated TikTok publisher service.

The service owns the browser session and its durable state.  It does not expose
cookies, session IDs, environment values, or upstream browser logs through the
API.  Dry-run is the default and is the path used by local E2E validation.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import threading
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator


State = Literal["pending", "uploading", "success", "failed", "login_required", "unknown"]
DATA_DIR = Path(os.environ.get("PUBLISHER_DATA_DIR", "/publisher-data"))
OUTPUT_DIR = Path(os.environ.get("PUBLISHER_OUTPUT_DIR", "/output"))
COOKIE_FILE = Path(os.environ.get("TIKTOK_COOKIE_FILE", str(DATA_DIR / "tiktok-cookies.txt")))
UPSTREAM_COMMIT = os.environ.get(
    "TIKTOK_UPLOADER_COMMIT", "6f6c594ca087b35bb152b3c60cd0196e7e46b2b9")
HEADLESS = os.environ.get("TIKTOK_UPLOADER_HEADLESS", "true").lower() in {"1", "true", "yes"}
BROWSER = os.environ.get("TIKTOK_UPLOADER_BROWSER", "chromium")
STATE_FILE = DATA_DIR / "state.json"
LOCK = threading.Lock()


class PublishRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    clip_index: int = Field(ge=0)
    video_path: str = Field(min_length=1, max_length=1000)
    source_identity: str = Field(min_length=1, max_length=1000)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    caption: str = Field(default="", max_length=2200)
    hashtags: list[str] = Field(default_factory=list, max_length=100)
    schedule_at: Optional[str] = None
    timezone: Optional[str] = None
    product_id: Optional[str] = Field(default=None, max_length=100)
    privacy: Literal["everyone", "friends", "only_you"] = "everyone"
    allow_comments: bool = True
    allow_duet: bool = True
    allow_stitch: bool = True
    dry_run: bool = True

    @field_validator("product_id")
    @classmethod
    def product_id_is_not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("product_id cannot be blank")
        return value


app = FastAPI(title="OpenShorts Local TikTok Publisher", docs_url=None, redoc_url=None)


def _load_state() -> dict[str, Any]:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"requests": {}}


def _save_state(state: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(STATE_FILE)


def _safe_record(req: PublishRequest, state: State, *, error: Optional[str] = None,
                 evidence: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    # Captions are intentionally not returned in status records; the API only
    # needs a safe state/evidence trail and must never become a secret log sink.
    record: dict[str, Any] = {
        "request_id": req.request_id,
        "job_id": req.job_id,
        "clip_index": req.clip_index,
        "state": state,
        "dry_run": req.dry_run,
        "source_identity": req.source_identity,
        "source_sha256": req.source_sha256,
        "schedule_at": req.schedule_at,
        "timezone": req.timezone,
        "product_requested": bool(req.product_id),
        "privacy": req.privacy,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        record["error"] = error[:500]
    if evidence:
        record["evidence"] = evidence
    return record


def _safe_path(value: str) -> Path:
    root = OUTPUT_DIR.resolve()
    normalized = value.replace("\\", "/")
    # The backend sends the stable container identity `/output/<job>/<file>`.
    # Resolve that identity against the mounted output root rather than the
    # host process' filesystem root (which also keeps this unit-testable).
    if normalized == "/output" or normalized.startswith("/output/"):
        path = (root / normalized.removeprefix("/output/")).resolve()
    else:
        path = Path(value).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Publisher file is outside the output root") from exc
    if not path.is_file() or path.stat().st_size <= 0:
        raise HTTPException(status_code=404, detail="Publisher file is missing")
    if path.suffix.lower() != ".mp4":
        raise HTTPException(status_code=400, detail="Publisher accepts MP4 files only")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schedule_utc(req: PublishRequest) -> Optional[datetime]:
    if not req.schedule_at:
        if req.timezone:
            raise HTTPException(status_code=422, detail="timezone requires schedule_at")
        return None
    if not req.timezone:
        raise HTTPException(status_code=422, detail="schedule_at requires an explicit timezone")
    try:
        ZoneInfo(req.timezone)
        value = datetime.fromisoformat(req.schedule_at.replace("Z", "+00:00"))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail="schedule_at/timezone is invalid") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(status_code=422, detail="schedule_at must include an offset")
    utc = value.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if utc < now + timedelta(minutes=20) or utc > now + timedelta(days=10):
        raise HTTPException(status_code=422, detail="TikTok schedule must be 20 minutes to 10 days in the future")
    if utc.second or utc.microsecond or utc.minute % 5:
        raise HTTPException(status_code=422, detail="TikTok schedule must use a five-minute boundary")
    # The pinned upstream expects a naive or UTC-aware datetime.  Naive UTC is
    # unambiguous and avoids its rejection of non-UTC offsets.
    return utc.replace(tzinfo=None)


def _interactivity(req: PublishRequest) -> dict[str, bool]:
    return {
        "allow_comments": req.allow_comments,
        "allow_duet": req.allow_duet,
        "allow_stitch": req.allow_stitch,
    }


def _adapter_upload(req: PublishRequest, path: Path, schedule: Optional[datetime]) -> bool:
    """Call the pinned upstream package exactly once.

    Import is lazy so a dry-run works in lightweight test environments and so
    Playwright remains isolated from the OpenShorts backend image.
    """
    try:
        from tiktok_uploader import TikTokUploader
    except Exception as exc:
        raise RuntimeError("pinned TikTok uploader is unavailable") from exc

    # The upstream auth implementation prints cookie dictionaries and browser
    # diagnostics.  Contain both streams; neither belongs in Docker logs.
    sink = io.StringIO()
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    uploader = None
    try:
        with redirect_stdout(sink), redirect_stderr(sink):
            uploader = TikTokUploader(
                cookies=str(COOKIE_FILE),
                browser=BROWSER,
                headless=HEADLESS,
            )
            return bool(uploader.upload_video(
                str(path),
                description=req.caption,
                schedule=schedule,
                product_id=req.product_id,
                visibility=req.privacy,
                num_retries=0,
                comment=req.allow_comments,
                duet=req.allow_duet,
                stitch=req.allow_stitch,
            ))
    finally:
        logging.disable(previous_logging)
        if uploader is not None:
            try:
                uploader.close()
            except Exception:
                pass


def _recover_inflight() -> None:
    state = _load_state()
    changed = False
    for record in state.get("requests", {}).values():
        if record.get("state") in {"pending", "uploading"}:
            record["state"] = "unknown"
            record["error"] = "Publisher restarted during an in-flight attempt; retry is blocked."
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            changed = True
    if changed:
        _save_state(state)


@app.on_event("startup")
def startup() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _recover_inflight()


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "local-tiktok-publisher", "upstream_commit": UPSTREAM_COMMIT}


@app.get("/v1/status")
def status() -> dict[str, Any]:
    state = _load_state()
    records = list(state.get("requests", {}).values())
    last = records[-1] if records else None
    busy = bool(last and last.get("state") in {"pending", "uploading"})
    configured = COOKIE_FILE.is_file() and COOKIE_FILE.stat().st_size > 0
    return {
        "enabled": True,
        "state": "login_required" if not configured else (last.get("state") if last else "ready"),
        "configured": configured,
        "session_valid": None,
        "login_required": not configured,
        "busy": busy,
        "last_state": last.get("state") if last else None,
        "upstream_commit": UPSTREAM_COMMIT,
    }


@app.post("/v1/publish")
def publish(req: PublishRequest) -> dict[str, Any]:
    schedule = _schedule_utc(req)
    path = _safe_path(req.video_path)
    actual_sha = _sha256(path)
    if actual_sha != req.source_sha256:
        raise HTTPException(status_code=409, detail="Current server file changed before publisher boundary")

    with LOCK:
        state = _load_state()
        existing = state.setdefault("requests", {}).get(req.request_id)
        if existing:
            return {**existing, "duplicate": True}
        if any(record.get("state") in {"pending", "uploading"}
               for record in state["requests"].values()):
            raise HTTPException(status_code=409, detail="A TikTok publish is already busy")
        record = _safe_record(req, "pending", evidence={
            "filename": path.name,
            "bytes": path.stat().st_size,
            "sha256": actual_sha,
            "caption_chars": len(req.caption),
            "hashtags": [f"#{tag}" for tag in req.hashtags],
            "interactivity": _interactivity(req),
            "product_id_forwarded": bool(req.product_id),
        })
        state["requests"][req.request_id] = record
        _save_state(state)

        if req.dry_run:
            record["state"] = "success"
            record["mode"] = "dry_run"
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            state["requests"][req.request_id] = record
            _save_state(state)
            return {**record, "mode": "dry_run"}

        if not COOKIE_FILE.is_file() or COOKIE_FILE.stat().st_size <= 0:
            record["state"] = "login_required"
            record["error"] = "TikTok session is not configured"
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            state["requests"][req.request_id] = record
            _save_state(state)
            return record

        record["state"] = "uploading"
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        state["requests"][req.request_id] = record
        _save_state(state)

        try:
            ok = _adapter_upload(req, path, schedule)
        except Exception as exc:
            # The browser may have reached TikTok before an exception escaped;
            # do not label that ambiguous outcome as a safe failure or retry it.
            record["state"] = "unknown"
            record["error"] = "TikTok uploader ended ambiguously; inspect TikTok before retrying."
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            state["requests"][req.request_id] = record
            _save_state(state)
            return record

        # The upstream API has no post ID and catches product/interactivity
        # failures internally.  Do not claim a fully confirmed success when a
        # requested option cannot be independently verified.
        option_confirmation = not req.product_id and req.allow_comments and req.allow_duet and req.allow_stitch
        record["state"] = "success" if ok and option_confirmation else ("unknown" if ok else "failed")
        if not ok:
            record["error"] = "TikTok uploader did not confirm completion"
        elif not option_confirmation:
            record["error"] = "TikTok upload returned, but requested options have no independent confirmation"
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        state["requests"][req.request_id] = record
        _save_state(state)
        return record
