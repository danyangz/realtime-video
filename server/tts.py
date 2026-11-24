"""Streaming text-to-speech via Together's TTS WebSocket.

Protocol: https://docs.together.ai/docs/inference/text-to-speech/websocket
Each assistant turn gets its own WebSocket, so interrupting a turn is just closing
its socket: no stale audio can leak into the next turn. To avoid paying the
connection setup on every turn, one spare connection is kept warm.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from urllib.parse import urlencode

import websockets
from websockets.protocol import State

from .config import Settings

log = logging.getLogger("tts")

URL = "wss://api.together.ai/v1/audio/speech/websocket"
SPARE_MAX_AGE_S = 50.0


class TTSError(RuntimeError):
    pass


class TTSStream:
    """Text in, PCM16 audio out, for one assistant turn."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.sent = 0
        self.done = 0
        self.committed = False

    async def send_text(self, text: str) -> None:
        await self.ws.send(json.dumps({"type": "input_text_buffer.append", "text": text}))
        self.sent += 1

    async def finish(self) -> None:
        """No more text for this turn."""
        await self.ws.send(json.dumps({"type": "input_text_buffer.commit"}))
        self.committed = True

    async def audio(self) -> AsyncIterator[bytes]:
        """Yield raw PCM16 chunks until all submitted text has been spoken."""
        loop = asyncio.get_running_loop()
        last = loop.time()
        while True:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=0.25)
            except TimeoutError:
                idle = loop.time() - last
                if self.committed and ((self.done >= self.sent and idle >= 0.3) or idle >= 2.0):
                    return
                continue
            except websockets.ConnectionClosed:
                if self.committed:
                    return
                raise TTSError("TTS connection closed")
            last = loop.time()
            msg = json.loads(raw)
            t = msg.get("type", "")
            if t == "conversation.item.audio_output.delta":
                data = msg.get("delta")
                if data:
                    yield base64.b64decode(data)
            elif t == "conversation.item.audio_output.done":
                self.done += 1
            elif t in ("conversation.item.tts.failed", "error"):
                raise TTSError(str(msg.get("error") or msg)[:500])

    def close(self) -> None:
        asyncio.get_running_loop().create_task(_quiet_close(self.ws))


async def _quiet_close(ws) -> None:
    with contextlib.suppress(Exception):
        await ws.close()


class TogetherTTS:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._spare: asyncio.Task | None = None
        self._spare_born = 0.0
        self._keeper: asyncio.Task | None = None

    @property
    def sample_rate(self) -> int:
        return self.s.tts_sample_rate

    def _url(self) -> str:
        params = {
            "model": self.s.tts_model,
            "voice": self.s.tts_voice,
            "response_format": "pcm",
            "sample_rate": self.s.tts_sample_rate,
            "language": self.s.tts_language,
            # We already split text into sentences ourselves; synthesize each piece right away.
            "segment": "immediate",
        }
        if self.s.tts_instructions and "qwen3-tts" in self.s.tts_model.lower():
            params["instructions"] = self.s.tts_instructions
        return f"{URL}?{urlencode(params)}"

    async def _connect(self):
        ws = await websockets.connect(
            self._url(),
            additional_headers={"Authorization": f"Bearer {self.s.together_api_key}"},
            max_size=None,
            close_timeout=1,
        )
        try:
            first = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        except BaseException:  # including cancellation: never leak the socket
            asyncio.get_running_loop().create_task(_quiet_close(ws))
            raise
        if first.get("type") != "session.created":
            await _quiet_close(ws)
            raise TTSError(f"unexpected first TTS message: {str(first)[:300]}")
        return ws

    def warm(self) -> None:
        """Start connecting a spare socket in the background (idempotent)."""
        if self._spare is None:
            self._spare = asyncio.get_running_loop().create_task(self._connect())
            self._spare_born = asyncio.get_running_loop().time()
        if self._keeper is None:
            self._keeper = asyncio.get_running_loop().create_task(self._keep_fresh())

    async def _keep_fresh(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(10)
            spare = self._spare
            if spare and spare.done() and loop.time() - self._spare_born > SPARE_MAX_AGE_S:
                self._spare = None
                if not spare.cancelled() and spare.exception() is None:
                    await _quiet_close(spare.result())
                self.warm()

    async def open(self) -> TTSStream:
        ws = None
        spare, self._spare = self._spare, None
        if spare is not None:
            try:
                ws = await spare
                if ws.state is not State.OPEN:
                    ws = None
            except Exception as e:  # noqa: BLE001
                log.warning("spare TTS connection failed: %s", e)
                ws = None
        if ws is None:
            ws = await self._connect()
        self.warm()
        return TTSStream(ws)

    async def aclose(self) -> None:
        if self._keeper:
            self._keeper.cancel()
        spare, self._spare = self._spare, None
        if spare:
            if spare.done() and not spare.cancelled() and spare.exception() is None:
                await _quiet_close(spare.result())
            else:
                spare.cancel()
