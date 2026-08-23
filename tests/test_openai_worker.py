import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import openai_worker


class Item(BaseModel):
    score: int


class Payload(BaseModel):
    items: list[Item]


class FakeResponse:
    def __init__(self, status_code=200, data=None, headers=None, text=""):
        self.status_code = status_code
        self._data = data or {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, headers=None, json=None):
        self.calls.append((url, headers, json))
        return self.responses.pop(0)


def success_data(payload=None, *, input_tokens=100, cached_tokens=20, output_tokens=30):
    payload = payload or {"items": [{"score": 91}]}
    return {
        "status": "completed",
        "output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": json.dumps(payload)}],
        }],
        "usage": {
            "input_tokens": input_tokens,
            "input_tokens_details": {"cached_tokens": cached_tokens},
            "output_tokens": output_tokens,
            "output_tokens_details": {"reasoning_tokens": 5},
        },
    }


def test_structured_output_request_and_validation():
    client = FakeClient([FakeResponse(data=success_data())])
    parsed, cost = openai_worker.run_stage(
        "sk-test", "gpt-5.6-luna", "score this", Payload,
        client=client, sleep_fn=lambda _: None,
    )
    assert parsed == {"items": [{"score": 91}]}
    assert cost["provider"] == "openai"
    assert cost["model"] == "gpt-5.6-luna"
    assert cost["cached_input_tokens"] == 20
    assert client.calls[0][2]["text"]["format"]["type"] == "json_schema"
    assert client.calls[0][2]["text"]["format"]["strict"] is True
    assert client.calls[0][2]["reasoning"]["effort"] == "none"
    assert client.calls[0][2]["store"] is False


def test_429_retries_and_respects_retry_after():
    waits = []
    client = FakeClient([
        FakeResponse(429, {"error": {"message": "slow down"}}, {"retry-after": "7"}),
        FakeResponse(data=success_data()),
    ])
    parsed, _ = openai_worker.run_stage(
        "sk-test", "gpt-5.6-luna", "x", Payload,
        client=client, sleep_fn=waits.append,
    )
    assert parsed["items"][0]["score"] == 91
    assert len(client.calls) == 2
    assert waits == [7.0]


def test_permanent_auth_error_is_not_retried():
    client = FakeClient([
        FakeResponse(401, {"error": {"message": "bad key"}}),
        FakeResponse(data=success_data()),
    ])
    with pytest.raises(openai_worker.OpenAIProviderError, match="401"):
        openai_worker.run_stage(
            "bad", "gpt-5.6-luna", "x", Payload,
            client=client, sleep_fn=lambda _: None,
        )
    assert len(client.calls) == 1


def test_refusal_is_explicit():
    data = {
        "status": "completed",
        "output": [{"type": "message", "content": [
            {"type": "refusal", "refusal": "cannot comply"}
        ]}],
        "usage": {"input_tokens": 10, "output_tokens": 1},
    }
    client = FakeClient([FakeResponse(data=data)])
    with pytest.raises(openai_worker.OpenAIBlockedError):
        openai_worker.run_stage(
            "sk-test", "gpt-5.6-luna", "x", Payload,
            client=client, sleep_fn=lambda _: None,
        )


def test_luna_cost_uses_cached_input_discount():
    data = success_data(input_tokens=1_000_000, cached_tokens=500_000, output_tokens=100_000)
    cost = openai_worker._calculate_cost_analysis(data, "gpt-5.6-luna")
    # This request exceeds the GPT-5.6 long-context threshold, so the estimator
    # applies 2x input/cached-input and 1.5x output rates.
    expected = (0.5 * 0.40) + (0.5 * 0.04) + (0.1 * 1.80)
    assert cost["long_context_pricing"] is True
    assert cost["total_cost"] == pytest.approx(expected)


def test_strict_schema_closes_nested_objects():
    schema = openai_worker._closed_schema(Payload)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["items"]
    item_def = schema["$defs"]["Item"]
    assert item_def["additionalProperties"] is False
    assert item_def["required"] == ["score"]
