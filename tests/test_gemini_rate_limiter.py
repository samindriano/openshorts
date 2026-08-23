"""Focused tests for the shared Gemini budget and retry contract."""

import threading
import types

import pytest

import gemini_rate_limiter as limiter_module


class _RateLimitError(RuntimeError):
    code = 429

    def __init__(self, retry_after=None):
        super().__init__("429 RESOURCE_EXHAUSTED")
        self.details = {"retryDelay": retry_after} if retry_after is not None else {}


class _AuthError(RuntimeError):
    code = 401


class _Response:
    usage_metadata = None


def _config(tmp_path, *, tpm=None, rpm=None):
    return limiter_module.RateLimitConfig(
        tpm_limit=tpm,
        rpm_limit=rpm,
        headroom=1.0,
        state_path=str(tmp_path / "gemini-ledger.json"),
        max_retries=5,
        backoff_seconds=1.0,
    )


def _install(monkeypatch, limiter):
    monkeypatch.setattr(
        limiter_module, "get_gemini_limiter", lambda **_kwargs: limiter)


def test_config_applies_safety_headroom(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_TPM_LIMIT", "250000")
    monkeypatch.setenv("GEMINI_RPM_LIMIT", "15")
    monkeypatch.setenv("GEMINI_RATE_HEADROOM", "0.90")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_STATE_PATH", str(tmp_path / "state.json"))

    config = limiter_module.RateLimitConfig.from_env()

    assert config.tpm_limit == 225000
    assert config.rpm_limit == 13.5


def test_missing_limits_pass_through_without_breaking_retries(monkeypatch, tmp_path):
    sleeps = []
    limiter = limiter_module.GeminiRateLimiter(
        _config(tmp_path), sleep=sleeps.append)
    _install(monkeypatch, limiter)

    assert limiter_module.call_with_retry(
        lambda: "ok",
        label="score",
        estimated_tokens=1000000,
        sleep=sleeps.append,
    ) == "ok"
    assert sleeps == []


def test_normal_request_succeeds_without_delay(monkeypatch, tmp_path):
    sleeps = []
    limiter = limiter_module.GeminiRateLimiter(
        _config(tmp_path, tpm=1000, rpm=10), sleep=sleeps.append)
    _install(monkeypatch, limiter)
    calls = []

    result = limiter_module.call_with_retry(
        lambda: calls.append("request") or _Response(),
        label="score",
        estimated_tokens=10,
        sleep=sleeps.append,
    )

    assert isinstance(result, _Response)
    assert calls == ["request"]
    assert sleeps == []


def test_429_uses_provider_retry_delay_and_then_succeeds(monkeypatch, tmp_path):
    sleeps = []
    limiter = limiter_module.GeminiRateLimiter(
        _config(tmp_path, tpm=1000, rpm=10), sleep=sleeps.append)
    _install(monkeypatch, limiter)
    calls = []

    def request():
        calls.append(1)
        if len(calls) == 1:
            raise _RateLimitError("7s")
        return _Response()

    assert isinstance(limiter_module.call_with_retry(
        request,
        label="detail",
        estimated_tokens=10,
        sleep=sleeps.append,
    ), _Response)
    assert calls == [1, 1]
    assert sleeps == [7.0]


def test_429_uses_retry_after_header(monkeypatch, tmp_path):
    sleeps = []
    limiter = limiter_module.GeminiRateLimiter(
        _config(tmp_path, tpm=1000, rpm=10), sleep=sleeps.append)
    _install(monkeypatch, limiter)
    calls = [0]

    error = _RateLimitError()
    error.response = types.SimpleNamespace(headers={"Retry-After": "11"})

    def request():
        calls[0] += 1
        if calls[0] == 1:
            raise error
        return "ok"

    assert limiter_module.call_with_retry(
        request,
        label="score",
        estimated_tokens=10,
        sleep=sleeps.append,
    ) == "ok"
    assert sleeps == [11.0]


def test_persistent_429_is_bounded(monkeypatch, tmp_path):
    sleeps = []
    limiter = limiter_module.GeminiRateLimiter(
        _config(tmp_path, tpm=1000, rpm=10), sleep=sleeps.append)
    _install(monkeypatch, limiter)
    calls = []

    with pytest.raises(_RateLimitError):
        limiter_module.call_with_retry(
            lambda: calls.append(1) or (_ for _ in ()).throw(_RateLimitError("1s")),
            label="score",
            estimated_tokens=10,
            max_retries=2,
            sleep=sleeps.append,
        )

    assert len(calls) == 3
    assert sleeps == [1.0, 1.0]


def test_authentication_error_is_not_retried(monkeypatch, tmp_path):
    sleeps = []
    limiter = limiter_module.GeminiRateLimiter(
        _config(tmp_path, tpm=1000, rpm=10), sleep=sleeps.append)
    _install(monkeypatch, limiter)
    calls = []

    with pytest.raises(_AuthError):
        limiter_module.call_with_retry(
            lambda: calls.append(1) or (_ for _ in ()).throw(_AuthError("invalid key")),
            label="score",
            estimated_tokens=10,
            sleep=sleeps.append,
        )

    assert len(calls) == 1
    assert sleeps == []


def test_two_large_requests_wait_for_the_tpm_window(monkeypatch, tmp_path):
    clock = [1000.0]
    waits = []
    monkeypatch.setattr(limiter_module.time, "time", lambda: clock[0])

    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    limiter = limiter_module.GeminiRateLimiter(
        _config(tmp_path, tpm=100), sleep=sleep)
    first = limiter.acquire(80, label="first")
    second = limiter.acquire(80, label="second")
    first.finish(80)
    second.finish(80)

    assert waits and waits[0] >= 59.0
    events = limiter_module._read_events(str(tmp_path / "gemini-ledger.json"))
    assert len(events) == 1
    assert events[0]["tokens"] == 80


def test_concurrent_local_jobs_share_one_limiter(monkeypatch, tmp_path):
    clock = [2000.0]
    waits = []
    clock_lock = threading.Lock()
    monkeypatch.setattr(limiter_module.time, "time", lambda: clock[0])

    def sleep(seconds):
        with clock_lock:
            waits.append(seconds)
            clock[0] += seconds

    limiter_a = limiter_module.GeminiRateLimiter(
        _config(tmp_path, tpm=100), sleep=sleep)
    limiter_b = limiter_module.GeminiRateLimiter(
        _config(tmp_path, tpm=100), sleep=sleep)

    def worker(limiter):
        lease = limiter.acquire(80, label="job")
        lease.finish(80)

    threads = [
        threading.Thread(target=worker, args=(limiter_a,)),
        threading.Thread(target=worker, args=(limiter_b,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert waits and waits[0] >= 59.0


def test_retry_does_not_repeat_completed_prior_stage(monkeypatch, tmp_path):
    sleeps = []
    limiter = limiter_module.GeminiRateLimiter(
        _config(tmp_path, tpm=1000, rpm=10), sleep=sleeps.append)
    _install(monkeypatch, limiter)
    completed = []
    detail_calls = [0]

    completed.append(limiter_module.call_with_retry(
        lambda: "score-complete",
        label="score",
        estimated_tokens=10,
        sleep=sleeps.append,
    ))

    def detail_request():
        detail_calls[0] += 1
        if detail_calls[0] == 1:
            raise _RateLimitError("1s")
        return "detail-complete"

    completed.append(limiter_module.call_with_retry(
        detail_request,
        label="detail",
        estimated_tokens=10,
        sleep=sleeps.append,
    ))

    assert completed == ["score-complete", "detail-complete"]
    assert detail_calls == [2]
    assert sleeps == [1.0]
