"""Backend-owned contract for the self-hosted TikTok publisher.

This module deliberately contains no TikTok credentials.  The browser session
is owned by the publisher container; the OpenShorts API only forwards a
server-resolved current-file identity and publishing options.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, field_validator


PublisherState = Literal[
    "pending", "uploading", "success", "failed", "login_required", "unknown", "unavailable"
]


class LocalTikTokPublishRequest(BaseModel):
    job_id: str = Field(min_length=1, max_length=128)
    clip_index: int = Field(ge=0)
    caption: Optional[str] = Field(default=None, max_length=2200)
    hashtags: list[str] = Field(default_factory=list, max_length=100)
    schedule_at: Optional[str] = None
    timezone: Optional[str] = None
    product_id: Optional[str] = Field(default=None, max_length=100)
    privacy: Literal["everyone", "friends", "only_you"] = "everyone"
    allow_comments: bool = True
    allow_duet: bool = True
    allow_stitch: bool = True
    dry_run: bool = True
    request_id: Optional[str] = Field(default=None, max_length=128)

    @field_validator("hashtags")
    @classmethod
    def validate_hashtags(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            tag = value.strip().lstrip("#")
            if not tag:
                continue
            if any(ch.isspace() for ch in tag) or len(tag) > 100:
                raise ValueError("hashtags must be one word and at most 100 characters")
            cleaned.append(tag)
        return cleaned

    @field_validator("product_id")
    @classmethod
    def validate_product_id(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("product_id cannot be blank")
        return value


class AuthoritativeClip:
    def __init__(self, path: Path, filename: str, source_identity: str, sha256: str):
        self.path = path
        self.filename = filename
        self.source_identity = source_identity
        self.sha256 = sha256


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_authoritative_clip(job: dict[str, Any], output_root: str | Path,
                               job_id: str, clip_index: int) -> AuthoritativeClip:
    """Resolve only the current ResultCard/server pointer for a clip.

    Never choose by mtime and never accept a client-provided filename.  This is
    the boundary that keeps clean, archived, or stale intermediate MP4s out of
    the local publisher.
    """
    try:
        clip = job["result"]["clips"][clip_index]
        video_url = str(clip.get("video_url") or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Job clip is not available") from exc

    parsed_path = urlsplit(video_url).path
    parts = [part for part in parsed_path.split("/") if part]
    if len(parts) != 3 or parts[0] != "videos" or parts[1] != job_id:
        raise HTTPException(status_code=409, detail="Clip does not have an authoritative server file")
    filename = parts[2]
    if filename in {".", ".."} or Path(filename).name != filename:
        raise HTTPException(status_code=409, detail="Clip server file is invalid")

    root = Path(output_root).resolve()
    job_root = (root / job_id).resolve()
    path = (job_root / filename).resolve()
    if path.parent != job_root or not path.is_file() or path.stat().st_size <= 0:
        raise HTTPException(status_code=404, detail="Current clip file is missing")
    return AuthoritativeClip(path, filename, video_url, _sha256_file(path))


def build_caption(clip: dict[str, Any], caption: Optional[str], hashtags: list[str]) -> str:
    base = (caption or clip.get("video_description_for_tiktok")
            or clip.get("video_description_for_instagram")
            or clip.get("video_title_for_youtube_short") or "").strip()
    suffix = " ".join(f"#{tag.lstrip('#')}" for tag in hashtags if tag.strip())
    return f"{base} {suffix}".strip()


class LocalTikTokPublisherClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 30.0):
        self.base_url = (base_url or os.environ.get(
            "LOCAL_TIKTOK_PUBLISHER_URL", "http://publisher:3200")).rstrip("/")
        self.timeout = timeout

    async def status(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.base_url}/v1/status")
            response.raise_for_status()
            data = response.json()
            return {"available": True, **data}
        except Exception:
            return {
                "available": False,
                "enabled": False,
                "state": "unavailable",
                "configured": False,
                "session_valid": None,
                "login_required": False,
                "busy": False,
            }

    async def publish(self, clip: AuthoritativeClip, req: LocalTikTokPublishRequest,
                      caption: str) -> dict[str, Any]:
        payload = req.model_dump()
        payload.update({
            "video_path": f"/output/{req.job_id}/{clip.filename}",
            "source_identity": clip.source_identity,
            "source_sha256": clip.sha256,
            "caption": caption,
            "request_id": req.request_id or str(uuid4()),
        })
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(f"{self.base_url}/v1/publish", json=payload)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", "Publisher request failed")
            except Exception:
                detail = "Publisher request failed"
            raise HTTPException(status_code=502, detail=str(detail))
        return response.json()


async def assert_self_host(request: Request, billing_enabled: bool) -> None:
    if billing_enabled:
        raise HTTPException(status_code=404, detail="Local publisher is self-host only")
