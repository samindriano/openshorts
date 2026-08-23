import pytest

from ai_provider import normalize_provider, spec_for


def test_provider_defaults_to_gemini():
    assert normalize_provider(None) == "gemini"


def test_provider_aliases_are_normalized():
    assert normalize_provider("google") == "gemini"
    assert normalize_provider("gpt") == "openai"
    assert normalize_provider("luna") == "openai"


def test_unknown_provider_fails_closed():
    with pytest.raises(ValueError):
        normalize_provider("mystery-ai")


def test_openai_spec_uses_luna_by_default():
    spec = spec_for("openai")
    assert spec.key_header == "X-OpenAI-Key"
    assert spec.key_env == "OPENAI_API_KEY"
    assert spec.default_model == "gpt-5.6-luna"
