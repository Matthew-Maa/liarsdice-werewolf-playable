"""
roles.py - 角色与阵营
=====================
5 种角色、2 个阵营。角色索引（ROLE_INDEX）是动作编码的一部分，**顺序不能改**：
    1=WOLF  2=VILLAGER  3=WITCH  4=SEER  5=HUNTER
"""

WOLF = "WOLF"
VILLAGER = "VILLAGER"
WITCH = "WITCH"
SEER = "SEER"
HUNTER = "HUNTER"

# 顺序即编码索引，禁止改动
ALL_ROLES = (WOLF, VILLAGER, WITCH, SEER, HUNTER)
ROLE_INDEX = {r: i + 1 for i, r in enumerate(ALL_ROLES)}   # 1..5
INDEX_ROLE = {i + 1: r for i, r in enumerate(ALL_ROLES)}

FACTION_WOLF = "wolf"
FACTION_GOOD = "good"

ROLE_CN = {
    WOLF: "狼人",
    VILLAGER: "平民",
    WITCH: "女巫",
    SEER: "预言家",
    HUNTER: "猎人",
}
FACTION_CN = {FACTION_WOLF: "狼人阵营", FACTION_GOOD: "好人阵营"}

GOD_ROLES = (WITCH, SEER, HUNTER)
VILLAGER_ROLES = (VILLAGER,)


def faction_of(role):
    """角色 -> 阵营。"""
    return FACTION_WOLF if role == WOLF else FACTION_GOOD


def is_wolf(role):
    return role == WOLF


def is_god(role):
    return role in GOD_ROLES


def role_cn(role):
    return ROLE_CN.get(role, role)


def faction_cn(f):
    return FACTION_CN.get(f, f)
