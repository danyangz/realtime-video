"""Save a game's full record (every event with who could see it, and every AI's reasoning)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from core.config import ROOT

from .engine import Game

OUT = ROOT / "out" / "werewolf"


def save_game(game: Game, models: dict[int, str] | None = None, **meta) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / time.strftime("game-%Y%m%d-%H%M%S.json")
    path.write_text(json.dumps({
        "winner": game.winner,
        "finished": game.winner is not None,
        **meta,
        "players": [{"seat": p.seat, "name": p.name, "role": p.role.value, "human": p.is_human,
                     "alive": p.alive, "model": (models or {}).get(p.seat, "")} for p in game.players.values()],
        "events": [{"kind": e.kind, "text": e.text, "day": e.day, "phase": e.phase,
                    "visible_to": sorted(e.visible_to) if e.visible_to else None} for e in game.events],
        "thoughts": game.thoughts,
    }, ensure_ascii=False, indent=1))
    return path
