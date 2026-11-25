"""WebSocket endpoint: one Session per browser connection."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.services import Services

from .config import HOTWORDS, ChatSettings
from .session import Session

log = logging.getLogger("video_chat")
router = APIRouter()
chat_settings = ChatSettings()


class _WSTransport:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws

    async def send_text(self, data: str) -> None:
        await self.ws.send_text(data)

    async def send_bytes(self, data: bytes) -> None:
        await self.ws.send_bytes(data)


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    services: Services = ws.app.state.services
    settings = services.settings
    await ws.accept()
    problems = settings.problems()
    if problems:
        await ws.send_text(json.dumps({"type": "fatal", "message": "；".join(problems)}, ensure_ascii=False))
        await ws.close()
        return

    tts = services.new_tts()
    session = Session(
        settings,
        _WSTransport(ws),
        llm=services.llm(),
        tts=tts,
        stt_factory=lambda on_event: services.new_stt(on_event, hotwords=HOTWORDS),
        stt_label=services.stt_label,
        chat=chat_settings,
    )
    runner = asyncio.create_task(session.run())
    try:
        while not runner.done():
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                session.on_audio(msg["bytes"])
            elif msg.get("text"):
                try:
                    session.on_client_message(json.loads(msg["text"]))
                except json.JSONDecodeError:
                    log.warning("bad client message: %r", msg["text"][:200])
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await runner
        await tts.aclose()
