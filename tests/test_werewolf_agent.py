"""Parsing of AI player output, and the agent against a fake LLM."""

import asyncio
import random

from apps.werewolf.agent import AIController, SpeechParser, parse_choice
from apps.werewolf.config import WerewolfSettings
from apps.werewolf.engine import Game, Option, deal
from apps.werewolf.personas import PERSONAS


def stream_through(parser, text, step):
    out = ""
    for i in range(0, len(text), step):
        out += parser.feed(text[i : i + step])
    return out + parser.finish()


def test_speech_parser_streams_only_the_speech():
    text = "【思考】3号像狼，我要踩他。\n【发言】我觉得3号的发言有问题，今天出3号。"
    for step in (1, 2, 5, 100):
        p = SpeechParser()
        assert stream_through(p, text, step) == "我觉得3号的发言有问题，今天出3号。"
        assert p.thought == "3号像狼，我要踩他。"


def test_speech_parser_stops_at_a_following_tag_without_leaking_it():
    text = "【思考】x【发言】我是好人。【思考】多余的"
    for step in (1, 3):
        p = SpeechParser()
        assert stream_through(p, text, step) == "我是好人。"


def test_speech_parser_without_tags_uses_whole_reply():
    p = SpeechParser()
    assert stream_through(p, "我先听听大家的。", 2) == "我先听听大家的。"


def test_parse_choice():
    opts = [Option(3, "3号"), Option(5, "5号")]
    assert parse_choice("【思考】5号可疑\n【选择】5", opts, False) == (5, "5号可疑")
    assert parse_choice("【思考】算了\n【选择】0", opts, True)[0] == 0
    assert parse_choice("【选择】0", opts, False)[0] is None  # skip not allowed
    assert parse_choice("【选择】7", opts, False)[0] is None  # not an option
    assert parse_choice("我选 3", opts, False)[0] == 3
    assert parse_choice("【选择】：3", opts, False)[0] == 3


class FakeLLM:
    provider = "fake"

    def __init__(self):
        self.calls = 0

    async def stream(self, messages, **kw):
        self.calls += 1
        task = messages[-1]["content"]
        assert "以下是到目前为止你能看到的全部信息" in task
        if "【选择】" in task:
            # pick the first listed option
            first = task.split("可选项：\n- ")[1].split("：")[0]
            reply = f"【思考】随便选\n【选择】{first}"
        else:
            reply = "【思考】先观察\n【发言】我是好人，先听后面的发言。"
        for i in range(0, len(reply), 4):
            yield reply[i : i + 4]

    async def complete(self, messages, **kw):
        return "".join([c async for c in self.stream(messages, **kw)])


def test_ai_players_can_play_a_whole_game():
    rng = random.Random(5)
    players = deal([p.name for p in PERSONAS], None, None, rng)
    llm = FakeLLM()
    s = WerewolfSettings(models=[("fake", "m")])
    ctl = {p.seat: AIController(llm, "m", PERSONAS[p.seat - 1], s) for p in players}
    game = Game(players, ctl, rng=rng)
    asyncio.run(asyncio.wait_for(game.run(), 30))
    assert game.winner in ("good", "wolf")
    speeches = [e.data["text"] for e in game.events if e.kind == "speech"]
    assert speeches and all(t == "我是好人，先听后面的发言。" for t in speeches)
    assert game.thoughts and {"seat", "thought", "output", "kind"} <= set(game.thoughts[0])
    # Each AI only sees what it may see: a villager's prompt never contains wolf chat.
    villager = next(p for p in players if p.role.value == "villager")
    from apps.werewolf.prompts import render_log
    assert "狼人夜聊" not in render_log(game, villager)


def test_saved_night_target_only_reaches_wolves_and_witch():
    """After a night where the witch saves the wolves' target, nobody else can learn who it was."""
    from apps.werewolf.prompts import user_message
    from apps.werewolf.scripted import ScriptedController

    class SaveFirstNight(ScriptedController):
        async def choose(self, game, player, kind, prompt, options, allow_skip):
            if kind == "witch_save":
                return 1
            return await super().choose(game, player, kind, prompt, options, allow_skip)

    async def one(seed):
        rng = random.Random(seed)
        players = deal([f"P{i}" for i in range(1, 13)], None, None, rng)
        bot = SaveFirstNight(rng)
        game = Game(players, {p.seat: bot for p in players}, rng=rng)
        game.day = 1
        await game.night()
        await game.daytime()
        return game

    checked = 0
    for seed in range(40):
        game = asyncio.run(one(seed))
        if not any("你用解药救了" in e.text for e in game.events):
            continue  # the wolves picked the witch herself, who may not self-save
        checked += 1
        for p in game.players.values():
            prompt = user_message(game, p, "test")
            assert prompt.count("你的身份是") <= 1  # never anyone else's role
            if p.role.is_wolf or p.role.value == "witch":
                continue
            for secret in ("狼人选择", "今晚被狼人刀", "解药", "狼人夜聊"):
                assert secret not in prompt, (seed, p.label, secret)
    assert checked >= 30
