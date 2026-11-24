"""HTTP + WebSocket server: serves the web UI and runs one Session per browser connection."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .config import ROOT, Settings
from .llm import LLM
from .session import Session
from .stt import TogetherSTT
from .tts import TogetherTTS

log = logging.getLogger("app")
settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.llm = LLM(settings)
    for p in settings.problems():
        log.warning("配置问题：%s", p)
    log.info("LLM=%s  STT=%s  TTS=%s/%s", app.state.llm.label, settings.stt_model, settings.tts_model, settings.tts_voice)
    yield
    await app.state.llm.aclose()


app = FastAPI(lifespan=lifespan)


class _WSTransport:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws

    async def send_text(self, data: str) -> None:
        await self.ws.send_text(data)

    async def send_bytes(self, data: bytes) -> None:
        await self.ws.send_bytes(data)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    problems = settings.problems()
    if problems:
        await ws.send_text(json.dumps({"type": "fatal", "message": "；".join(problems)}, ensure_ascii=False))
        await ws.close()
        return

    tts = TogetherTTS(settings)
    session = Session(
        settings,
        _WSTransport(ws),
        llm=app.state.llm,
        tts=tts,
        stt_factory=lambda on_event: TogetherSTT(settings, on_event),
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


# Static UI last, so it doesn't shadow /ws.
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
