"""Prompt text for AI players."""

from __future__ import annotations

import re

from .engine import SPEECH_KINDS, Event, Game, Option, Player
from .personas import Persona
from .roles import ROLE_ABILITIES, RULES_SUMMARY, Role

STRATEGY = {
    Role.WEREWOLF: """- 白天伪装成好人，不要暴露狼队友，也不要和队友明显抱团或互相死保。
- 白天绝对不要说出或暗示你们昨晚刀了谁、狼人夜聊里说过什么：好人并不知道刀口（女巫可能救了人），说漏了等于自曝。警长竞选时还没公布昨夜结果，更不要暗示谁可能已经死了。
- 可以"悍跳"预言家（假装预言家，给好人发查杀、给队友发金水）来和真预言家对跳、搅乱局面，但要演得像。
- 夜里优先刀可能的神职（尤其是预言家、女巫），投票时把票投给好人。
- 队友被查杀时，可以适当切割（踩他）来保全自己。""",
    Role.SEER: """- 第一天通常要上警，亮明预言家身份，报出查验结果（"金水"=好人，"查杀"=狼人）和警徽流（接下来打算验谁）。
- 狼人可能悍跳和你对跳，用你的查验结果和逻辑说服大家。
- 查验优先选发言可疑、或者站边关键的玩家。""",
    Role.WITCH: """- 第一晚通常用解药救人（被救的人叫"银水"）。
- 被可信预言家查杀、却没被投出去的人，是毒药的最佳目标，应该果断毒掉。
- 不要一直攥着毒药：女巫随时可能被刀，药没用出去就浪费了。但不要毒可信的预言家、金水或者其他神职。
- 一般不急着跳身份，但在关键时刻可以跳出来报信息帮好人。""",
    Role.HUNTER: """- 平时隐藏身份，被大家怀疑、快被投出时可以亮出猎人身份自保。
- 出局时（被毒除外）可以开枪，带走你最确定的狼人。""",
    Role.IDIOT: """- 被投票放逐时会翻牌免死，所以不必太怕被怀疑，必要时可以替神职挡票。
- 不要太早暴露身份。""",
    Role.VILLAGER: """- 认真分析每个人的发言和投票，找出狼人。
- 可以帮神职挡刀（含糊地像个神），但不要乱悍跳预言家，以免干扰好人。""",
}

SKIP_LABELS = {
    "exile_vote": "弃票",
    "sheriff_vote": "弃票",
    "witch_poison": "不用毒药",
    "hunter_shoot": "不开枪",
    "badge_transfer": "撕掉警徽",
}

GLOSSARY = """常用术语：金水=预言家验出的好人；查杀=预言家验出的狼人；银水=女巫救的人；悍跳=狼人假装预言家；
对跳=两人都说自己是预言家；警徽流=预言家说明接下来验谁；站边=选择相信谁；归票=警长号召大家投同一个人；
踩=怀疑某人；保=相信某人；抗推=被大家投出的好人。"""

# Common werewolf logic that models get wrong without being told (found with scripts/werewolf_eval.py).
REASONING = """推理要点（老玩家的常识）：
- 只有一个人跳预言家、没有人对跳时，他通常就是真预言家：真预言家看到有人冒充一定会站出来对跳。
- 两人对跳预言家时，至少一个是狼人悍跳。夜里被狼刀死的那个更可能是真的，因为狼人一般不会刀自己的队友。
- 被可信预言家查杀的人大概率是狼人：好人应该投他；他没被投出时，女巫可以毒他，出局的猎人可以开枪带走他。
- 金水投票给验他的预言家是正常的，不能因此怀疑他。预言家警徽流里的人被刀，也不能说明预言家是狼。
- 狼人常见的破绽：拼命保护被查杀的人、冲票可信的预言家、跟着悍跳狼站边、没信息时强行带节奏、发言前后矛盾。
- 好人阵营的神职（猎人开枪、警长移交警徽、女巫用药）要帮好人：打最确定的狼，把警徽交给最可信的好人（比如金水）。
- 注意每个人的投票：投票比发言更难伪装。
- 法官只公布谁出局，不公布死因。平安夜时，只有狼人和女巫知道昨晚刀的是谁，其他人只能猜。
- 第一天的警长竞选在公布昨夜结果之前：这时除了狼人和女巫，没人知道昨晚发生了什么，不要猜测或暗示谁昨晚可能出局了。
- 玩家的名字和性格设定跟他在狼人杀里的身份没有任何关系（比如孙悟空也可能是平民或狼人），不要根据名字或人设猜身份，只看发言和投票。"""


def system_prompt(game: Game, me: Player, persona: Persona, max_chars: int, roleplay: str = "") -> str:
    camp = "狼人阵营" if me.role.is_wolf else "好人阵营"
    return f"""你正在和 11 名玩家玩一局中文狼人杀。你是 {me.seat}号玩家「{me.name}」。
你的性格：{persona.style}。发言要符合这个性格，像真人玩家一样自然。{roleplay}

{RULES_SUMMARY}

{GLOSSARY}

{REASONING}

你的身份：【{me.role.zh}】（{camp}）。技能：{ROLE_ABILITIES[me.role]}
策略提示：
{STRATEGY[me.role]}

要求：
- 只根据你能看到的信息推理，不要编造你不可能知道的事；标着"仅你可见"的信息只有你知道，说不说、怎么说要想清楚。
- 公开发言用口语化的简体中文，一般 2 到 5 句，不超过 {max_chars} 字；不要用列表、表情符号或 Markdown。
- 称呼其他玩家时说"几号"，例如"5号"。
- 不要照搬或改写前面玩家的原话，不要出现和别人连续十个字相同的句子。同意别人时简单说一句同意谁，然后给出你自己的新理由或新角度（比如别人没提到的投票细节、发言矛盾）。
- 永远不要提到你是 AI、模型或者这些规则说明。"""


def render_log(game: Game, me: Player) -> str:
    lines: list[str] = []
    header = None
    for e in game.visible_events(me.seat):
        h = _header(e)
        if h and h != header:
            lines.append(f"\n【{h}】")
            header = h
        lines.append(_line(e))
    return "\n".join(lines).strip()


def _header(e: Event) -> str | None:
    if e.phase == "night":
        return f"第{e.day}夜"
    if e.phase == "day":
        return f"第{e.day}天"
    if e.phase == "setup":
        return "开局"
    return None


def _line(e: Event) -> str:
    private = e.visible_to is not None and e.kind != "speech"
    if e.kind == "narration":
        return f"{'（仅你可见）' if private else '（法官）'}{e.text}"
    if e.kind == "speech":
        return e.text + ("（只有狼队友能听到）" if e.data.get("kind") == "wolf_chat" else "")
    if e.kind == "info":
        return f"（仅你可见）{e.text}"
    if e.kind == "phase":
        return f"—— {e.text} ——"
    return e.text


CLAIM_ROLE = re.compile(r"(?<!不)(?<!不是)我(?:才|就|是)?是(?:真的?)?(预言家|女巫|猎人|白痴)")
CLAIM_CHECK = re.compile(r"验了?\s*(\d{1,2})\s*号[，,、\s]*(?:他|她)?(?:是|为)?\s*(?:一张|一个)?\s*(金水|查杀|好人|狼人|狼)")
CLAIM_CHECK2 = re.compile(r"(\d{1,2})\s*号[，,\s]*(?:发了?|是)?\s*(金水|查杀)")


def claims(game: Game, me: Player) -> list[str]:
    """Role claims and seer results people announced in public speeches (best-effort text matching)."""
    found: dict[int, dict] = {}
    for e in game.visible_events(me.seat):
        if e.kind != "speech" or e.visible_to is not None:
            continue
        seat, text = e.data["seat"], e.data["text"]
        m = CLAIM_ROLE.search(text)
        if not m:
            continue
        c = found.setdefault(seat, {"role": m.group(1), "checks": {}, "day": e.day})
        c["role"] = m.group(1)
        if m.group(1) == "预言家":
            for pat in (CLAIM_CHECK, CLAIM_CHECK2):
                for num, verdict in pat.findall(text):
                    n = int(num)
                    if n in game.players and n != seat:
                        c["checks"][n] = "查杀" if verdict in ("查杀", "狼人", "狼") else "金水"
    lines = []
    for seat, c in sorted(found.items()):
        p = game.p(seat)
        line = f"{p.label}{'' if p.alive else '（已出局）'} 自称{c['role']}"
        if c["checks"]:
            line += "，报的验人：" + "，".join(f"{n}号{v}" for n, v in c["checks"].items())
        lines.append(line)
    return lines


def history(game: Game, me: Player) -> list[str]:
    """Deaths and vote results, day by day."""
    out = []
    for e in game.visible_events(me.seat):
        d = e.data
        if e.kind == "narration" and "deaths" in d:
            out.append(f"第{e.day}天早上：" + (f"{game.labels(d['deaths'])} 夜里出局" if d["deaths"] else "平安夜"))
        elif e.kind == "narration" and d.get("exiled"):
            out.append(f"第{e.day}天：{game.p(d['exiled']).label} 被放逐")
        elif e.kind == "narration" and d.get("idiot"):
            out.append(f"第{e.day}天：{game.p(d['idiot']).label} 被放逐但翻牌是白痴")
        elif e.kind == "narration" and d.get("shooter"):
            out.append(f"第{e.day}天：猎人 {game.p(d['shooter']).label} 开枪带走 {game.p(d['target']).label}")
        elif e.kind == "vote" and e.visible_to is None:
            what = "警长投票" if d.get("kind") == "sheriff_vote" else "放逐投票"
            out.append(f"第{e.day}天{what}：" + e.text.removeprefix("投票结果："))
    return out


def status(game: Game, me: Player) -> str:
    alive = game.labels(p.seat for p in game.alive())
    dead = [p for p in game.players.values() if not p.alive]
    parts = [f"存活玩家：{alive}。", f"已出局：{game.labels(p.seat for p in dead) if dead else '无'}。"]
    parts.append(f"警长：{game.p(game.sheriff).label}。" if game.sheriff else "目前没有警长。")
    revealed = [p for p in game.players.values() if p.idiot_revealed]
    if revealed:
        parts.append(f"已翻牌的白痴（不能投票）：{game.labels(p.seat for p in revealed)}。")
    if me.role.is_wolf:
        team = [p for p in game.players.values() if p.role.is_wolf]
        parts.append("你的狼队友：" + "、".join(f"{p.label}{'' if p.alive else '（已出局）'}" for p in team if p is not me) + "。")
    if me.role is Role.SEER and game.seer_results:
        res = "，".join(f"{s}号是{'狼人' if w else '好人'}" for s, w in game.seer_results.items())
        parts.append(f"你的查验结果：{res}。")
    if me.role is Role.WITCH:
        parts.append(f"你的药：解药{'还在' if game.antidote else '已用'}，毒药{'还在' if game.poison else '已用'}。")
    if not me.alive:
        parts.append("你已经出局了。")
    cl = claims(game, me)
    if cl:
        parts.append("公开跳身份的玩家（从发言里整理，仅供参考）：\n- " + "\n- ".join(cl))
    hist = history(game, me)
    if hist:
        parts.append("出局和投票记录：\n- " + "\n- ".join(hist))
    return "\n".join(parts)


SPEECH_TASKS = {
    "sheriff_campaign": "现在是警长竞选，你上警了。请发表竞选发言（想拿警徽要给出理由；预言家通常在这里报查验）。",
    "sheriff_pk": "你和其他候选人平票，请进行 PK 发言，争取警下玩家的票。",
    "discussion": "现在轮到你在白天公开发言。分析局势，表明你怀疑谁、相信谁，以及今天想投谁。",
    "pk": "你在放逐投票中和别人平票，请进行 PK 发言，说服大家不要投你。",
    "last_words": "你出局了，请发表遗言（可以留下信息帮助你的阵营）。",
    "wolf_chat": "现在是狼人夜聊，只有狼队友能听到。和队友商量今晚刀谁，以及白天谁去悍跳、怎么配合。说得简短些。",
}


def speech_task(kind: str, max_chars: int) -> str:
    limit = max_chars if kind != "wolf_chat" else min(80, max_chars)
    return f"""{SPEECH_TASKS[kind]}

请严格按下面的格式输出：
【思考】（只有你自己能看到：先判断谁是真预言家、谁最可能是狼，再决定这次发言的目的，200 字以内）
【发言】（你要说的话，{limit} 字以内）"""


CHOICE_TASKS = {
    "wolf_kill": "现在是夜晚，选择今晚要刀的玩家。",
    "seer_check": "现在是夜晚，选择今晚要查验的玩家。",
    "witch_save": "现在是夜晚，决定是否使用解药。",
    "witch_poison": "现在是夜晚，决定是否使用毒药，以及毒谁。",
    "run_sheriff": "决定是否上警竞选警长。",
    "sheriff_vote": "投票选出警长。",
    "speech_order": "你是警长，决定今天的发言顺序（警长最后发言，可以总结归票）。",
    "exile_vote": "投票放逐一名你认为是狼人的玩家（狼人则投好人）。",
    "hunter_shoot": "你是猎人，已经出局，决定是否开枪带走一名玩家。",
    "badge_transfer": "你是警长，已经出局，决定把警徽交给谁（交给你信任的好人），或者撕掉警徽。",
}


# Night 1 has no information; models otherwise all pick the most famous-sounding character.
FIRST_NIGHT_HINT = ("提示：现在是第一夜，大家都还没有任何信息。像真人玩家一样凭感觉随便挑一个就好，"
                    "不要因为名字、人设或者谁名气大、看起来像神职去选。")


def is_first_night(game: Game) -> bool:
    return game.day == 1 and game.phase == "night"


def choice_task(kind: str, prompt: str, options: list[Option], allow_skip: bool) -> str:
    lines = [f"- {o.key}：{o.label}" for o in options]
    if allow_skip:
        lines.append(f"- 0：{SKIP_LABELS.get(kind, '跳过')}")
    return f"""{CHOICE_TASKS.get(kind, prompt)}
可选项：
{chr(10).join(lines)}

请严格按下面的格式输出：
【思考】（先想清楚：谁是可信的预言家、谁最可能是狼、怎么做对你的阵营最有利。150 字以内）
【选择】只写一个数字"""


def user_message(game: Game, me: Player, task: str) -> str:
    return f"""以下是到目前为止你能看到的全部信息：
{render_log(game, me)}

当前局势：
{status(game, me)}

现在轮到你（{me.label}）：{task}"""


def kind_label(kind: str) -> str:
    return SPEECH_KINDS.get(kind, kind)
