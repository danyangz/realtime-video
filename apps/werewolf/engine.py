"""One werewolf game: rules and flow. No LLM and no UI in here.

Every decision goes through a `Controller` (one per seat: AI or human), and everything that
happens is an `Event` with a visibility (public, or only some seats). A `Hooks` object lets the
caller show events and stream speeches (UI, TTS); the default just collects text.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import Counter, defaultdict
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from .roles import STANDARD_12, Role

log = logging.getLogger("werewolf")

SPEECH_KINDS = {
    "sheriff_campaign": "警上发言",
    "sheriff_pk": "警长 PK 发言",
    "discussion": "发言",
    "pk": "PK 发言",
    "last_words": "遗言",
    "wolf_chat": "狼人夜聊",
}


@dataclass
class Player:
    seat: int
    name: str
    role: Role
    is_human: bool = False
    alive: bool = True
    can_vote: bool = True
    idiot_revealed: bool = False

    @property
    def label(self) -> str:
        return f"{self.seat}号{self.name}"


@dataclass(frozen=True)
class Option:
    key: int  # seat number for seat choices, 1..n otherwise; 0 means "skip"
    label: str


@dataclass
class Event:
    kind: str  # phase | narration | speech | vote | info | end
    text: str
    day: int
    phase: str  # setup | night | day | end
    visible_to: frozenset[int] | None = None  # None = everyone
    data: dict = field(default_factory=dict)

    def visible(self, seat: int | None) -> bool:
        return seat is None or self.visible_to is None or seat in self.visible_to


class Controller(Protocol):
    def speak(self, game: Game, player: Player, kind: str, prompt: str) -> AsyncIterator[str]:
        """Stream the text of a speech."""

    async def choose(
        self, game: Game, player: Player, kind: str, prompt: str, options: list[Option], allow_skip: bool
    ) -> int:
        """Return an option key, or 0 to skip."""


class Hooks:
    """Override to show the game. Default: no UI, speeches are collected silently."""

    async def on_event(self, game: Game, event: Event) -> None:
        pass

    async def present_speech(
        self, game: Game, player: Player, kind: str, chunks: AsyncIterator[str], visible_to: frozenset[int] | None
    ) -> str:
        return "".join([c async for c in chunks])


@dataclass
class Rules:
    sheriff: bool = True
    first_night_last_words: bool = True
    witch_self_save: bool = False


def deal(names: list[str], human_seat: int | None, human_role: Role | None, rng: random.Random) -> list[Player]:
    """Seat 12 players and hand out the standard roles (optionally forcing the human's role)."""
    roles = list(STANDARD_12)
    rng.shuffle(roles)
    if human_seat is not None and human_role is not None:
        i = roles.index(human_role)
        roles[i], roles[human_seat - 1] = roles[human_seat - 1], roles[i]
    return [Player(seat=i + 1, name=names[i], role=roles[i], is_human=(i + 1 == human_seat)) for i in range(12)]


class Game:
    def __init__(
        self,
        players: list[Player],
        controllers: dict[int, Controller],
        hooks: Hooks | None = None,
        rules: Rules | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.players = {p.seat: p for p in sorted(players, key=lambda p: p.seat)}
        self.controllers = controllers
        self.hooks = hooks or Hooks()
        self.rules = rules or Rules()
        self.rng = rng or random.Random()
        self.day = 0
        self.phase = "setup"
        self.events: list[Event] = []
        self.sheriff: int | None = None
        self.antidote = True
        self.poison = True
        self.seer_results: dict[int, bool] = {}
        self.winner: str | None = None  # "good" | "wolf"
        self.pending_deaths: dict[int, str] = {}  # seat -> cause, announced at dawn
        self.last_night_deaths: list[int] = []
        self.thoughts: list[dict] = []  # AI reasoning, shown after the game

    # ------------------------------------------------------------ helpers

    def p(self, seat: int) -> Player:
        return self.players[seat]

    def alive(self) -> list[Player]:
        return [p for p in self.players.values() if p.alive]

    def alive_wolves(self) -> list[Player]:
        return [p for p in self.alive() if p.role.is_wolf]

    def role_holder(self, role: Role) -> Player | None:
        return next((p for p in self.players.values() if p.role is role), None)

    def seat_options(self, seats: Iterable[int]) -> list[Option]:
        return [Option(s, self.players[s].label) for s in sorted(seats)]

    def labels(self, seats: Iterable[int]) -> str:
        return "、".join(self.players[s].label for s in sorted(seats))

    def visible_events(self, seat: int | None) -> list[Event]:
        return [e for e in self.events if e.visible(seat)]

    async def emit(self, kind: str, text: str, visible_to: Iterable[int] | None = None, /, **data) -> Event:
        ev = Event(kind, text, self.day, self.phase, frozenset(visible_to) if visible_to is not None else None, data)
        self.events.append(ev)
        await self.hooks.on_event(self, ev)
        return ev

    async def narrate(self, text: str, *, speak: bool = False, visible_to: Iterable[int] | None = None, **data) -> None:
        await self.emit("narration", text, visible_to, speak=speak, **data)

    async def ask(
        self, p: Player, kind: str, prompt: str, options: list[Option], allow_skip: bool = False
    ) -> int:
        valid = {o.key for o in options} | ({0} if allow_skip else set())
        try:
            key = await self.controllers[p.seat].choose(self, p, kind, prompt, options, allow_skip)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a broken controller must not break the game
            log.exception("controller of seat %d failed on %s", p.seat, kind)
            key = -1
        if key not in valid:
            key = 0 if allow_skip or not options else self.rng.choice(options).key
        return key

    async def speech(self, p: Player, kind: str, prompt: str, visible_to: Iterable[int] | None = None) -> str:
        vis = frozenset(visible_to) if visible_to is not None else None

        async def safe(gen: AsyncIterator[str]) -> AsyncIterator[str]:
            got = False
            try:
                async for chunk in gen:
                    got = got or bool(chunk.strip())
                    yield chunk
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("speech of seat %d failed", p.seat)
            if not got:
                yield "（过）"

        chunks = safe(self.controllers[p.seat].speak(self, p, kind, prompt))
        text = (await self.hooks.present_speech(self, p, kind, chunks, vis)).strip() or "（过）"
        # Recorded after it was shown, so it isn't sent to the UI twice.
        self.events.append(Event(
            "speech", f"{p.label}（{SPEECH_KINDS[kind]}）：{text}", self.day, self.phase, vis,
            {"seat": p.seat, "kind": kind, "text": text},
        ))
        return text

    def check_winner(self) -> bool:
        if self.winner:
            return True
        alive = self.alive()
        if not any(p.role.is_wolf for p in alive):
            self.winner = "good"
        elif not any(p.role.is_god for p in alive) or not any(p.role is Role.VILLAGER for p in alive):
            self.winner = "wolf"
        return self.winner is not None

    # --------------------------------------------------------------- flow

    async def run(self) -> str:
        await self.narrate("游戏开始。本局是 12 人标准局：4 名狼人、4 名平民，以及预言家、女巫、猎人、白痴。", speak=True)
        for p in self.players.values():
            await self.emit("info", f"你的身份是【{p.role.zh}】。", [p.seat], role=p.role.value)
        wolves = [p.seat for p in self.players.values() if p.role.is_wolf]
        await self.emit("info", f"狼人阵营：{self.labels(wolves)}。", wolves, wolves=wolves)

        while not self.winner:
            self.day += 1
            await self.night()
            await self.daytime()

        self.phase = "end"
        verdict = "好人阵营获胜！" if self.winner == "good" else "狼人阵营获胜！"
        reveal = "，".join(f"{p.label}：{p.role.zh}" for p in self.players.values())
        await self.narrate(f"游戏结束，{verdict}", speak=True, winner=self.winner)
        await self.emit("end", f"身份公布：{reveal}。", winner=self.winner,
                        roles={p.seat: p.role.value for p in self.players.values()})
        return self.winner

    # ------------------------------------------------------------- night

    async def night(self) -> None:
        self.phase = "night"
        await self.emit("phase", f"第 {self.day} 夜", day=self.day, phase="night")
        await self.narrate("天黑请闭眼。", speak=True)
        victim, _ = await asyncio.gather(self._wolf_turn(), self._seer_turn())
        saved, poisoned = await self._witch_turn(victim)
        deaths: dict[int, str] = {}
        if victim and not saved:
            deaths[victim] = "wolf"
        if poisoned:
            deaths[poisoned] = "poison"  # a poisoned hunter cannot shoot
        self.pending_deaths = deaths

    async def _wolf_turn(self) -> int | None:
        wolves = self.alive_wolves()
        if not wolves:
            return None
        team = [w.seat for w in wolves]
        await self.narrate("狼人请睁眼。商量今晚要刀谁。", visible_to=team)
        if len(wolves) > 1:
            for w in wolves:
                await self.speech(w, "wolf_chat", "现在是狼人夜聊，只有狼队友能听到。商量今晚刀谁、白天怎么打。", team)
        options = self.seat_options(p.seat for p in self.alive())
        picks = await asyncio.gather(*(
            self.ask(w, "wolf_kill", "选择今晚要刀的玩家。", options) for w in wolves
        ))
        count = Counter(picks)
        top = max(count.values())
        tied = sorted(s for s, c in count.items() if c == top)
        target = tied[0] if len(tied) == 1 else self.rng.choice(tied)
        detail = "，".join(f"{w.seat}号投{k}号" for w, k in zip(wolves, picks))
        await self.emit("info", f"狼人选择：{detail}。今晚刀 {self.p(target).label}。", team, target=target)
        return target

    async def _seer_turn(self) -> None:
        seer = self.role_holder(Role.SEER)
        if not seer or not seer.alive:
            return
        options = self.seat_options(p.seat for p in self.alive() if p.seat != seer.seat)
        target = await self.ask(seer, "seer_check", "选择今晚要查验的玩家。", options)
        is_wolf = self.p(target).role.is_wolf
        self.seer_results[target] = is_wolf
        await self.emit("info", f"你查验了 {self.p(target).label}，他是【{'狼人' if is_wolf else '好人'}】。",
                        [seer.seat], target=target, is_wolf=is_wolf)

    async def _witch_turn(self, victim: int | None) -> tuple[bool, int | None]:
        witch = self.role_holder(Role.WITCH)
        if not witch or not witch.alive or not (self.antidote or self.poison):
            return False, None
        me = [witch.seat]
        saved = False
        if self.antidote:
            if victim is None:
                await self.emit("info", "今晚没有人被狼人刀。", me)
            elif victim == witch.seat and not self.rules.witch_self_save:
                await self.emit("info", "今晚被狼人刀的是你自己。规则不允许女巫自救。", me, victim=victim)
            else:
                label = self.p(victim).label
                await self.emit("info", f"今晚被狼人刀的是 {label}。", me, victim=victim)
                k = await self.ask(witch, "witch_save", f"要用解药救 {label} 吗？",
                                   [Option(1, "用解药救他"), Option(2, "不救")])
                if k == 1:
                    saved = True
                    self.antidote = False
                    await self.emit("info", f"你用解药救了 {label}。", me)
        else:
            await self.emit("info", "你的解药已经用过了，今晚看不到谁被刀。", me)
        poisoned = None
        if not saved and self.poison:
            options = self.seat_options(p.seat for p in self.alive() if p.seat != witch.seat)
            k = await self.ask(witch, "witch_poison", "要用毒药毒死一名玩家吗？不用就跳过。", options, allow_skip=True)
            if k:
                poisoned = k
                self.poison = False
                await self.emit("info", f"你用毒药毒了 {self.p(k).label}。", me)
        return saved, poisoned

    # --------------------------------------------------------------- day

    async def daytime(self) -> None:
        self.phase = "day"
        await self.emit("phase", f"第 {self.day} 天", day=self.day, phase="day")
        await self.narrate("天亮了。", speak=True)
        if self.day == 1 and self.rules.sheriff:
            await self._sheriff_election()

        deaths, self.pending_deaths = self.pending_deaths, {}
        order = sorted(deaths)
        self.last_night_deaths = order
        if not order:
            await self.narrate("昨晚是平安夜，没有人出局。", speak=True, deaths=[])
        else:
            for s in order:
                self.p(s).alive = False
            await self.narrate(f"昨晚出局的是：{self.labels(order)}。", speak=True, deaths=order)
        if self.check_winner():
            return
        last_words = self.day == 1 and self.rules.first_night_last_words
        for s in order:
            await self._after_death(self.p(s), deaths[s], last_words)
            if self.winner:
                return

        await self._discussion()
        await self._exile_vote()

    async def _after_death(self, p: Player, cause: str, last_words: bool) -> None:
        if last_words:
            await self.speech(p, "last_words", "你出局了，请发表遗言。")
        if p.role is Role.HUNTER and cause != "poison":
            options = self.seat_options(x.seat for x in self.alive())
            k = await self.ask(p, "hunter_shoot", "你是猎人，可以开枪带走一名玩家，也可以不开枪。", options, allow_skip=True)
            if k:
                target = self.p(k)
                target.alive = False
                await self.narrate(f"{p.label} 亮出猎人身份，开枪带走了 {target.label}。", speak=True,
                                   shooter=p.seat, target=k)
                if self.check_winner():
                    return
                await self._after_death(target, "shot", last_words=True)
                if self.winner:
                    return
        if self.sheriff == p.seat:
            options = self.seat_options(x.seat for x in self.alive())
            k = await self.ask(p, "badge_transfer", "你是警长，请把警徽移交给一名存活玩家，或者跳过表示撕掉警徽。",
                               options, allow_skip=True)
            if k:
                self.sheriff = k
                await self.narrate(f"{p.label} 把警徽移交给了 {self.p(k).label}。", speak=True, sheriff=k)
            else:
                self.sheriff = None
                await self.narrate(f"{p.label} 撕掉了警徽，本局不再有警长。", speak=True, sheriff=None)

    async def _vote(
        self, kind: str, candidates: list[Player], voters: list[Player], prompt: str,
        weight: Callable[[Player], float] = lambda v: 1.0,
    ) -> list[int]:
        """Everyone votes at once. Returns the seat(s) with the most votes (empty if nobody voted)."""
        options = self.seat_options(c.seat for c in candidates)
        picks = await asyncio.gather(*(
            self.ask(v, kind, prompt, [o for o in options if o.key != v.seat], allow_skip=True) for v in voters
        ))
        tally: dict[int, float] = defaultdict(float)
        lines = []
        for v, k in zip(voters, picks):
            if k:
                tally[k] += weight(v)
                lines.append(f"{v.seat}号投{k}号")
            else:
                lines.append(f"{v.seat}号弃票")
        ranking = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
        result = "，".join(f"{s}号 {t:g} 票" for s, t in ranking) or "无人得票"
        await self.emit("vote", f"投票结果：{'，'.join(lines)}。得票：{result}。", kind=kind,
                        votes={v.seat: k for v, k in zip(voters, picks)}, tally=dict(tally))
        if not tally:
            return []
        top = ranking[0][1]
        return [s for s, t in ranking if t == top]

    async def _sheriff_election(self) -> None:
        await self.narrate("现在开始竞选警长。", speak=True)
        everyone = self.alive()
        picks = await asyncio.gather(*(
            self.ask(p, "run_sheriff", "要上警竞选警长吗？", [Option(1, "上警"), Option(2, "不上警")])
            for p in everyone
        ))
        cands = [p for p, k in zip(everyone, picks) if k == 1]
        if not cands:
            await self.narrate("没有人上警，本局没有警长。", speak=True)
            return
        await self.narrate(f"上警的玩家：{self.labels(c.seat for c in cands)}。", speak=True,
                           candidates=[c.seat for c in cands])
        if len(cands) == 1:
            self.sheriff = cands[0].seat
            await self.narrate(f"只有 {cands[0].label} 上警，直接当选警长。", speak=True, sheriff=self.sheriff)
            return
        start = self.rng.randrange(len(cands))
        for c in cands[start:] + cands[:start]:
            await self.speech(c, "sheriff_campaign", "你上警了，请发表竞选发言。")
        voters = [p for p in everyone if p not in cands]
        if not voters:
            await self.narrate("所有人都上警了，没有人能投票，警徽流失。", speak=True)
            return
        await self.narrate("请警下的玩家投票选出警长。", speak=True)
        top = await self._vote("sheriff_vote", cands, voters, "投票选出警长，也可以弃票。")
        if len(top) > 1:
            tied = [self.p(s) for s in sorted(top)]
            await self.narrate(f"{self.labels(top)} 平票，进入 PK 发言。", speak=True)
            for c in tied:
                await self.speech(c, "sheriff_pk", "你和其他候选人平票，请进行 PK 发言。")
            top = await self._vote("sheriff_vote", tied, voters, "警长 PK 投票：在平票的候选人中选一个，也可以弃票。")
        if len(top) != 1:
            await self.narrate("没有选出警长，警徽流失。", speak=True)
            return
        self.sheriff = top[0]
        await self.narrate(f"{self.p(top[0]).label} 当选警长。", speak=True, sheriff=self.sheriff)

    def _order_from(self, seat: int, increasing: bool) -> list[int]:
        """Alive seats starting next to `seat` in the given direction, wrapping around, excluding `seat`."""
        seats = sorted(p.seat for p in self.alive() if p.seat != seat)
        after = [s for s in seats if s > seat]
        before = [s for s in seats if s < seat]
        return after + before if increasing else (before[::-1] + after[::-1])

    async def _discussion(self) -> None:
        sheriff = self.p(self.sheriff) if self.sheriff and self.p(self.sheriff).alive else None
        if sheriff:
            k = await self.ask(sheriff, "speech_order", "你是警长，请决定发言顺序（警长最后发言）。",
                               [Option(1, "从警长左边开始（座位号递增）"), Option(2, "从警长右边开始（座位号递减）")])
            order = self._order_from(sheriff.seat, increasing=(k != 2)) + [sheriff.seat]
        else:
            ref = self.last_night_deaths[0] if self.last_night_deaths else self.rng.choice(self.alive()).seat - 1
            order = self._order_from(ref, increasing=True)
            if ref in self.players and self.p(ref).alive and ref not in order:
                order.append(ref)
        if not order:
            return
        await self.narrate(f"请从 {self.p(order[0]).label} 开始依次发言。", speak=True, order=order)
        for s in order:
            await self.speech(self.p(s), "discussion", "轮到你发言了。")

    async def _exile_vote(self) -> None:
        await self.narrate("发言结束，开始投票放逐。", speak=True)
        alive = self.alive()
        voters = [p for p in alive if p.can_vote]

        def weight(v: Player) -> float:
            return 1.5 if v.seat == self.sheriff else 1.0

        top = await self._vote("exile_vote", alive, voters, "投票放逐一名玩家，也可以弃票。", weight)
        if len(top) > 1:
            tied = [self.p(s) for s in sorted(top)]
            await self.narrate(f"{self.labels(top)} 平票，进入 PK 发言。", speak=True)
            for p in tied:
                await self.speech(p, "pk", "你和其他人平票，请进行 PK 发言，争取不被投出。")
            voters2 = [v for v in voters if v.seat not in top]
            top = await self._vote("exile_vote", tied, voters2, "PK 投票：在平票的玩家中选一个放逐，也可以弃票。", weight)
        if len(top) != 1:
            await self.narrate("没有人被放逐，今天平安过去。", speak=True)
            return
        target = self.p(top[0])
        if target.role is Role.IDIOT and not target.idiot_revealed:
            target.idiot_revealed = True
            target.can_vote = False
            await self.narrate(f"{target.label} 被投票放逐，翻牌亮出身份：白痴。免于出局，但之后不能投票。",
                               speak=True, idiot=target.seat)
            if self.sheriff == target.seat:
                self.sheriff = None
                await self.narrate("白痴翻牌后不能再当警长，警徽流失。", speak=True, sheriff=None)
            return
        target.alive = False
        await self.narrate(f"{target.label} 被放逐出局。", speak=True, exiled=target.seat)
        if self.check_winner():
            return
        await self._after_death(target, "exile", last_words=True)
