"""OpenAI Responses API adapter for OpenShorts clip analysis.

It deliberately uses the already-installed ``httpx`` dependency instead of the
OpenAI SDK. The rest of OpenShorts consumes the same Pydantic schemas/prompts it
uses for Gemini, so switching providers changes only the model call, not clip
selection semantics, timestamp snapping, reframing, captions, or rendering.
"""
from __future__ import annotations

import base64
import copy
import json
import os
import random
import time
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable, Optional, Type

import httpx
from pydantic import BaseModel


DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

# USD per 1M tokens. Cost is informational only; provider billing remains the
# source of truth. Long-context multiplier is applied below when applicable.
MODEL_PRICES = {
    "gpt-5.6-luna": {
        "input": 0.20,
        "cached_input": 0.02,
        "output": 1.20,
    },
}


class OpenAIProviderError(RuntimeError):
    pass


class OpenAIBlockedError(OpenAIProviderError):
    """The model explicitly refused the supplied content."""


def _closed_schema(model: Type[BaseModel]) -> dict:
    """Return a strict JSON schema accepted by Responses Structured Outputs."""
    schema = copy.deepcopy(model.model_json_schema())

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                node["additionalProperties"] = False
                node["required"] = list(props.keys())
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    return schema


def _output_text(data: dict) -> str:
    """Extract text from a raw Responses API response and surface refusals."""
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    chunks = []
    for item in data.get("output") or []:
        for part in item.get("content") or []:
            ptype = str(part.get("type") or "")
            if ptype == "refusal":
                refusal = part.get("refusal") or part.get("text") or "content refused"
                raise OpenAIBlockedError(f"OpenAI refused this content: {refusal}")
            if ptype in ("output_text", "text") and part.get("text"):
                chunks.append(str(part["text"]))
    text = "\n".join(chunks).strip()
    if not text:
        status = data.get("status")
        incomplete = (data.get("incomplete_details") or {}).get("reason")
        suffix = f" status={status!r}" if status else ""
        if incomplete:
            suffix += f" incomplete_reason={incomplete!r}"
        raise OpenAIProviderError(f"OpenAI returned no structured output.{suffix}")
    return text


def _retry_after_seconds(response: Any) -> Optional[float]:
    value = (getattr(response, "headers", None) or {}).get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(value)
            now = parsedate_to_datetime(parsedate_to_datetime(time.strftime(
                "%a, %d %b %Y %H:%M:%S GMT", time.gmtime()
            )).strftime("%a, %d %b %Y %H:%M:%S GMT"))
            return max(0.0, (target - now).total_seconds())
        except Exception:
            return None


def _calculate_cost_analysis(data: dict, model_name: str) -> Optional[dict]:
    usage = data.get("usage") or {}
    if not usage:
        return None

    prices = MODEL_PRICES.get(model_name)
    estimated = prices is None
    if prices is None:
        # Conservative fallback for an operator-selected unknown OpenAI model.
        prices = {"input": 1.00, "cached_input": 0.10, "output": 5.00}

    input_tokens = int(usage.get("input_tokens") or 0)
    input_details = usage.get("input_tokens_details") or {}
    cached_tokens = min(input_tokens, int(input_details.get("cached_tokens") or 0))
    uncached_tokens = max(0, input_tokens - cached_tokens)
    output_tokens = int(usage.get("output_tokens") or 0)
    output_details = usage.get("output_tokens_details") or {}
    reasoning_tokens = int(output_details.get("reasoning_tokens") or 0)

    input_rate = float(prices["input"])
    cached_rate = float(prices["cached_input"])
    output_rate = float(prices["output"])
    long_context = input_tokens > 272_000 and model_name.startswith("gpt-5.6")
    if long_context:
        # GPT-5.6 long-context requests use 2x input and 1.5x output pricing.
        input_rate *= 2.0
        cached_rate *= 2.0
        output_rate *= 1.5

    input_cost = (uncached_tokens / 1_000_000) * input_rate
    cached_input_cost = (cached_tokens / 1_000_000) * cached_rate
    output_cost = (output_tokens / 1_000_000) * output_rate
    return {
        "provider": "openai",
        "model": model_name,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "input_cost": input_cost + cached_input_cost,
        "output_cost": output_cost,
        "total_cost": input_cost + cached_input_cost + output_cost,
        "price_estimated": estimated,
        "long_context_pricing": long_context,
    }


def _request_body(
    model_name: str,
    contents: list,
    schema: Type[BaseModel],
    schema_name: str,
    reasoning_effort: str,
) -> dict:
    return {
        "model": model_name,
        "input": [{"role": "user", "content": contents}],
        "reasoning": {"effort": reasoning_effort},
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name[:64],
                "strict": True,
                "schema": _closed_schema(schema),
            }
        },
        "max_output_tokens": 12_000,
        "store": False,
    }


def _post_with_retry(
    api_key: str,
    body: dict,
    *,
    base_url: Optional[str] = None,
    max_attempts: int = 4,
    client: Any = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict:
    url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/responses"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    owned = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(180.0, connect=20.0))
    try:
        for attempt in range(1, max_attempts + 1):
            try:
                response = http.post(url, headers=headers, json=body)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= max_attempts:
                    raise OpenAIProviderError(f"OpenAI network error: {exc}") from exc
                wait = min(30.0, 2.0 * (2 ** (attempt - 1))) + random.uniform(0, 0.35)
                print(f"⚠️ OpenAI network error — retrying in {wait:.1f}s (attempt {attempt}/{max_attempts})")
                sleep_fn(wait)
                continue

            status = int(response.status_code)
            if 200 <= status < 300:
                try:
                    return response.json()
                except Exception as exc:
                    if attempt >= max_attempts:
                        raise OpenAIProviderError("OpenAI returned invalid JSON.") from exc
                    wait = min(30.0, 2.0 * (2 ** (attempt - 1)))
                    sleep_fn(wait)
                    continue

            transient = status == 429 or status in (408, 409, 500, 502, 503, 504)
            try:
                err_data = response.json()
                message = ((err_data.get("error") or {}).get("message")
                           or response.text or f"HTTP {status}")
            except Exception:
                message = getattr(response, "text", "") or f"HTTP {status}"

            if not transient or attempt >= max_attempts:
                raise OpenAIProviderError(f"OpenAI HTTP {status}: {str(message)[:500]}")

            retry_after = _retry_after_seconds(response)
            wait = retry_after if retry_after is not None else min(
                45.0, 3.0 * (2 ** (attempt - 1)) + random.uniform(0, 0.75))
            print(f"⚠️ OpenAI transient HTTP {status} — retrying in {wait:.1f}s (attempt {attempt}/{max_attempts})")
            sleep_fn(wait)
    finally:
        if owned:
            http.close()

    raise OpenAIProviderError("OpenAI request failed after retries.")


def run_stage(
    api_key: str,
    model_name: str,
    prompt: str,
    schema: Type[BaseModel],
    *,
    base_url: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    max_attempts: int = 4,
    client: Any = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[dict, Optional[dict]]:
    """Run one text-only score/detail stage with strict structured output."""
    effort = (reasoning_effort or os.getenv("OPENAI_REASONING_EFFORT") or "none").strip().lower()
    if effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
        effort = "none"
    body = _request_body(
        model_name,
        [{"type": "input_text", "text": prompt}],
        schema,
        schema.__name__,
        effort,
    )
    data = _post_with_retry(
        api_key, body, base_url=base_url or os.getenv("OPENAI_BASE_URL"),
        max_attempts=max_attempts, client=client, sleep_fn=sleep_fn)
    parsed = json.loads(_output_text(data))
    validated = schema.model_validate(parsed).model_dump()
    return validated, _calculate_cost_analysis(data, model_name)


def run_image_stage(
    api_key: str,
    model_name: str,
    prompt: str,
    images: Iterable[bytes],
    schema: Type[BaseModel],
    *,
    base_url: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    max_attempts: int = 4,
    client: Any = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[dict, Optional[dict]]:
    """Run a sampled-frame vision stage (used by AUTO_LAYOUT)."""
    contents = [{"type": "input_text", "text": prompt}]
    for image in images:
        encoded = base64.b64encode(image).decode("ascii")
        contents.append({
            "type": "input_image",
            "image_url": f"data:image/jpeg;base64,{encoded}",
            "detail": "low",
        })
    effort = (reasoning_effort or os.getenv("OPENAI_REASONING_EFFORT") or "none").strip().lower()
    if effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
        effort = "none"
    body = _request_body(model_name, contents, schema, schema.__name__, effort)
    data = _post_with_retry(
        api_key, body, base_url=base_url or os.getenv("OPENAI_BASE_URL"),
        max_attempts=max_attempts, client=client, sleep_fn=sleep_fn)
    parsed = json.loads(_output_text(data))
    validated = schema.model_validate(parsed).model_dump()
    return validated, _calculate_cost_analysis(data, model_name)
