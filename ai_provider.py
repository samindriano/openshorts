"""Shared configuration for the clip-analysis AI provider.

The video pipeline is provider-agnostic after clip timestamps/copy are chosen.
This module keeps provider names, request headers and model defaults in one
small dependency-free place so app.py, main.py and tests agree on the contract.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    label: str
    key_env: str
    key_header: str
    model_env: str
    default_model: str


PROVIDERS = {
    "gemini": ProviderSpec(
        name="gemini",
        label="Gemini",
        key_env="GEMINI_API_KEY",
        key_header="X-Gemini-Key",
        model_env="GEMINI_MODEL",
        default_model="gemini-3.1-flash-lite",
    ),
    "openai": ProviderSpec(
        name="openai",
        label="OpenAI",
        key_env="OPENAI_API_KEY",
        key_header="X-OpenAI-Key",
        model_env="OPENAI_MODEL",
        default_model="gpt-5.6-luna",
    ),
}

ALIASES = {
    "google": "gemini",
    "gpt": "openai",
    "luna": "openai",
}


def normalize_provider(value: Optional[str], default: str = "gemini") -> str:
    """Return the canonical provider name or raise ValueError.

    The API intentionally accepts a couple of human-friendly aliases while all
    persisted/job state uses only ``gemini`` or ``openai``.
    """
    raw = str(value or default).strip().lower()
    raw = ALIASES.get(raw, raw)
    if raw not in PROVIDERS:
        allowed = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unsupported AI provider {value!r}; expected one of: {allowed}")
    return raw


def spec_for(value: Optional[str]) -> ProviderSpec:
    return PROVIDERS[normalize_provider(value)]
