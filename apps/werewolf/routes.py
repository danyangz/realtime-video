"""WebSocket endpoint: one game per browser connection."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.services import Services

from .config import WerewolfSettings
from .personas import CASTS
from .session import WerewolfSession

log = logging.getLogger("werewolf")
router = APIRouter()
settings = WerewolfSettings()


@router.get("/casts")
def casts() -> list[dict]:
    """Character sets with their looks, for the start screen preview."""
    return [{
        "key": c.key, "title": c.title,
        "judge": {"name": c.judge_name, "look": c.judge_look},
        "human_look": c.human_look,
        "personas": [{"name": p.name, "color": p.color, "look": p.look, "style": p.style} for p in c.personas],
    } for c in CASTS.values()]


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    services: Services = ws.app.state.services
    await ws.accept()
    if not settings.fake_ai and services.settings.problems():
        await ws.send_text(json.dumps({"type": "fatal", "message": "；".join(services.settings.problems())},
                                      ensure_ascii=False))
        await ws.close()
        return

    session = WerewolfSession(services, settings, ws.send_text, ws.send_bytes)
    await session.hello()
    tasks: set[asyncio.Task] = set()
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                session.on_audio(msg["bytes"])
            elif msg.get("text"):
                try:
                    data = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                # Handle in the background so a slow handler never blocks the receive loop.
                t = asyncio.create_task(session.handle(data))
                tasks.add(t)
                t.add_done_callback(tasks.discard)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        for t in tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            await session.close()
