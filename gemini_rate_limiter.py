"""Shared Gemini request budgeting and bounded retry helpers.

The Clip Generator runs each job in a separate ``main.py`` subprocess.  A
plain module-global limiter would therefore let every job believe it owned the
whole Gemini project quota.  This module keeps the policy in-process for normal
callers and uses a tiny file-backed rolling ledger so sibling job processes on
the same host share the same budget too.

The proactive limiter is opt-in: if neither ``GEMINI_TPM_LIMIT`` nor
``GEMINI_RPM_LIMIT`` is configured, requests pass through and still receive
bounded transient-error retries.  Token reservations are conservative prompt
estimates and are corrected with Gemini usage metadata when it is available.
"""

from __future__ import annotations

import contextlib
import email.utils
import json
import math
import os
import random
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional


WINDOW_SECONDS = 60.0
_DEFAULT_HEADROOM = 0.90
# Keep the upstream no-config behavior at three total attempts. Local Free Tier
# deployments can opt into a longer bounded window via GEMINI_MAX_RETRIES.
_DEFAULT_MAX_RETRIES = 2
_DEFAULT_BACKOFF_SECONDS = 5.0


def _positive_float(name: str) -> Optional[float]:
    try:
        value = float(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _bounded_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default
    return min(max(value, low), high)


def _nonnegative_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default
    return max(0.0, value)


def _positive_int(name: str, default: int, maximum: int = 100) -> int:
    try:
        value = int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default
    return min(max(value, 0), maximum)


@dataclass(frozen=True)
class RateLimitConfig:
    """Effective rolling-window limits after safety headroom is applied."""

    tpm_limit: Optional[float]
    rpm_limit: Optional[float]
    headroom: float
    state_path: str
    max_retries: int
    backoff_seconds: float

    @classmethod
    def from_env(cls) -> "RateLimitConfig":
        headroom = _bounded_float(
            "GEMINI_RATE_HEADROOM", _DEFAULT_HEADROOM, 0.10, 1.0)
        tpm = _positive_float("GEMINI_TPM_LIMIT")
        rpm = _positive_float("GEMINI_RPM_LIMIT")
        configured_state = (os.environ.get("GEMINI_RATE_LIMIT_STATE_PATH") or "").strip()
        state_path = configured_state or os.path.join(
            tempfile.gettempdir(), "openshorts-gemini-rate-limit.json")
        return cls(
            tpm_limit=tpm * headroom if tpm is not None else None,
            rpm_limit=rpm * headroom if rpm is not None else None,
            headroom=headroom,
            state_path=state_path,
            max_retries=_positive_int(
                "GEMINI_MAX_RETRIES", _DEFAULT_MAX_RETRIES, maximum=20),
            backoff_seconds=_nonnegative_float(
                "GEMINI_RETRY_BACKOFF_SECONDS", _DEFAULT_BACKOFF_SECONDS),
        )

    @property
    def enabled(self) -> bool:
        return self.tpm_limit is not None or self.rpm_limit is not None


class _CrossProcessLock:
    """Small dependency-free lock for the shared ledger."""

    def __init__(self, path: str):
        self.path = path + ".lock"
        self._file = None

    def __enter__(self):
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._file = open(self.path, "a+b")
        self._file.seek(0)
        self._file.write(b"0")
        self._file.flush()

        if os.name == "nt":
            import msvcrt

            # LK_LOCK retries briefly in the CRT.  The outer loop handles the
            # rare case where a sibling process holds the byte for longer.
            while True:
                try:
                    self._file.seek(0)
                    msvcrt.locking(self._file.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
        else:
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._file is None:
                return
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            if self._file is not None:
                self._file.close()
                self._file = None


def estimate_tokens(text: Any, extra_tokens: int = 0) -> int:
    """Estimate request tokens without spending another Gemini API call.

    Four UTF-8 bytes per token is deliberately conservative for the mixed
    transcript/prompt text used here.  ``extra_tokens`` covers non-text parts
    such as sampled images or uploaded video, for which character counting is
    meaningless.  A modest output reserve protects the TPM budget before the
    response's actual usage metadata is known.
    """

    if isinstance(text, bytes):
        byte_count = len(text)
    else:
        byte_count = len(str(text or "").encode("utf-8"))
    output_reserve = _positive_int("GEMINI_OUTPUT_TOKEN_RESERVE", 4096, maximum=100000)
    return max(1, math.ceil(byte_count / 4) + max(0, int(extra_tokens)) + output_reserve)


def usage_tokens(response: Any) -> Optional[int]:
    """Return total billed-looking token usage when the SDK exposed it."""

    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None

    total = getattr(usage, "total_token_count", None)
    if total is not None:
        try:
            return max(0, int(total))
        except (TypeError, ValueError):
            pass

    names = (
        "prompt_token_count",
        "candidates_token_count",
        "thoughts_token_count",
        "tool_use_prompt_token_count",
    )
    values = []
    for name in names:
        value = getattr(usage, name, None)
        if value is not None:
            try:
                values.append(max(0, int(value)))
            except (TypeError, ValueError):
                pass
    return sum(values) if values else None


def _read_events(path: str) -> list[dict[str, float | str]]:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            payload = json.load(stream)
        events = payload.get("events", [])
        return events if isinstance(events, list) else []
    except (OSError, ValueError, TypeError):
        return []


def _write_events(path: str, events: list[dict[str, float | str]]) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump({"events": events}, stream, separators=(",", ":"))
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(OSError):
            os.remove(temporary)


def _prune(events: Iterable[dict[str, float | str]], now: float) -> list[dict[str, float | str]]:
    fresh = []
    for event in events:
        try:
            timestamp = float(event["timestamp"])
            tokens = max(0.0, float(event["tokens"]))
            event_id = str(event["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if now - timestamp < WINDOW_SECONDS:
            fresh.append({"id": event_id, "timestamp": timestamp, "tokens": tokens})
    return fresh


def _wait_seconds(
    events: list[dict[str, float | str]],
    estimated_tokens: float,
    now: float,
    tpm_limit: Optional[float],
    rpm_limit: Optional[float],
) -> float:
    if not events:
        # A single request larger than the configured TPM window cannot be
        # made to fit by waiting. Let the provider decide rather than deadlock.
        return 0.0

    ordered = sorted(events, key=lambda event: float(event["timestamp"]))
    waits = []
    if rpm_limit is not None and len(ordered) + 1 > rpm_limit:
        waits.append(float(ordered[0]["timestamp"]) + WINDOW_SECONDS - now)

    if tpm_limit is not None:
        total = sum(float(event["tokens"]) for event in ordered)
        if total + estimated_tokens > tpm_limit:
            remaining = total
            release_at = None
            for event in ordered:
                remaining -= float(event["tokens"])
                if remaining + estimated_tokens <= tpm_limit:
                    release_at = float(event["timestamp"]) + WINDOW_SECONDS
                    break
            # If this request itself is oversized, wait for the current window
            # to clear, then allow the one request as described above.
            if release_at is None:
                release_at = float(ordered[-1]["timestamp"]) + WINDOW_SECONDS
            waits.append(release_at - now)

    return max(0.0, max(waits, default=0.0))


@dataclass
class _Lease:
    limiter: Optional["GeminiRateLimiter"]
    event_id: Optional[str]
    estimated_tokens: int
    finished: bool = False

    def finish(
        self,
        actual_tokens: Optional[int] = None,
        *,
        discard: bool = False,
    ) -> None:
        if self.finished:
            return
        self.finished = True
        if self.limiter is not None and self.event_id is not None:
            self.limiter._finish(self.event_id, actual_tokens, discard=discard)


class GeminiRateLimiter:
    """Rolling request/token budget shared by calls in one local deployment."""

    def __init__(
        self,
        config: Optional[RateLimitConfig] = None,
        *,
        logger: Optional[Callable[[str], None]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = config or RateLimitConfig.from_env()
        self._logger = logger or print
        self._sleep = sleep
        self._thread_lock = threading.Lock()
        self._state_error_logged = False

    def _log_state_error(self, error: BaseException) -> None:
        if self._state_error_logged:
            return
        self._state_error_logged = True
        self._logger(
            f"⚠️ Gemini limiter state is unavailable ({error}); "
            "continuing with retries but without proactive queueing.")

    def acquire(self, estimated_tokens: int, label: str = "Gemini request") -> _Lease:
        estimated = max(1, int(estimated_tokens))
        if not self.config.enabled:
            return _Lease(None, None, estimated)

        logged_wait = False
        while True:
            with self._thread_lock:
                now = time.time()
                try:
                    with _CrossProcessLock(self.config.state_path):
                        events = _prune(_read_events(self.config.state_path), now)
                        wait = _wait_seconds(
                            events, estimated, now,
                            self.config.tpm_limit, self.config.rpm_limit)
                        if wait <= 0.01:
                            event_id = uuid.uuid4().hex
                            events.append({
                                "id": event_id,
                                "timestamp": now,
                                "tokens": float(estimated),
                            })
                            _write_events(self.config.state_path, events)
                            return _Lease(self, event_id, estimated)
                        _write_events(self.config.state_path, events)
                except OSError as error:
                    self._log_state_error(error)
                    return _Lease(None, None, estimated)

            if not logged_wait:
                self._logger(
                    f"⏳ Gemini rate limit approaching — queued request '{label}', "
                    f"waiting ~{max(1, math.ceil(wait))}s")
                logged_wait = True
            self._sleep(wait)

    def _finish(
        self,
        event_id: str,
        actual_tokens: Optional[int],
        *,
        discard: bool,
    ) -> None:
        if not self.config.enabled:
            return
        with self._thread_lock:
            now = time.time()
            try:
                with _CrossProcessLock(self.config.state_path):
                    events = _prune(_read_events(self.config.state_path), now)
                    updated = []
                    for event in events:
                        if str(event.get("id")) != event_id:
                            updated.append(event)
                            continue
                        if not discard:
                            if actual_tokens is not None:
                                event["tokens"] = float(max(1, int(actual_tokens)))
                            updated.append(event)
                    _write_events(self.config.state_path, updated)
            except OSError as error:
                self._log_state_error(error)


_default_limiter: Optional[GeminiRateLimiter] = None
_default_signature = None


def get_gemini_limiter(
    *,
    logger: Optional[Callable[[str], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> GeminiRateLimiter:
    """Return the process-wide limiter, rebuilding it when env config changes."""

    global _default_limiter, _default_signature
    signature = tuple(
        os.environ.get(name)
        for name in (
            "GEMINI_TPM_LIMIT", "GEMINI_RPM_LIMIT",
            "GEMINI_RATE_HEADROOM", "GEMINI_RATE_LIMIT_STATE_PATH",
            "GEMINI_MAX_RETRIES", "GEMINI_RETRY_BACKOFF_SECONDS",
        )
    )
    if _default_limiter is None or signature != _default_signature:
        _default_limiter = GeminiRateLimiter(
            logger=logger, sleep=sleep)
        _default_signature = signature
    elif logger is not None:
        _default_limiter._logger = logger
        _default_limiter._sleep = sleep
    return _default_limiter


def reset_default_limiter() -> None:
    """Test/support hook; production callers should use get_gemini_limiter."""

    global _default_limiter, _default_signature
    _default_limiter = None
    _default_signature = None


def _as_int_code(exc: BaseException) -> Optional[int]:
    for obj in (exc, getattr(exc, "response", None)):
        for name in ("code", "status_code"):
            value = getattr(obj, name, None)
            try:
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                pass
    return None


def _error_text(exc: BaseException) -> str:
    values = [str(exc)]
    for name in ("status", "message", "details"):
        value = getattr(exc, name, None)
        if value is not None:
            values.append(str(value))
    return " ".join(values)


def is_rate_limit_error(exc: BaseException) -> bool:
    code = _as_int_code(exc)
    if code == 429:
        return True
    # Invalid credentials and permission failures must never become a quota
    # retry loop merely because their provider message contains "quota".
    if code in (401, 403):
        return False
    text = _error_text(exc).upper()
    return bool(re.search(
        r"\b429\b|RESOURCE[_ ]EXHAUSTED|RATE[_ -]?LIMIT|TOO MANY REQUESTS|QUOTA .*EXCEEDED",
        text,
    ))


def is_retryable_error(exc: BaseException) -> bool:
    """Classify only temporary provider/response failures as retryable."""

    if is_rate_limit_error(exc):
        return True
    code = _as_int_code(exc)
    text = _error_text(exc).upper()
    if code in (400, 401, 403):
        return False
    if any(marker in text for marker in (
        "UNAUTHENTICATED", "INVALID API KEY", "API KEY NOT VALID",
        "PERMISSION_DENIED", "AUTHENTICATION", "INVALID_ARGUMENT",
    )):
        return False
    if code in (500, 502, 503, 504):
        return True
    return any(marker in text for marker in (
        "UNAVAILABLE", "INTERNAL", "OVERLOADED", "DEADLINE",
        "TIMED OUT", "TIMEOUT", "EMPTY RESPONSE BODY",
        "DID NOT CONTAIN A JSON OBJECT", "FAILED TO PARSE GEMINI JSON RESPONSE",
    ))


def _parse_delay(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|secs|seconds)?", raw, re.I)
    if match:
        return max(0.0, float(match.group(1)))
    return None


def _find_delay(value: Any, depth: int = 0) -> Optional[float]:
    if depth > 5:
        return None
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).replace("_", "").lower()
            if normalized in ("retryafter", "retrydelay", "retryin"):
                delay = _parse_delay(nested)
                if delay is not None:
                    return delay
            delay = _find_delay(nested, depth + 1)
            if delay is not None:
                return delay
    elif isinstance(value, (list, tuple)):
        for nested in value:
            delay = _find_delay(nested, depth + 1)
            if delay is not None:
                return delay
    return None


def retry_after_seconds(exc: BaseException, *, now: Optional[float] = None) -> Optional[float]:
    """Read Retry-After or Google's RetryInfo from the SDK error object."""

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        for key in ("retry-after", "Retry-After"):
            try:
                raw = headers.get(key)
            except AttributeError:
                raw = None
            delay = _parse_delay(raw)
            if delay is not None:
                return delay
            if raw:
                try:
                    stamp = email.utils.parsedate_to_datetime(str(raw)).timestamp()
                    return max(0.0, stamp - (time.time() if now is None else now))
                except (TypeError, ValueError, OverflowError):
                    pass

    for obj in (exc, getattr(exc, "details", None)):
        delay = _find_delay(obj)
        if delay is not None:
            return delay

    match = re.search(
        r"(?:RETRY(?:\s+IN|\s+AFTER)?|AFTER)\D{0,12}"
        r"([0-9]+(?:\.[0-9]+)?)\s*(?:S|SEC|SECONDS)",
        _error_text(exc).upper(),
    )
    return float(match.group(1)) if match else None


def _retry_delay(
    exc: BaseException,
    attempt: int,
    config: RateLimitConfig,
) -> float:
    server_delay = retry_after_seconds(exc)
    if server_delay is not None:
        return server_delay
    base = config.backoff_seconds * (2 ** max(0, attempt - 1))
    # Bounded jitter prevents simultaneous local jobs from retrying together.
    jitter = random.uniform(0.0, min(1.0, base * 0.25)) if base else 0.0
    return base + jitter


def call_with_retry(
    request: Callable[[], Any],
    *,
    label: str,
    estimated_tokens: int,
    handle_response: Optional[Callable[[Any], Any]] = None,
    max_retries: Optional[int] = None,
    non_retryable_exceptions: tuple[type[BaseException], ...] = (),
    logger: Optional[Callable[[str], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Budget, call, and safely retry one Gemini operation.

    ``handle_response`` is deliberately inside the retry loop.  The existing
    provider occasionally returned HTTP 200 with an empty body; parsing that
    response is part of the same safe retry contract as a transient HTTP error.
    """

    limiter = get_gemini_limiter(logger=logger, sleep=sleep)
    retries = limiter.config.max_retries if max_retries is None else max(0, int(max_retries))
    max_attempts = retries + 1
    log = logger or limiter._logger

    for attempt in range(1, max_attempts + 1):
        lease = limiter.acquire(estimated_tokens, label=label)
        response = None
        try:
            response = request()
            result = handle_response(response) if handle_response else response
            lease.finish(usage_tokens(response))
            return result
        except Exception as exc:
            actual = usage_tokens(response)
            explicitly_non_retryable = isinstance(exc, non_retryable_exceptions)
            retryable = not explicitly_non_retryable and is_retryable_error(exc)
            rate_limited = retryable and is_rate_limit_error(exc)
            if rate_limited:
                # A 429 may have consumed quota even when no response metadata
                # is available. Keep the conservative reservation in the shared
                # ledger until its 60-second window expires.
                lease.finish(actual)
            elif actual is not None and retryable:
                lease.finish(actual)
            else:
                lease.finish(discard=True)

            if not retryable or attempt >= max_attempts:
                raise

            delay = _retry_delay(exc, attempt, limiter.config)
            if rate_limited:
                log(
                    f"⚠️ Gemini returned 429 — retrying in {delay:.1f}s "
                    f"(attempt {attempt + 1}/{max_attempts})")
            else:
                log(
                    f"⚠️ Gemini transient error — retrying in {delay:.1f}s "
                    f"(attempt {attempt + 1}/{max_attempts}): {str(exc)[:150]}")
            sleep(delay)

    raise RuntimeError("unreachable Gemini retry state")
