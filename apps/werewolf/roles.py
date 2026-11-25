"""Roles and the standard 12-player board (预女猎白)."""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    WEREWOLF = "werewolf"
    VILLAGER = "villager"
    SEER = "seer"
    WITCH = "witch"
    HUNTER = "hunter"
    IDIOT = "idiot"

    @property
    def zh(self) -> str:
        return ROLE_ZH[self]

    @property
    def is_wolf(self) -> bool:
        return self is Role.WEREWOLF

    @property
    def is_god(self) -> bool:
        return self in GODS


ROLE_ZH = {
    Role.WEREWOLF: "狼人",
    Role.VILLAGER: "平民",
    Role.SEER: "预言家",
    Role.WITCH: "女巫",
    Role.HUNTER: "猎人",
    Role.IDIOT: "白痴",
}

GODS = {Role.SEER, Role.WITCH, Role.HUNTER, Role.IDIOT}

# 12 人标准局：4 狼 4 民 + 预言家、女巫、猎人、白痴；屠边胜利。
STANDARD_12 = [Role.WEREWOLF] * 4 + [Role.VILLAGER] * 4 + [Role.SEER, Role.WITCH, Role.HUNTER, Role.IDIOT]

ROLE_ABILITIES = {
    Role.WEREWOLF: "每晚和狼队友商量，一起刀死一名玩家。白天要隐藏身份，可以伪装成好人甚至假跳神职。",
    Role.VILLAGER: "没有技能。靠听发言和投票找出狼人。",
    Role.SEER: "每晚可以查验一名玩家，得知他是狼人还是好人。",
    Role.WITCH: "有一瓶解药和一瓶毒药，各只能用一次。晚上会得知谁被狼人刀了，可以用解药救他，"
    "或者用毒药毒死一名玩家。同一晚不能两瓶都用，不能救自己。",
    Role.HUNTER: "出局时（被毒死除外）可以开枪带走一名玩家。",
    Role.IDIOT: "白天被投票放逐时可以翻牌免死，之后继续发言但失去投票权。晚上被刀会正常出局。",
}

RULES_SUMMARY = """12 人标准局（预女猎白）：4 名狼人，4 名平民，预言家、女巫、猎人、白痴各 1 名。
胜负（屠边）：狼人全部出局，好人胜；所有神职出局或所有平民出局，狼人胜。
流程：夜晚狼人刀人、预言家查验、女巫用药；白天先公布昨夜出局的玩家（首夜出局有遗言），
然后所有存活玩家依次发言，再投票放逐一人（被放逐者有遗言）。
第一天白天先竞选警长：想当警长的玩家上警发言，其余玩家投票。警长投票算 1.5 票，决定发言顺序并最后发言，
出局时可以把警徽移交给别人或撕掉。法官不公布死因。"""
