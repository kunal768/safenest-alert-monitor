"""Ollama / mock sound-event classifier."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.config import runtime, settings
from app.rules import LABELS

log = logging.getLogger("safenest.classifier")

PROMPT_VERSION = settings.prompt_version
# Port 9 (discard) refuses connections: a real network failure for the outage demo.
DEAD_OLLAMA_URL = "http://127.0.0.1:9"
MOCK_TAGS = ["llama3.2:3b", "qwen2.5:3b", "qwen2:7b"]

SYSTEM_PROMPT = f"""You are SafeNest's home audio event classifier.
The gateway already converted microphone audio into a short text description.
Classify the description into exactly one label from this set:
{', '.join(LABELS)}.

Return JSON only:
{{"label": "<one of the labels>", "confidence": <0.0-1.0>, "reason": "<one short sentence>"}}

Rules:
- glass, window smash, shattering → glass_break
- person falling, body hitting the floor, thud then moan → fall
- scream, shriek, yelling in panic → scream
- someone asking for help, "help me", emergency call → call_for_help
- cough, throat clearing → cough
- door latch, hinge, lock, door opening or closing → door_open
- an unfamiliar or stranger's voice, whispering intruder → unknown_voice
- no sound or movement for a long time → long_silence
- everyday household sounds are normal: talking, laughing, TV, music, furniture moving, footsteps, cooking, appliances
Only use unknown_voice when the description says the voice is unfamiliar or suspicious; laughter and chatter are normal.
Do not invent labels. Confidence is your certainty, not a calibrated probability.
"""


class ClassifyResult(BaseModel):
    label: str
    confidence: float
    reason: str = Field(min_length=1)
    latency_ms: int = 0
    model_version: str = ""
    prompt_version: str = PROMPT_VERSION
    unclassified: bool = False
    error: str | None = None

    @field_validator("label")
    @classmethod
    def _label(cls, v: str) -> str:
        v = v.strip().lower().replace(" ", "_")
        if v not in LABELS:
            raise ValueError(f"invalid label {v}")
        return v

    @field_validator("confidence")
    @classmethod
    def _conf(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))


def normalize_tag(tag: str) -> str:
    return tag if ":" in tag else f"{tag}:latest"


def build_user_message(description: str, context: dict | None = None) -> str:
    # Description first; leading with room/level metadata made small models over-read it.
    parts = [f"{k.lower()} {v}" for k, v in (context or {}).items() if v not in (None, "")]
    if not parts:
        return description
    return f"Sound description: {description}\nContext (not evidence of the label): {', '.join(parts)}"


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


class MockAdapter:
    """Deterministic labels for tests and no-Ollama fallback."""

    KEYWORDS = [
        ("glass", "glass_break", 0.93, "Shattering glass described"),
        ("shatter", "glass_break", 0.93, "Shattering glass described"),
        ("fall", "fall", 0.91, "Fall-like impact described"),
        ("thud", "fall", 0.88, "Impact consistent with a fall"),
        ("scream", "scream", 0.92, "Scream described"),
        ("help", "call_for_help", 0.90, "Call for help described"),
        ("cough", "cough", 0.89, "Cough described"),
        ("door", "door_open", 0.86, "Door activity described"),
        ("silence", "long_silence", 0.84, "Long silence described"),
        ("unknown voice", "unknown_voice", 0.82, "Unidentified voice"),
        ("stranger", "unknown_voice", 0.82, "Unidentified voice"),
        ("laugh", "normal", 0.88, "Everyday household sound"),
        ("furniture", "normal", 0.86, "Everyday household sound"),
        ("talk", "normal", 0.85, "Speech that is not distress"),
    ]

    async def classify(self, description: str, model: str, context: dict | None = None) -> ClassifyResult:
        t0 = time.perf_counter()
        if settings.mock_latency_ms:
            await asyncio.sleep(settings.mock_latency_ms / 1000)
        text = description.lower()
        if "unintelligible" in text or "fail-loud" in text:
            return ClassifyResult(
                label="normal",
                confidence=0.0,
                reason="classifier failed",
                unclassified=True,
                error="mock unclassified",
                latency_ms=int((time.perf_counter() - t0) * 1000),
                model_version=model or "mock",
            )
        for needle, label, conf, reason in self.KEYWORDS:
            if needle in text:
                return ClassifyResult(
                    label=label,
                    confidence=conf,
                    reason=reason,
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                    model_version=model or "mock",
                )
        return ClassifyResult(
            label="normal",
            confidence=0.7,
            reason="No distress keywords matched",
            latency_ms=int((time.perf_counter() - t0) * 1000),
            model_version=model or "mock",
        )

    async def health_prompt(self, model: str) -> ClassifyResult:
        return await self.classify("A brief cough from the bedroom", model)

    async def list_tags(self) -> list[str]:
        return list(MOCK_TAGS)


class OutageAdapter(MockAdapter):
    """Mock-mode stand-in for a stopped Ollama: every call fails."""

    async def classify(self, description: str, model: str, context: dict | None = None) -> ClassifyResult:
        return ClassifyResult(
            label="normal",
            confidence=0.0,
            reason="classifier failed",
            unclassified=True,
            error="simulated LLM outage",
            model_version=model,
        )

    async def list_tags(self) -> list[str]:
        raise httpx.ConnectError("simulated LLM outage")


class OllamaAdapter:
    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (base_url or settings.ollama_url).rstrip("/")
        self.timeout = timeout or settings.classifier_timeout_sec

    async def classify(self, description: str, model: str, context: dict | None = None) -> ClassifyResult:
        last_err = "unknown"
        message = build_user_message(description, context)
        for attempt in range(3):
            t0 = time.perf_counter()
            try:
                raw = await self._chat(model, message, repair=attempt > 0)
                data = _extract_json(raw)
                result = ClassifyResult.model_validate(data)
                result.latency_ms = int((time.perf_counter() - t0) * 1000)
                result.model_version = model
                result.prompt_version = PROMPT_VERSION
                log.info("classified model=%s label=%s conf=%.2f latency_ms=%d", model, result.label, result.confidence, result.latency_ms)
                return result
            except (ValidationError, json.JSONDecodeError, httpx.HTTPError, ValueError) as exc:
                last_err = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                log.warning("classify attempt %d failed model=%s: %s", attempt + 1, model, last_err)
                if attempt < 2:
                    await asyncio.sleep(0.2 * (2**attempt))
        log.error("unclassified after retries model=%s: %s", model, last_err)
        return ClassifyResult(
            label="normal",
            confidence=0.0,
            reason="classifier failed",
            unclassified=True,
            error=last_err,
            model_version=model,
            prompt_version=PROMPT_VERSION,
        )

    async def _chat(self, model: str, description: str, repair: bool) -> str:
        user = description
        if repair:
            user = (
                "Your previous answer was not valid JSON with keys label, confidence, reason. "
                "Reply with JSON only. Event: " + description
            )
        payload = {
            "model": model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
            "keep_alive": "30m",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(f"{self.base_url}/api/chat", json=payload)
            r.raise_for_status()
            body = r.json()
            return body.get("message", {}).get("content", "")

    async def health_prompt(self, model: str) -> ClassifyResult:
        """Warm-up prompt; a cold model load can take far longer than the 8 s classify timeout."""
        warm = OllamaAdapter(self.base_url, timeout=settings.warmup_timeout_sec)
        return await warm.classify("A brief cough from the bedroom", model)

    async def list_tags(self) -> list[str]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{self.base_url}/api/tags")
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]


def get_adapter(name: str | None = None):
    kind = (name or settings.model_adapter).lower()
    if kind == "mock":
        return OutageAdapter() if runtime["ai_outage"] else MockAdapter()
    if runtime["ai_outage"]:
        return OllamaAdapter(base_url=DEAD_OLLAMA_URL, timeout=1.0)
    return OllamaAdapter()
