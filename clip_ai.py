"""Provider-agnostic transcript clip selection.

This module owns the two-pass score -> detail algorithm while keeping the
existing OpenShorts prompts and Pydantic output contracts. ``main.py`` only has
to call :func:`get_viral_clips`; everything downstream receives the same
``shorts`` dictionaries regardless of Gemini vs OpenAI.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from google import genai
from google.genai import types as genai_types

import gemini_rate_limiter
import gemini_worker
import openai_worker
from ai_provider import normalize_provider, spec_for
from clip_selection import (build_transcript_windows, clip_count_targets,
                            clip_duration_bounds, snap_clip_to_words)


def _run_gemini_stage(client, model_name, prompt, schema, label="Gemini analysis"):
    """One Gemini stage using the shared cross-process rate limiter."""
    config = genai_types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
    )
    response_holder = {}

    def _handle_response(response):
        response_holder["response"] = response
        gemini_worker.raise_if_blocked(response)
        parsed_obj = getattr(response, "parsed", None)
        if parsed_obj is not None:
            return (parsed_obj.model_dump()
                    if hasattr(parsed_obj, "model_dump") else parsed_obj)
        return gemini_worker._parse_json_response_text(
            gemini_worker._get_response_text(response))

    parsed = gemini_rate_limiter.call_with_retry(
        lambda: client.models.generate_content(
            model=model_name, contents=prompt, config=config),
        label=label,
        estimated_tokens=gemini_rate_limiter.estimate_tokens(prompt),
        handle_response=_handle_response,
        non_retryable_exceptions=(gemini_worker.GeminiBlockedError,),
    )
    response = response_holder.get("response")
    cost = gemini_worker._calculate_cost_analysis(response, model_name)
    if cost:
        cost = {**cost, "provider": "gemini"}
    return parsed, cost


class _ProviderRunner:
    def __init__(self, provider: str):
        self.provider = normalize_provider(provider)
        self.spec = spec_for(self.provider)
        self.api_key = os.getenv(self.spec.key_env)
        self.model_name = os.getenv(self.spec.model_env) or self.spec.default_model
        self.client = None
        if not self.api_key:
            raise RuntimeError(
                f"{self.spec.label} selected but {self.spec.key_env} is not configured.")
        if self.provider == "gemini":
            self.client = genai.Client(api_key=self.api_key)

    def run(self, prompt, schema, label):
        if self.provider == "gemini":
            return _run_gemini_stage(
                self.client, self.model_name, prompt, schema, label=label)
        return openai_worker.run_stage(
            self.api_key,
            self.model_name,
            prompt,
            schema,
            base_url=os.getenv("OPENAI_BASE_URL"),
            reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT", "none"),
        )


def get_viral_clips(transcript_result, video_duration, provider: Optional[str] = None):
    """Two-pass text clip selection with Gemini or OpenAI.

    Both providers receive the SAME prompts and schemas, so an A/B test compares
    model decisions rather than two different selection algorithms.
    """
    provider_name = normalize_provider(provider or os.getenv("AI_PROVIDER") or "gemini")
    runner = _ProviderRunner(provider_name)
    language = str(transcript_result.get("language") or "unknown")
    print(f"🤖 Analyzing with {runner.spec.label} (2-pass: score → detail)...")
    print(f"🤖 Model: {runner.model_name} | language: {language}")

    words = []
    for segment in transcript_result.get("segments", []):
        for word in segment.get("words", []):
            words.append({"w": word["word"], "s": word["start"], "e": word["end"]})

    try:
        min_secs, max_secs = clip_duration_bounds()
        windows = build_transcript_windows(
            transcript_result,
            video_duration,
            window_seconds=max(90, int(max_secs * 1.5)),
        )
        print(f"   Built {len(windows)} scoring window(s).")
        costs = []

        scored = []
        score_batch = 8
        for start in range(0, len(windows), score_batch):
            batch = windows[start:start + score_batch]
            payload = [
                {"id": w["id"], "start": w["start"], "end": w["end"], "text": w["text"]}
                for w in batch
            ]
            prompt = gemini_worker.SCORE_PROMPT_TEMPLATE.format(
                video_duration=video_duration,
                language=language,
                windows_json=json.dumps(payload, ensure_ascii=False),
            )
            parsed, cost = runner.run(
                prompt,
                gemini_worker.ScoreResponse,
                f"transcript scoring batch {start // score_batch + 1}",
            )
            if cost:
                costs.append(cost)
            scored.extend(parsed.get("windows") or [])

        scored.sort(key=lambda w: w.get("score", 0), reverse=True)
        target = max(3, min(10, int(video_duration // 90) + 2))
        by_id = {w["id"]: w for w in windows}
        shortlist = [
            by_id[w["id"]] for w in scored[:target]
            if w.get("id") in by_id
        ]
        if not shortlist:
            shortlist = windows[:target]
        print(f"   Shortlisted {len(shortlist)} window(s) for detail.")

        payload = [
            {"id": w["id"], "start": w["start"], "end": w["end"], "text": w["text"]}
            for w in shortlist
        ]
        min_clips, max_clips = clip_count_targets(len(shortlist))
        prompt = gemini_worker.DETAIL_PROMPT_TEMPLATE.format(
            video_duration=video_duration,
            language=language,
            min_clips=min_clips,
            max_clips=max_clips,
            min_secs=min_secs,
            max_secs=max_secs,
            windows_json=json.dumps(payload, ensure_ascii=False),
        )
        detail, cost = runner.run(
            prompt, gemini_worker.DetailResponse, "transcript detail selection")
        if cost:
            costs.append(cost)

        shorts = detail.get("shorts") or []
        for short in shorts:
            new_start, new_end = snap_clip_to_words(
                short.get("start", 0), short.get("end", 0), words, video_duration,
                min_duration=min_secs, max_duration=max_secs,
            )
            short["start"], short["end"] = new_start, new_end

        if not shorts:
            print(f"⚠️ {runner.spec.label} 2-pass selection returned no clips.")
            return None

        cost_analysis = None
        if costs:
            cost_analysis = {
                "provider": provider_name,
                "model": runner.model_name,
                "input_tokens": sum(c.get("input_tokens", 0) for c in costs),
                "cached_input_tokens": sum(c.get("cached_input_tokens", 0) for c in costs),
                "output_tokens": sum(c.get("output_tokens", 0) for c in costs),
                "reasoning_tokens": sum(c.get("reasoning_tokens", 0) for c in costs),
                "total_cost": sum(c.get("total_cost", 0) for c in costs),
            }
            print(f"💰 Total estimated cost ({provider_name}/{runner.model_name}, "
                  f"{len(costs)} calls): ${cost_analysis['total_cost']:.6f}")

        result = {
            "shorts": shorts,
            "ai_provider": provider_name,
            "ai_model": runner.model_name,
        }
        if cost_analysis:
            result["cost_analysis"] = cost_analysis
        return result
    except (gemini_worker.GeminiBlockedError, openai_worker.OpenAIBlockedError):
        raise
    except Exception as exc:
        print(f"❌ {runner.spec.label} clip-analysis error: {exc}")
        return None
