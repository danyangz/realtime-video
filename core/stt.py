"""Streaming speech-to-text via Together's realtime Whisper WebSocket.

Protocol: https://docs.together.ai/docs/inference/transcription/streaming
Audio in: 16 kHz mono PCM16, base64 in `input_audio_buffer.append` events.
Server-side VAD segments speech; we get interim `delta` and final `completed` events.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Callable
from urllib.parse import urlencode

import websockets

from .config import Settings

log = logging.getLogger("stt")

URL = "wss://api.together.ai/v1/realtime"
BYTES_PER_MS = 32  # 16 kHz * 2 bytes
SEND_BATCH_BYTES = 60 * BYTES_PER_MS  # send ~60 ms of audio per message
MAX_QUEUED_BYTES = 3000 * BYTES_PER_MS  # keep at most ~3 s while (re)connecting

# on_event(kind, text) where kind is one of: ready, partial, final, turn_start, error
EventCallback = Callable[[str, str], None]


class TogetherSTT:
    def __init__(self, settings: Settings, on_event: EventCallback) -> None:
        self.s = settings
        self.on_event = on_event
        self._pending = bytearray()
        self._have_audio = asyncio.Event()

    def push_audio(self, pcm16: bytes) -> None:
        self._pending += pcm16
        if len(self._pending) > MAX_QUEUED_BYTES:
            del self._pending[: len(self._pending) - MAX_QUEUED_BYTES]
        self._have_audio.set()

    def _vad(self) -> dict:
        return {
            "type": "server_vad",
            "threshold": self.s.vad_threshold,
            "min_silence_duration_ms": self.s.vad_min_silence_ms,
            "min_speech_duration_ms": self.s.vad_min_speech_ms,
            "max_speech_duration_s": self.s.vad_max_speech_s,
            "speech_pad_ms": self.s.vad_speech_pad_ms,
        }

    def _url(self) -> str:
        vad = self._vad()
        params = {
            "model": self.s.stt_model,
            "input_audio_format": "pcm_s16le_16000",
            "turn_detection": vad.pop("type"),
            **vad,
        }
        if self.s.stt_language:
            params["language"] = self.s.stt_language
        return f"{URL}?{urlencode(params)}"

    async def run(self) -> None:
        """Keep a transcription session open until cancelled, reconnecting on failure."""
        backoff = 0.5
        while True:
            try:
                await self._session()
                backoff = 0.5
            except asyncio.CancelledError:
                raise
            except websockets.InvalidStatus as e:
                code = e.response.status_code
                if code in (401, 403):
                    self.on_event("error", f"语音识别鉴权失败（HTTP {code}），检查 TOGETHER_API_KEY")
                    return
                log.warning("STT connection rejected: HTTP %s", code)
                self.on_event("error", f"语音识别连接被拒绝（HTTP {code}），稍后重试")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 8.0)
            except Exception as e:  # noqa: BLE001 - report and reconnect
                log.warning("STT connection error: %s", e)
                self.on_event("error", f"语音识别连接出错：{e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 8.0)

    async def _session(self) -> None:
        headers = {"Authorization": f"Bearer {self.s.together_api_key}", "OpenAI-Beta": "realtime=v1"}
        async with websockets.connect(self._url(), additional_headers=headers, max_size=None) as ws:
            first = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if first.get("type") != "session.created":
                raise RuntimeError(f"unexpected first message: {str(first)[:300]}")
            session: dict = {"turn_detection": self._vad()}
            if self.s.stt_prompt:
                session["prompt"] = self.s.stt_prompt
            await ws.send(json.dumps({"type": "transcription_session.updated", "session": session}))
            log.info("STT session ready (%s)", self.s.stt_model)
            self.on_event("ready", "")

            sender = asyncio.create_task(self._sender(ws))
            try:
                async for raw in ws:
                    self._handle(json.loads(raw))
            finally:
                sender.cancel()
                try:
                    await sender
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            raise RuntimeError("STT websocket closed")

    def _handle(self, msg: dict) -> None:
        t = msg.get("type", "")
        if t == "conversation.item.input_audio_transcription.delta":
            self.on_event("partial", msg.get("delta") or "")
        elif t == "conversation.item.input_audio_transcription.completed":
            self.on_event("final", msg.get("transcript") or "")
        elif t == "conversation.item.input_audio_transcription.start_of_turn":
            self.on_event("turn_start", "")
        elif t == "conversation.item.input_audio_transcription.failed" or t == "error":
            err = msg.get("error") or msg
            log.warning("STT error event: %s", err)
            self.on_event("error", f"语音识别出错：{err.get('message', err) if isinstance(err, dict) else err}")
        # session.created / transcription_session.updated / others: ignore

    async def _sender(self, ws) -> None:
        while True:
            await self._have_audio.wait()
            self._have_audio.clear()
            if len(self._pending) < SEND_BATCH_BYTES:
                continue  # wait for a full batch; avoids flooding with tiny messages
            chunk = bytes(self._pending)
            self._pending.clear()
            await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": base64.b64encode(chunk).decode()}))
