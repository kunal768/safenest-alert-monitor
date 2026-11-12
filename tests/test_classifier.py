import json

import httpx
import pytest

from ai.classifier import ClassifyResult, MockAdapter, OllamaAdapter, _extract_json


@pytest.mark.asyncio
async def test_mock_glass_break():
    r = await MockAdapter().classify(
        "Sharp crash of shattering glass followed by two seconds of silence", "mock"
    )
    assert r.label == "glass_break"
    assert 0 <= r.confidence <= 1
    assert r.reason


@pytest.mark.asyncio
async def test_mock_unclassified():
    r = await MockAdapter().classify("zzzz unintelligible sensor burst", "mock")
    assert r.unclassified


def test_json_extract_and_clamp():
    data = _extract_json('noise {"label": "fall", "confidence": 1.4, "reason": "ok"} trailing')
    r = ClassifyResult.model_validate(data)
    assert r.label == "fall"
    assert r.confidence == 1.0


def test_invalid_label_rejected():
    with pytest.raises(Exception):
        ClassifyResult.model_validate({"label": "explosion", "confidence": 0.9, "reason": "nope"})


@pytest.mark.asyncio
async def test_ollama_adapter_timeout_unclassified(monkeypatch):
    async def boom(*_a, **_k):
        raise httpx.ConnectError("down")

    adapter = OllamaAdapter(timeout=0.2)
    monkeypatch.setattr(adapter, "_chat", boom)
    r = await adapter.classify("glass smash", "llama3.2:3b")
    assert r.unclassified


@pytest.mark.asyncio
async def test_ollama_repair_then_ok(monkeypatch):
    calls = {"n": 0}

    async def chat(model, description, repair=False):
        calls["n"] += 1
        if calls["n"] == 1:
            return "not json"
        return json.dumps({"label": "cough", "confidence": 0.9, "reason": "cough"})

    adapter = OllamaAdapter(timeout=2)
    monkeypatch.setattr(adapter, "_chat", chat)
    r = await adapter.classify("a cough", "x")
    assert r.label == "cough"
    assert not r.unclassified
    assert calls["n"] >= 2


@pytest.mark.ollama
@pytest.mark.asyncio
async def test_live_ollama_five_descriptions():
    """make test-ollama  (OLLAMA_TEST_MODEL=qwen2:7b to use another pulled model)."""
    import os

    model = os.environ.get("OLLAMA_TEST_MODEL", "llama3.2:3b")
    adapter = OllamaAdapter(timeout=60)
    samples = [
        ("Sharp crash of shattering glass followed by two seconds of silence", "glass_break"),
        ("A dry cough repeated three times", "cough"),
        ("someone is dragging furniture and laughing", "normal"),
        ("Heavy thud then a groan as if someone fell in the bathroom", "fall"),
        ("Front door latch clicking open", "door_open"),
    ]
    for desc, expected in samples:
        r = await adapter.classify(desc, model)
        assert not r.unclassified, r.error
        assert r.label == expected, f"{desc!r}: {r.label} ({r.reason})"


def test_user_message_includes_room_context():
    from ai.classifier import build_user_message

    msg = build_user_message("glass smash", {"Room": "Kitchen", "Level": "92 dB", "Duration": None})
    assert msg == "Sound description: glass smash\nContext (not evidence of the label): room Kitchen, level 92 dB"


@pytest.mark.asyncio
async def test_ollama_chat_payload_via_httpx_mock(monkeypatch):
    """Exercises the real HTTP path against an httpx MockTransport standing in for Ollama."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": '{"label":"glass_break","confidence":0.97,"reason":"Glass shattering"}'}})

    real = httpx.AsyncClient
    monkeypatch.setattr("ai.classifier.httpx.AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    r = await OllamaAdapter(base_url="http://ollama.test").classify("glass smash", "llama3.2:3b", {"Room": "Kitchen"})
    assert r.label == "glass_break" and r.model_version == "llama3.2:3b" and r.prompt_version
    assert seen["url"] == "http://ollama.test/api/chat"
    body = seen["body"]
    assert body["stream"] is False and body["format"] == "json" and body["options"]["temperature"] == 0
    assert body["messages"][0]["role"] == "system" and "room Kitchen" in body["messages"][1]["content"]


@pytest.mark.asyncio
async def test_invalid_label_twice_then_unclassified(monkeypatch):
    async def chat(model, description, repair=False):
        return json.dumps({"label": "explosion", "confidence": 0.9, "reason": "x"})

    adapter = OllamaAdapter(timeout=1)
    monkeypatch.setattr(adapter, "_chat", chat)
    r = await adapter.classify("bang", "x")
    assert r.unclassified and "invalid label" in r.error


def test_outage_switch_selects_failing_adapter():
    from ai.classifier import DEAD_OLLAMA_URL, OutageAdapter, get_adapter
    from app.config import runtime

    runtime["ai_outage"] = True
    assert isinstance(get_adapter("mock"), OutageAdapter)
    assert get_adapter("ollama").base_url == DEAD_OLLAMA_URL
    runtime["ai_outage"] = False
    assert isinstance(get_adapter("mock"), MockAdapter)
