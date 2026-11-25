"""Long-lived resources shared by every app, created once by the server.

Apps reach them through `websocket.app.state.services`.
"""

from __future__ import annotations

import asyncio
import logging

from .config import Settings
from .llm import LLM
from .stt import TogetherSTT
from .tts import TogetherTTS

log = logging.getLogger("services")


class Services:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self._llms: dict[str, LLM] = {}
        self.asr = None  # local_stt.ASREngine when STT_ENGINE=local

    async def start(self) -> None:
        if self.settings.stt_engine == "local":
            from .local_stt import ASREngine, ensure_silero

            await asyncio.to_thread(ensure_silero)
            self.asr = ASREngine(self.settings)
            self.asr.start()  # load models in the background so the first call is fast

    def llm(self, provider: str | None = None) -> LLM:
        """Shared HTTP client per provider (model is chosen per call)."""
        provider = (provider or self.settings.llm_provider).lower()
        if provider not in self._llms:
            self._llms[provider] = LLM(self.settings, provider)
        return self._llms[provider]

    def new_tts(self) -> TogetherTTS:
        """One per connection: it keeps a warm socket for the default voice."""
        return TogetherTTS(self.settings)

    def new_stt(self, on_event, hotwords: str = ""):
        """Streaming STT for one connection: local Qwen3-ASR or Together Whisper."""
        if self.settings.stt_engine == "local":
            from .local_stt import LocalSTT

            return LocalSTT(self.settings, self.asr, on_event, hotwords=hotwords)
        return TogetherSTT(self.settings, on_event)

    @property
    def stt_label(self) -> str:
        s = self.settings
        return f"本地 {s.asr_model}" if s.stt_engine == "local" else f"Together {s.stt_model}"

    async def transcribe_clip(self, pcm16: bytes, hotwords: str = "") -> str:
        """One-shot transcription of a short recording (e.g. push-to-talk). Local engine only."""
        if self.asr is None:
            raise RuntimeError("按住说话需要本地语音识别（STT_ENGINE=local）")
        import numpy as np

        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        context = " ".join(x for x in (hotwords, self.settings.asr_hotwords) if x)
        return await self.asr.transcribe(audio, partial=False, context=context)

    async def aclose(self) -> None:
        for llm in self._llms.values():
            await llm.aclose()
