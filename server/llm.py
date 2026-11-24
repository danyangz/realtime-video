"""Streaming chat completions over the OpenAI-compatible HTTP API (Together or OpenRouter)."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from .config import Settings
from .text import ThinkFilter

log = logging.getLogger("llm")


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.client = httpx.AsyncClient(
            base_url=settings.llm_base_url,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        self.extra = self._extra_body()

    def _extra_body(self) -> dict:
        extra: dict = {}
        if self.s.llm_disable_thinking:
            if self.s.llm_provider == "together":
                extra["reasoning"] = {"enabled": False}
            else:  # openrouter
                extra["reasoning"] = {"effort": "none"}
        if self.s.llm_provider == "openrouter":
            # Route to whichever provider serving this (open-weight) model answers fastest.
            extra["provider"] = {"sort": "latency"}
        return extra

    @property
    def label(self) -> str:
        return f"{self.s.llm_provider}:{self.s.llm_model}"

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """Yield text deltas of the assistant reply (reasoning / <think> content removed)."""
        for attempt in range(2):
            body = {
                "model": self.s.llm_model,
                "messages": messages,
                "stream": True,
                "max_tokens": self.s.llm_max_tokens,
                "temperature": self.s.llm_temperature,
                **self.extra,
            }
            async with self.client.stream("POST", "/chat/completions", json=body) as r:
                if r.status_code >= 400:
                    detail = (await r.aread()).decode(errors="replace")[:500]
                    if attempt == 0 and r.status_code == 400 and "reasoning" in self.extra:
                        # Some models reject the reasoning toggle; retry without it.
                        log.warning("LLM rejected reasoning param, retrying without it: %s", detail)
                        self.extra.pop("reasoning", None)
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

    async def aclose(self) -> None:
        await self.client.aclose()
