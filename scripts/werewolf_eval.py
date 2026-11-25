"""Decision-quality eval for werewolf AI players.

Hand-built game situations where a competent player has a clear best move. Each one is asked
several times; we report accuracy and latency per model.

    python scripts/werewolf_eval.py                                  # default model
    python scripts/werewolf_eval.py --models together:moonshotai/Kimi-K3 --runs 5
    python scripts/werewolf_eval.py --only seer_killed --show         # print the AI's reasoning
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apps.werewolf.agent import AIController  # noqa: E402
from apps.werewolf.config import WerewolfSettings  # noqa: E402
from apps.werewolf.engine import Event, Game, Option, Player  # noqa: E402
from apps.werewolf.personas import CITY, Persona, Voice  # noqa: E402
from apps.werewolf.roles import Role  # noqa: E402
from core.services import Services  # noqa: E402

NAMES = [p.name for p in CITY.personas]
NEUTRAL = Persona("测试", "男", "冷静理性，说话有条理", Voice("", ""), "#888")
W, V, S, WI, H, I = Role.WEREWOLF, Role.VILLAGER, Role.SEER, Role.WITCH, Role.HUNTER, Role.IDIOT


class Builder:
    """Write a game history by hand."""

    def __init__(self, roles: dict[int, Role], me: int) -> None:
        players = [Player(s, NAMES[s - 1], roles[s], is_human=False) for s in range(1, 13)]
        self.g = Game(players, {})
        self.me = me
        self.day, self.phase = 0, "setup"
        g = self.g
        self.add("info", f"你的身份是【{g.p(me).role.zh}】。", [me])
        if g.p(me).role.is_wolf:
            wolves = [p.seat for p in players if p.role.is_wolf]
            self.add("info", f"狼人阵营：{g.labels(wolves)}。", wolves)

    def add(self, kind, text, vis=None, /, **data):
        self.g.events.append(Event(kind, text, self.day, self.phase, frozenset(vis) if vis else None, data))

    def night(self, n):
        self.day, self.phase = n, "night"
        self.g.day, self.g.phase = n, "night"
        self.add("phase", f"第 {n} 夜")
        self.add("narration", "天黑请闭眼。")

    def dawn(self, n, deaths=()):
        self.day, self.phase = n, "day"
        self.g.day, self.g.phase = n, "day"
        self.add("phase", f"第 {n} 天")
        self.add("narration", "天亮了。")
        for s in deaths:
            self.g.p(s).alive = False
        self.add("narration", f"昨晚出局的是：{self.g.labels(deaths)}。" if deaths else "昨晚是平安夜，没有人出局。",
                 deaths=list(deaths))

    def say(self, seat, text, kind="discussion"):
        label = {"discussion": "发言", "sheriff_campaign": "警上发言", "last_words": "遗言", "wolf_chat": "狼人夜聊"}[kind]
        vis = [p.seat for p in self.g.players.values() if p.role.is_wolf] if kind == "wolf_chat" else None
        self.g.events.append(Event("speech", f"{self.g.p(seat).label}（{label}）：{text}", self.day, self.phase,
                                   frozenset(vis) if vis else None, {"seat": seat, "kind": kind, "text": text}))

    def vote(self, kind, votes: dict[int, int]):
        tally: dict[int, float] = {}
        for v, t in votes.items():
            if t:
                tally[t] = tally.get(t, 0) + (1.5 if v == self.g.sheriff else 1)
        lines = "，".join(f"{v}号投{t}号" if t else f"{v}号弃票" for v, t in votes.items())
        res = "，".join(f"{s}号 {t:g} 票" for s, t in sorted(tally.items(), key=lambda kv: -kv[1]))
        self.add("vote", f"投票结果：{lines}。得票：{res}。", kind=kind, votes=votes, tally=tally)

    def say_narr(self, text, **data):
        self.add("narration", text, **data)


@dataclass
class Scenario:
    name: str
    desc: str
    build: callable
    kind: str
    prompt: str = ""
    allow_skip: bool = False
    accept: set = field(default_factory=set)
    options: callable = None  # game, me -> [Option]; default: alive seats except me


def alive_others(g, me):
    return g.seat_options(p.seat for p in g.alive() if p.seat != me)


# ------------------------------------------------------------------ scenarios


def seer_votes_check():
    roles = {1: V, 2: W, 3: V, 4: S, 5: V, 6: WI, 7: H, 8: W, 9: W, 10: V, 11: I, 12: W}
    b = Builder(roles, 4)
    b.night(1)
    b.add("info", "你查验了 9号婷婷，他是【狼人】。", [4])
    b.dawn(1)
    b.say_narr("上警的玩家：4号周律师、9号婷婷。")
    b.say(4, "我是预言家，昨晚验了9号，查杀。警徽流5号、11号。警徽给我，今天出9号。", "sheriff_campaign")
    b.say(9, "我才是预言家，昨晚验了4号，他是狼。4号悍跳，大家别信。", "sheriff_campaign")
    b.vote("sheriff_vote", {1: 4, 2: 9, 3: 4, 5: 4, 6: 4, 7: 4, 8: 9, 10: 4, 11: 9, 12: 9})
    b.g.sheriff = 4
    b.say_narr("4号周律师 当选警长。")
    b.say(1, "我站4号，9号的对跳很仓促，警徽流都没报。")
    b.say(2, "我反而觉得9号像真预言家，4号太急了。")
    b.say(5, "4号警徽流清楚，我跟4号。")
    b.say(8, "两边都有可能，我先不站边。")
    return b


def uncontested_seer():
    roles = {1: V, 2: W, 3: W, 4: V, 5: V, 6: V, 7: H, 8: S, 9: W, 10: WI, 11: I, 12: W}
    b = Builder(roles, 6)
    b.night(1)
    b.dawn(1)
    b.say_narr("上警的玩家：8号阿杰、2号老王。")
    b.say(8, "我是预言家，昨晚验了3号，查杀。警徽流5号、10号。", "sheriff_campaign")
    b.say(2, "我上警是想听听，没有身份。", "sheriff_campaign")
    b.vote("sheriff_vote", {1: 8, 3: 2, 4: 8, 5: 8, 6: 8, 7: 8, 9: 2, 10: 8, 11: 8, 12: 2})
    b.g.sheriff = 8
    b.say_narr("8号阿杰 当选警长。")
    b.say(3, "8号乱说，我是好人，我不服。8号就是想推我出去。")
    b.say(4, "只有8号一个人跳预言家，没人对跳，我信8号。")
    b.say(9, "3号发言挺真诚的，我觉得可以再看看。")
    return b


def hunter_shoots():
    roles = {1: V, 2: W, 3: V, 4: W, 5: V, 6: WI, 7: H, 8: S, 9: W, 10: V, 11: I, 12: W}
    b = Builder(roles, 7)
    b.night(1)
    b.dawn(1)
    b.say(8, "我是预言家，昨晚验了2号，查杀。警徽流7号、10号。", "sheriff_campaign")
    b.g.sheriff = 8
    b.say_narr("只有 8号阿杰 上警，直接当选警长。")
    b.say(2, "我是好人，8号验错了吧。")
    b.vote("exile_vote", {1: 2, 2: 11, 3: 2, 4: 11, 5: 2, 6: 11, 7: 2, 9: 11, 10: 11, 11: 2, 12: 11, 8: 0})
    b.say_narr("2号老王、11号米娅 平票，进入 PK 发言。")
    b.say_narr("没有人被放逐，今天平安过去。")
    b.night(2)
    b.dawn(2, [7])
    return b


def badge_to_gold():
    roles = {1: V, 2: W, 3: W, 4: V, 5: V, 6: WI, 7: H, 8: S, 9: W, 10: V, 11: I, 12: W}
    b = Builder(roles, 8)
    b.night(1)
    b.add("info", "你查验了 5号苏苏，他是【好人】。", [8])
    b.g.seer_results[5] = False
    b.dawn(1)
    b.say(8, "我是预言家，昨晚验了5号，金水。警徽流2号、11号。", "sheriff_campaign")
    b.g.sheriff = 8
    b.say_narr("只有 8号阿杰 上警，直接当选警长。")
    b.vote("exile_vote", {1: 10, 2: 10, 3: 10, 4: 1, 5: 1, 6: 1, 7: 1, 8: 1, 9: 10, 10: 1, 11: 10, 12: 10})
    b.say_narr("10号大刘 被放逐出局。")
    b.night(2)
    b.add("info", "你查验了 2号老王，他是【狼人】。", [8])
    b.g.seer_results[2] = True
    b.dawn(2, [8])
    return b


def wolf_kills_seer():
    roles = {1: W, 2: V, 3: W, 4: V, 5: V, 6: WI, 7: H, 8: S, 9: V, 10: W, 11: I, 12: W}
    b = Builder(roles, 1)
    b.night(1)
    b.dawn(1)
    b.say(8, "我是预言家，昨晚验了3号，查杀。警徽流1号、9号。", "sheriff_campaign")
    b.g.sheriff = 8
    b.say_narr("只有 8号阿杰 上警，直接当选警长。")
    b.vote("exile_vote", {1: 8, 2: 3, 3: 8, 4: 3, 5: 3, 6: 3, 7: 3, 8: 3, 9: 3, 10: 3, 11: 3, 12: 8})
    b.g.p(3).alive = False
    b.say_narr("3号赵队长 被放逐出局。")
    b.night(2)
    b.say(10, "8号今天验的是谁还不知道，警徽流里有1号。", "wolf_chat")
    b.say(12, "同意，先处理威胁最大的那个。", "wolf_chat")
    return b


def witch_saves():
    roles = {1: V, 2: W, 3: V, 4: W, 5: V, 6: WI, 7: H, 8: S, 9: V, 10: W, 11: I, 12: W}
    b = Builder(roles, 6)
    b.night(1)
    b.add("info", "今晚被狼人刀的是 9号婷婷。", [6], victim=9)
    return b


def witch_poisons():
    roles = {1: V, 2: W, 3: V, 4: W, 5: V, 6: WI, 7: H, 8: S, 9: V, 10: W, 11: I, 12: W}
    b = Builder(roles, 6)
    b.g.antidote = False
    b.night(1)
    b.add("info", "今晚被狼人刀的是 9号婷婷。", [6])
    b.add("info", "你用解药救了 9号婷婷。", [6])
    b.dawn(1)
    b.say(8, "我是预言家，昨晚验了2号，查杀。警徽流5号、9号。", "sheriff_campaign")
    b.g.sheriff = 8
    b.say_narr("只有 8号阿杰 上警，直接当选警长。")
    b.say(2, "8号乱发查杀，我是好人。")
    b.vote("exile_vote", {1: 2, 2: 5, 3: 2, 4: 5, 5: 2, 6: 2, 7: 5, 8: 2, 9: 5, 10: 5, 11: 2, 12: 5})
    b.say_narr("2号老王、5号苏苏 平票，进入 PK 发言。")
    b.say_narr("没有人被放逐，今天平安过去。")
    b.night(2)
    b.add("info", "你的解药已经用过了，今晚看不到谁被刀。", [6])
    return b


def gold_water_not_suspicious():
    roles = {1: V, 2: W, 3: V, 4: V, 5: V, 6: WI, 7: W, 8: S, 9: H, 10: W, 11: I, 12: W}
    b = Builder(roles, 4)
    b.night(1)
    b.dawn(1)
    b.say(8, "我是预言家，昨晚验了5号，金水。警徽流3号、11号。", "sheriff_campaign")
    b.say(12, "我是预言家，昨晚验了7号，查杀。8号是悍跳的。", "sheriff_campaign")
    b.vote("sheriff_vote", {1: 8, 2: 12, 3: 8, 4: 12, 5: 8, 6: 8, 7: 8, 9: 12, 10: 12, 11: 8})
    b.g.sheriff = 8
    b.say_narr("8号阿杰 当选警长。")
    b.say(11, "5号是8号的金水，却投票给8号，这很可疑，我怀疑5号和8号是一伙的。")
    return b


def seer_killed_at_night():
    roles = {1: V, 2: W, 3: W, 4: V, 5: V, 6: V, 7: H, 8: S, 9: V, 10: WI, 11: I, 12: W}
    b = Builder(roles, 6)
    b.night(1)
    b.dawn(1)
    b.say_narr("上警的玩家：8号阿杰、12号阿宁。")
    b.say(8, "我是预言家，昨晚验了5号，金水。警徽流3号、10号。", "sheriff_campaign")
    b.say(12, "我是预言家，昨晚验了3号，金水。8号是悍跳狼。", "sheriff_campaign")
    b.vote("sheriff_vote", {1: 8, 2: 12, 3: 12, 4: 8, 5: 8, 6: 12, 7: 8, 9: 12, 10: 8, 11: 12})
    b.say_narr("8号阿杰、12号阿宁 平票，进入 PK 发言。")
    b.say_narr("没有选出警长，警徽流失。")
    b.vote("exile_vote", {1: 9, 2: 9, 3: 9, 4: 2, 5: 9, 6: 9, 7: 2, 8: 9, 9: 2, 10: 9, 11: 9, 12: 9})
    b.g.p(9).alive = False
    b.say_narr("9号婷婷 被放逐出局。")
    b.night(2)
    b.dawn(2, [8])
    b.say(12, "我是真预言家，昨晚验了10号，金水。今天大家跟我走。")
    b.say(3, "我是12号的金水，我跟12号。")
    b.say(5, "我是8号的金水。8号昨晚被刀了，狼人一般不会刀自己的悍跳队友，所以8号是真预言家，12号是悍跳狼。今天出12号。")
    return b


SCENARIOS = [
    Scenario("seer_votes_check", "预言家：放逐投票投自己查杀的人", seer_votes_check, "exile_vote", allow_skip=True, accept={9}),
    Scenario("uncontested_seer", "平民：唯一跳预言家的人查杀了 3 号", uncontested_seer, "exile_vote", allow_skip=True, accept={3}),
    Scenario("hunter_shoots", "猎人：出局开枪带走被查杀的 2 号", hunter_shoots, "hunter_shoot", allow_skip=True, accept={2}),
    Scenario("badge_to_gold", "预言家警长：出局把警徽交给金水 5 号", badge_to_gold, "badge_transfer", allow_skip=True, accept={5}),
    Scenario("wolf_kills_seer", "狼人：夜里刀掉预言家 8 号", wolf_kills_seer, "wolf_kill", accept={8},
             options=lambda g, me: g.seat_options(p.seat for p in g.alive())),
    Scenario("witch_saves", "女巫：首夜用解药救人", witch_saves, "witch_save", accept={1},
             options=lambda g, me: [Option(1, "用解药救他"), Option(2, "不救")]),
    Scenario("witch_poisons", "女巫：毒掉被查杀的 2 号", witch_poisons, "witch_poison", allow_skip=True, accept={2}),
    Scenario("gold_water", "判断：金水投给预言家不可疑", gold_water_not_suspicious, "judge",
             prompt="11号说“5号是8号的金水，却投票给8号，很可疑”。你认为5号投票给8号这件事可疑吗？",
             accept={2}, options=lambda g, me: [Option(1, "可疑"), Option(2, "正常，不可疑")]),
    Scenario("seer_killed", "平民：对跳的 8 号夜里被刀，应出 12 号", seer_killed_at_night, "exile_vote", allow_skip=True,
             accept={12}),
]


async def run_one(llm, model, ws, sc: Scenario):
    b = sc.build()
    g, me = b.g, b.g.p(b.me)
    options = sc.options(g, me.seat) if sc.options else alive_others(g, me.seat)
    ai = AIController(llm, model, NEUTRAL, ws)
    t0 = time.perf_counter()
    try:
        key = await ai.choose(g, me, sc.kind, sc.prompt, options, sc.allow_skip)
    except Exception as e:  # noqa: BLE001
        return None, time.perf_counter() - t0, f"ERROR {e}"
    thought = g.thoughts[-1]["thought"] if g.thoughts else ""
    return key, time.perf_counter() - t0, thought


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--show", action="store_true", help="print the AI's reasoning for each answer")
    args = ap.parse_args()

    ws = WerewolfSettings()
    specs = [tuple(m.split(":", 1)) for m in args.models] if args.models else ws.models
    scenarios = [s for s in SCENARIOS if not args.only or s.name in args.only]
    services = Services()
    try:
        for prov, model in specs:
            llm = services.llm(prov)
            print(f"\n### {prov}:{model}")
            total, correct, times = 0, 0, []
            for sc in scenarios:
                results = await asyncio.gather(*(run_one(llm, model, ws, sc) for _ in range(args.runs)))
                ok = sum(1 for k, _, _ in results if k in sc.accept)
                total += len(results)
                correct += ok
                times += [t for _, t, _ in results]
                picks = ",".join(str(k) for k, _, _ in results)
                print(f"  {'✓' if ok == len(results) else '✗' if ok == 0 else '~'} {ok}/{len(results)} {sc.desc:28s} 选了 {picks}")
                if args.show:
                    for k, _, th in results:
                        print(f"      → {k}: {th[:300]}")
            print(f"  合计 {correct}/{total} = {100 * correct / total:.0f}%   平均每次决策 {statistics.mean(times):.1f}s")
    finally:
        await services.aclose()


if __name__ == "__main__":
    asyncio.run(main())
