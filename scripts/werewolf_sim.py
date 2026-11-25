"""Watch 12 AIs play a werewolf game in the terminal (text only, no browser).

    python scripts/werewolf_sim.py
    python scripts/werewolf_sim.py --models together:deepseek-ai/DeepSeek-V4.1-Flash together:Qwen/Qwen3.5-9B
    python scripts/werewolf_sim.py --fake          # scripted bots, no API calls

With several models, seats are assigned round-robin, so you can see which model wins more.
The full log (with every AI's private reasoning) is saved to out/werewolf/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apps.werewolf.agent import AIController  # noqa: E402
from apps.werewolf.config import WerewolfSettings  # noqa: E402
from apps.werewolf.engine import SPEECH_KINDS, Game, Hooks, deal  # noqa: E402
from apps.werewolf.personas import CASTS, DEFAULT_CAST  # noqa: E402
from apps.werewolf.scripted import ScriptedController  # noqa: E402
from core.services import Services  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "out" / "werewolf"


class Console(Hooks):
    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.speech_times: list[float] = []

    def stamp(self) -> str:
        s = int(time.monotonic() - self.t0)
        return f"[{s // 60:02d}:{s % 60:02d}]"

    async def on_event(self, game, event):
        if event.kind == "phase":
            print(f"\n{'=' * 20} {event.text} {'=' * 20}")
        elif event.kind == "info" and "role" in event.data:
            return
        else:
            who = "" if event.visible_to is None else f"(仅 {game.labels(event.visible_to)}) "
            print(f"{self.stamp()} {who}{event.text}")

    async def present_speech(self, game, player, kind, chunks, visible_to):
        t = time.monotonic()
        text = "".join([c async for c in chunks])
        self.speech_times.append(time.monotonic() - t)
        tag = "（狼人夜聊）" if kind == "wolf_chat" else f"（{SPEECH_KINDS[kind]}）"
        print(f"{self.stamp()} {player.label}[{player.role.zh}]{tag}：{text}")
        return text


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", help="provider:model specs (round-robin over seats)")
    ap.add_argument("--fake", action="store_true", help="scripted bots instead of LLMs")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--cast", default=DEFAULT_CAST, choices=sorted(CASTS), help="character set")
    args = ap.parse_args()

    ws = WerewolfSettings()
    if args.models:
        ws.models = [tuple(m.split(":", 1)) if ":" in m else ("together", m) for m in args.models]
    services = Services()
    rng = random.Random(args.seed)
    cast = CASTS[args.cast]
    personas = list(cast.personas)
    rng.shuffle(personas)
    players = deal([p.name for p in personas], None, None, rng)
    controllers, models = {}, {}
    for p in players:
        if args.fake:
            controllers[p.seat] = ScriptedController(rng)
            models[p.seat] = "scripted"
        else:
            prov, model = ws.models[(p.seat - 1) % len(ws.models)]
            controllers[p.seat] = AIController(services.llm(prov), model, personas[p.seat - 1], ws, roleplay=cast.roleplay)
            models[p.seat] = f"{prov}:{model}"

    print("座位：")
    for p in players:
        print(f"  {p.label:8s} {p.role.zh:4s} {models[p.seat]}")
    hooks = Console()
    game = Game(players, controllers, hooks=hooks, rng=rng)
    try:
        winner = await game.run()
    finally:
        await services.aclose()

    print(f"\n胜方：{'好人' if winner == 'good' else '狼人'}  共 {game.day} 天，用时 {hooks.stamp()}")
    if hooks.speech_times:
        avg = sum(hooks.speech_times) / len(hooks.speech_times)
        print(f"发言 {len(hooks.speech_times)} 次，平均生成 {avg:.1f}s")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / time.strftime("game-%Y%m%d-%H%M%S.json")
    path.write_text(json.dumps({
        "winner": winner,
        "players": [{"seat": p.seat, "name": p.name, "role": p.role.value, "model": models[p.seat]} for p in players],
        "events": [{"kind": e.kind, "text": e.text, "day": e.day, "phase": e.phase,
                    "visible_to": sorted(e.visible_to) if e.visible_to else None} for e in game.events],
        "thoughts": game.thoughts,
    }, ensure_ascii=False, indent=1))
    print(f"完整记录（含每个 AI 的思考）：{path}")


if __name__ == "__main__":
    asyncio.run(main())
