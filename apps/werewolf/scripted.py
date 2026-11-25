"""A rule-of-thumb bot with canned speeches. Used in tests and for UI work without an LLM
(WEREWOLF_FAKE_AI=1). It plays legally and roughly sensibly, nothing more."""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator

from .engine import Game, Option, Player
from .roles import Role

LINES = {
    "sheriff_campaign": ["我上警是想带队，昨晚没什么信息，先听听大家怎么说。", "我是好人，警徽给我我会认真归票。"],
    "sheriff_pk": ["请把票投给我，我会带好人走。"],
    "discussion": ["我是好人，目前觉得{x}号发言有点怪，先关注一下。", "我这轮没有太多信息，跟着警长的思路走。",
                   "{x}号刚才的逻辑有问题，我建议今天出他。"],
    "pk": ["我真的是好人，大家别投错了。"],
    "last_words": ["我是好人，好人加油，注意{x}号。"],
    "wolf_chat": ["今晚刀{x}号吧，他看起来像神。", "同意，白天我们分开站边。"],
}


class ScriptedController:
    def __init__(self, rng: random.Random | None = None, delay: float = 0.0) -> None:
        self.rng = rng or random.Random()
        self.delay = delay

    async def speak(self, game: Game, player: Player, kind: str, prompt: str) -> AsyncIterator[str]:
        others = [p.seat for p in game.alive() if p.seat != player.seat] or [player.seat]
        text = self.rng.choice(LINES[kind]).format(x=self.rng.choice(others))
        for i in range(0, len(text), 6):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield text[i : i + 6]

    async def choose(
        self, game: Game, player: Player, kind: str, prompt: str, options: list[Option], allow_skip: bool
    ) -> int:
        if self.delay:
            await asyncio.sleep(self.delay)
        keys = [o.key for o in options]
        if not keys:
            return 0
        wolves = {p.seat for p in game.players.values() if p.role.is_wolf}
        if kind == "wolf_kill":
            good = [k for k in keys if k not in wolves]
            return self.rng.choice(good or keys)
        if kind == "seer_check":
            fresh = [k for k in keys if k not in game.seer_results]
            return self.rng.choice(fresh or keys)
        if kind == "witch_save":
            return 1 if game.day == 1 else 2
        if kind == "witch_poison":
            return self.rng.choice(keys) if self.rng.random() < 0.3 else 0
        if kind == "run_sheriff":
            return 1 if self.rng.random() < 0.4 else 2
        if kind in ("exile_vote", "sheriff_vote"):
            if player.role.is_wolf:
                keys = [k for k in keys if k not in wolves] or keys
            elif player.role is Role.SEER:
                known = [k for k in keys if game.seer_results.get(k)]
                if known:
                    return known[0]
            return self.rng.choice(keys) if self.rng.random() < 0.9 else 0
        if kind == "hunter_shoot":
            return self.rng.choice(keys) if self.rng.random() < 0.7 else 0
        return self.rng.choice(keys)
