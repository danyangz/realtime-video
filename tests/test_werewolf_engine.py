"""Werewolf rules and flow, with scripted players (no LLM)."""

import asyncio
import random

from apps.werewolf.engine import Game, Option, Player, Rules, deal
from apps.werewolf.roles import STANDARD_12, Role
from apps.werewolf.scripted import ScriptedController

NAMES = [f"P{i}" for i in range(1, 13)]
DEAD_OK = {"last_words", "hunter_shoot", "badge_transfer"}


class Checked(ScriptedController):
    """Scripted bot that also asserts the engine only asks players who may act."""

    async def speak(self, game, player, kind, prompt):
        assert player.alive or kind in DEAD_OK, (player.seat, kind)
        async for c in super().speak(game, player, kind, prompt):
            yield c

    async def choose(self, game, player, kind, prompt, options, allow_skip):
        assert player.alive or kind in DEAD_OK, (player.seat, kind)
        if kind in ("exile_vote", "sheriff_vote"):
            assert player.can_vote
            assert player.seat not in [o.key for o in options], "cannot vote for yourself"
        if kind in ("wolf_kill",):
            assert player.role.is_wolf
        return await super().choose(game, player, kind, prompt, options, allow_skip)


def play(seed: int) -> Game:
    rng = random.Random(seed)
    players = deal(NAMES, None, None, rng)
    ctl = Checked(rng)
    game = Game(players, {p.seat: ctl for p in players}, rng=rng)
    asyncio.run(game.run())
    return game


def test_random_games_always_finish_with_a_correct_winner():
    winners = set()
    for seed in range(300):
        g = play(seed)
        alive = g.alive()
        wolves = [p for p in alive if p.role.is_wolf]
        gods = [p for p in alive if p.role.is_god]
        villagers = [p for p in alive if p.role is Role.VILLAGER]
        if g.winner == "good":
            assert not wolves
        else:
            assert g.winner == "wolf" and wolves and (not gods or not villagers)
        texts = [e.text for e in g.events]
        assert sum("你用解药" in t for t in texts) <= 1
        assert sum("你用毒药" in t for t in texts) <= 1
        assert g.events[-1].kind == "end"
        winners.add(g.winner)
        assert g.day < 12
    assert winners == {"good", "wolf"}


def test_private_information_stays_private():
    g = play(7)
    seer = g.role_holder(Role.SEER)
    wolves = {p.seat for p in g.players.values() if p.role.is_wolf}
    for e in g.events:
        if "你查验了" in e.text:
            assert e.visible_to == frozenset({seer.seat})
        if e.data.get("kind") == "wolf_chat":
            assert e.visible_to and e.visible_to <= wolves
        if e.text.startswith("你的身份是"):
            assert len(e.visible_to) == 1


def test_deal_can_force_the_human_role():
    for role in set(STANDARD_12):
        players = deal(NAMES, 5, role, random.Random(1))
        assert players[4].role is role and players[4].is_human
        assert sorted(p.role.value for p in players) == sorted(r.value for r in STANDARD_12)


# ---------------------------------------------------------------- targeted rules


class Puppet:
    """Answers come from a function (kind, player, options) -> key; speeches are fixed."""

    def __init__(self, decide):
        self.decide = decide

    async def speak(self, game, player, kind, prompt):
        yield f"{player.seat}号{kind}"

    async def choose(self, game, player, kind, prompt, options, allow_skip):
        return self.decide(game, kind, player, options)


ROLES = [Role.WEREWOLF, Role.WEREWOLF, Role.WEREWOLF, Role.WEREWOLF,  # 1-4
         Role.SEER, Role.WITCH, Role.HUNTER, Role.IDIOT,  # 5-8
         Role.VILLAGER, Role.VILLAGER, Role.VILLAGER, Role.VILLAGER]  # 9-12


def make(decide, rules=None):
    players = [Player(i + 1, NAMES[i], ROLES[i]) for i in range(12)]
    puppet = Puppet(decide)
    return Game(players, {p.seat: puppet for p in players}, rules=rules or Rules(sheriff=False), rng=random.Random(0))


def run_day(game, days=1):
    async def main():
        for _ in range(days):
            game.day += 1
            await game.night()
            await game.daytime()
    asyncio.run(main())


def narrations(game):
    return [e.text for e in game.events if e.kind == "narration"]


def base(kill=9, save=False, poison=0, exile=0, check=1):
    def decide(game, kind, player, options):
        if kind == "wolf_kill":
            return kill
        if kind == "seer_check":
            return check
        if kind == "witch_save":
            return 1 if save else 2
        if kind == "witch_poison":
            return poison
        if kind == "exile_vote":
            return exile if exile in [o.key for o in options] else 0
        if kind == "hunter_shoot":
            return 1
        if kind == "badge_transfer":
            return 0
        return options[0].key if options else 0
    return decide


def test_witch_save_means_peaceful_night():
    g = make(base(kill=9, save=True))
    run_day(g)
    assert all(p.alive for p in g.players.values())
    assert any("平安夜" in t for t in narrations(g))
    assert not g.antidote


def test_poisoned_hunter_cannot_shoot_but_wolf_killed_hunter_can():
    g = make(base(kill=9, poison=7))
    run_day(g)
    assert not g.p(7).alive and not g.p(9).alive
    assert not any("开枪" in t for t in narrations(g))

    g = make(base(kill=7))
    run_day(g)
    assert not g.p(7).alive
    assert not g.p(1).alive  # hunter shot seat 1
    assert any("开枪带走了 1号" in t for t in narrations(g))


def test_seer_result_is_correct():
    g = make(base(check=2))
    run_day(g)
    assert g.seer_results == {2: True}
    g = make(base(check=10))
    run_day(g)
    assert g.seer_results == {10: False}


def test_idiot_survives_exile_but_loses_vote():
    g = make(base(kill=9, exile=8))
    run_day(g)
    idiot = g.p(8)
    assert idiot.alive and idiot.idiot_revealed and not idiot.can_vote
    assert any("白痴" in t for t in narrations(g))


def test_exile_tie_goes_to_pk_and_second_tie_means_no_exile():
    def decide(game, kind, player, options):
        keys = [o.key for o in options]
        if kind == "wolf_kill":
            return 12
        if kind == "witch_save":
            return 1  # nobody dies at night
        if kind == "witch_poison":
            return 0
        if kind == "exile_vote":
            # Odd seats vote 10, even seats vote 11 -> tie; in PK the same split repeats.
            want = 10 if player.seat % 2 else 11
            return want if want in keys else 0
        return keys[0] if keys else 0

    g = make(decide)
    run_day(g)
    speeches = [e.data for e in g.events if e.kind == "speech"]
    assert {s["seat"] for s in speeches if s["kind"] == "pk"} == {10, 11}
    assert all(p.alive for p in g.players.values())
    assert any("没有人被放逐" in t for t in narrations(g))


def test_sheriff_vote_counts_one_and_a_half():
    def decide(game, kind, player, options):
        keys = [o.key for o in options]
        if kind == "run_sheriff":
            return 1 if player.seat == 5 else 2
        if kind == "wolf_kill":
            return 12
        if kind == "witch_save":
            return 1
        if kind == "witch_poison":
            return 0
        if kind == "exile_vote":
            # 5 (sheriff) + 4 others vote 1; 5 others vote 2; the rest abstain -> 5.5 vs 5.
            want = 1 if player.seat in (5, 6, 7, 8, 9) else 2 if player.seat in (1, 3, 4, 10, 11) else 0
            return want if want in keys else 0
        return keys[0] if keys else 0

    g = make(decide, Rules(sheriff=True))
    run_day(g)
    assert g.sheriff == 5
    assert not g.p(1).alive  # 4.0 + 1.5 beats 5.0
    votes = [e for e in g.events if e.kind == "vote" and e.data["kind"] == "exile_vote"]
    assert votes[-1].data["tally"][1] == 5.5


def test_sheriff_killed_at_night_passes_the_badge():
    def decide(game, kind, player, options):
        keys = [o.key for o in options]
        if kind == "run_sheriff":
            return 1 if player.seat == 9 else 2
        if kind == "wolf_kill":
            return 9
        if kind == "witch_save":
            return 2
        if kind == "witch_poison":
            return 0
        if kind == "badge_transfer":
            return 10
        if kind == "exile_vote":
            return 0
        return keys[0] if keys else 0

    g = make(decide, Rules(sheriff=True))
    run_day(g)
    assert not g.p(9).alive and g.sheriff == 10
    lw = [e.data["seat"] for e in g.events if e.kind == "speech" and e.data["kind"] == "last_words"]
    assert lw == [9]  # first-night death gets last words


def test_discussion_order_follows_sheriff_choice():
    def decide(game, kind, player, options):
        keys = [o.key for o in options]
        if kind == "run_sheriff":
            return 1 if player.seat == 6 else 2
        if kind == "speech_order":
            return 2  # decreasing seat numbers
        if kind == "wolf_kill":
            return 12
        if kind == "witch_save":
            return 1
        if kind in ("witch_poison", "exile_vote"):
            return 0
        return keys[0] if keys else 0

    g = make(decide, Rules(sheriff=True))
    run_day(g)
    order = [e.data["seat"] for e in g.events if e.kind == "speech" and e.data["kind"] == "discussion"]
    assert order == [5, 4, 3, 2, 1, 12, 11, 10, 9, 8, 7, 6]


def test_broken_controller_does_not_break_the_game():
    class Broken(Puppet):
        async def choose(self, *a):
            raise RuntimeError("boom")

        async def speak(self, game, player, kind, prompt):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    players = deal(NAMES, None, None, random.Random(3))
    b = Broken(None)
    g = Game(players, {p.seat: b for p in players}, rng=random.Random(3))
    asyncio.run(asyncio.wait_for(g.run(), 10))
    assert g.winner in ("good", "wolf")
    assert any(e.data.get("text") == "（过）" for e in g.events if e.kind == "speech")


def test_option_labels():
    g = make(base())
    assert g.seat_options([3, 1]) == [Option(1, "1号P1"), Option(3, "3号P3")]
