"""Tests for persistent httpx.Client reuse in the NutriBench cloud runner and provider."""

from __future__ import annotations

import json
import sys

import nutribench_provider as provider_mod
import nutribench_runner_cloud as cloud_runner
import pytest


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


class _RecordingClient:
    def __init__(self, contents: list[str] | None = None) -> None:
        self._contents = list(contents or ['{"energy": 400, "protein": 30, "carb": 40, "fat": 12}'])
        self.calls: list[dict] = []
        self.closed = False

    @property
    def is_closed(self) -> bool:
        return self.closed

    def post(self, url: str, *, headers: dict, json: dict, timeout: float) -> _FakeResponse:
        idx = min(len(self.calls), len(self._contents) - 1)
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _FakeResponse(self._contents[idx])

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> _RecordingClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


@pytest.fixture(autouse=True)
def _reset_shared_http_client():
    cloud_runner.close_http_client()
    yield
    cloud_runner.close_http_client()


def test_cloud_generate_reuses_injected_client_across_calls_and_retry():
    client = _RecordingClient(
        contents=[
            "not valid json",
            '{"energy": 500, "protein": 40, "carb": 45, "fat": 15}',
            '{"energy": 300, "protein": 20, "carb": 35, "fat": 8}',
        ]
    )
    provider = cloud_runner.PROVIDERS["openai"]

    parsed1, _, _ = cloud_runner.cloud_generate(
        provider=provider,
        model=provider.default_model,
        api_key="test-key",
        meal_description="chicken and rice",
        client=client,
    )
    parsed2, _, _ = cloud_runner.cloud_generate(
        provider=provider,
        model=provider.default_model,
        api_key="test-key",
        meal_description="oatmeal with berries",
        client=client,
    )

    assert parsed1 == {"energy": 500, "protein": 40, "carb": 45, "fat": 15}
    assert parsed2 == {"energy": 300, "protein": 20, "carb": 35, "fat": 8}
    assert len(client.calls) == 3
    assert client.closed is False


def test_cloud_generate_uses_shared_client_and_closes_cleanly(monkeypatch):
    created: list[_RecordingClient] = []

    def make_client() -> _RecordingClient:
        c = _RecordingClient()
        created.append(c)
        return c

    monkeypatch.setattr(cloud_runner.httpx, "Client", make_client)
    provider = cloud_runner.PROVIDERS["openai"]

    for meal in ("toast and eggs", "greek yogurt"):
        parsed, _, _ = cloud_runner.cloud_generate(
            provider=provider,
            model=provider.default_model,
            api_key="test-key",
            meal_description=meal,
        )
        assert parsed is not None

    assert len(created) == 1
    assert len(created[0].calls) == 2
    assert created[0].closed is False

    cloud_runner.close_http_client()
    assert created[0].closed is True


def test_cloud_runner_main_reuses_one_client_and_closes_on_exit(monkeypatch, capsys):
    created: list[_RecordingClient] = []

    def make_client() -> _RecordingClient:
        c = _RecordingClient()
        created.append(c)
        return c

    monkeypatch.setattr(cloud_runner.httpx, "Client", make_client)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        cloud_runner,
        "load_split",
        lambda split, max_rows: [
            {"meal_description": f"meal {i}", "energy": 400.0, "protein": 30.0, "carb": 40.0, "fat": 12.0}
            for i in range(3)
        ],
    )
    monkeypatch.setattr(
        sys, "argv", ["nutribench_runner_cloud.py", "--provider", "openai", "--max-rows", "3"]
    )

    assert cloud_runner.main() == 0
    assert len(created) == 1
    assert len(created[0].calls) == 3
    assert created[0].closed is True
    assert "parse rate: 100.0%  (3/3)" in capsys.readouterr().out


def test_provider_cloud_call_reuses_shared_client_across_rows(monkeypatch):
    created: list[_RecordingClient] = []

    def make_client() -> _RecordingClient:
        c = _RecordingClient(contents=[json.dumps({"energy": 410, "protein": 31, "carb": 42, "fat": 11})])
        created.append(c)
        return c

    monkeypatch.setattr(cloud_runner.httpx, "Client", make_client)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    options = {"config": {"kind": "cloud", "provider": "openai", "model": "gpt-4o-mini"}}
    res1 = provider_mod.call_api("", options, {"vars": {"meal_description": "salad bowl"}})
    res2 = provider_mod.call_api("", options, {"vars": {"meal_description": "salmon fillet"}})

    assert "error" not in res1
    assert "error" not in res2
    assert len(created) == 1
    assert len(created[0].calls) == 2


def test_provider_cloud_call_accepts_injected_client(monkeypatch):
    client = _RecordingClient()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    text, _ = provider_mod._cloud_call(
        provider_name="openai",
        model="gpt-4o-mini",
        meal="bowl of chili",
        thinking=False,
        client=client,
    )

    assert json.loads(text)["energy"] == 400
    assert len(client.calls) == 1
