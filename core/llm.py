"""Streaming chat completions over the OpenAI-compatible HTTP API (Together or OpenRouter).

One `LLM` per provider; the model, token limit, temperature and thinking switch can be
overridden on each call, so different apps (or different game characters) can share it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from .config import BASE_URLS, DEFAULT_MODELS, Settings
from .text import ThinkFilter

log = logging.getLogger("llm")


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, settings: Settings, provider: str | None = None, model: str | None = None) -> None:
        self.s = settings
        self.provider = (provider or settings.llm_provider).lower()
        if self.provider not in BASE_URLS:
            raise ValueError(f"unknown LLM provider {self.provider!r}")
        if model:
            self.model = model
        elif self.provider == settings.llm_provider:
            self.model = settings.llm_model
        else:
            self.model = DEFAULT_MODELS[self.provider]
        self.client = httpx.AsyncClient(
            base_url=BASE_URLS[self.provider],
            headers={"Authorization": f"Bearer {settings.api_key_for(self.provider)}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        self._rejects_reasoning: set[str] = set()  # models that returned 400 for the reasoning toggle

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"

    def _extra_body(self, model: str, disable_thinking: bool) -> dict:
        extra: dict = {}
        if disable_thinking and model not in self._rejects_reasoning:
            if self.provider == "together":
                extra["reasoning"] = {"enabled": False}
            else:  # openrouter
                extra["reasoning"] = {"effort": "none"}
        if self.provider == "openrouter":
            # Route to whichever provider serving this (open-weight) model answers fastest.
            extra["provider"] = {"sort": "latency"}
        return extra

    async def stream(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        disable_thinking: bool | None = None,
    ) -> AsyncIterator[str]:
        """Yield text deltas of the assistant reply (reasoning / <think> content removed)."""
        model = model or self.model
        no_think = self.s.llm_disable_thinking if disable_thinking is None else disable_thinking
        for attempt in range(2):
            extra = self._extra_body(model, no_think)
            body = {
                "model": model,
                "messages": messages,
                "stream": True,
                "max_tokens": max_tokens or self.s.llm_max_tokens,
                "temperature": self.s.llm_temperature if temperature is None else temperature,
                **extra,
            }
            async with self.client.stream("POST", "/chat/completions", json=body) as r:
                if r.status_code >= 400:
                    detail = (await r.aread()).decode(errors="replace")[:500]
                    if attempt == 0 and r.status_code == 400 and "reasoning" in extra:
                        # Some models reject the reasoning toggle; retry without it.
                        log.warning("%s rejected reasoning param, retrying without it: %s", model, detail)
                        self._rejects_reasoning.add(model)
                        continue
                    raise LLMError(f"HTTP {r.status_code}: {detail}")

                think = ThinkFilter()
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("error"):
                        raise LLMError(str(obj["error"])[:500])
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    content = (choices[0].get("delta") or {}).get("content")
                    if content:
                        text = think.feed(content)
                        if text:
                            yield text
                rest = think.flush()
                if rest:
                    yield rest
                return

    async def complete(self, messages: list[dict], **kw) -> str:
        """Whole reply as one string."""
        return "".join([d async for d in self.stream(messages, **kw)])

    async def aclose(self) -> None:
        await self.client.aclose()
