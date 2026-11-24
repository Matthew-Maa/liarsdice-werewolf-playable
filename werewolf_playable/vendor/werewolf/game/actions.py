"""
actions.py - 动作定义与整数编码
================================
环境里所有动作都是 dict（人可读）；落盘时同时写 dict 和 int code（机器可读）。

int code 表（2026-10-23 为支持 7 人板**重排**；段位拉开、支持 pid 0..7（8 人板）×5 角色，
各段不重叠、不与固定码冲突。**旧 6 人数据用的老编码（6..35 ACCUSE、40..45 VOTE 等）
已作废**，旧 jsonl 只作数字存档、不再回放）：

    0                 SILENCE                       沉默
    1 .. 5            SELF_CLAIM(role)              = ROLE_INDEX[role]
    10..49            ACCUSE(target, role)          = 10 + target*5 + (ROLE_INDEX[role]-1)
    50..58            VOTE(target)                  = 50 + target
    59                ABSTAIN                       弃票
    60..68            KILL(target)                  = 60 + target
    70                WITCH_NONE                    女巫不用药
    71                WITCH_SAVE                    女巫用解药
    72..80            WITCH_POISON(target)          = 72 + target
    81..89            CHECK(target)                 = 81 + target
    90                NO_SHOOT                      猎人不开枪
    91..99            SHOOT(target)                 = 91 + target

target 一律是 pid（0 到 board_size()-1；当前 7 人板是 0..6），-1 表示无目标。
encode 用 offset+target 公式，天然支持任意 target；decode 用 MAX_TARGET 决定区间上界。

⚠ 为什么必须重排：原 6 人表里 ACCUSE 用 6 + target*5 占 6..35，7 人板 target=6 时
accuse(6, HUNTER)=6+30+4=40，正好撞 VOTE(0)=40 —— **6 人表塞不下第 7 人**。
故把每个动作类别拉开段位重排。8 人（pid≤7）也装得下；超过 8 人需再重排。
"""
from .roles import ALL_ROLES, ROLE_INDEX, INDEX_ROLE

# target 的 pid 上限 = 板子人数 - 1。decode 区间上界全靠它。
# task_LRS_003：改为函数每次读 C.board_size()，切换板子预设（BOARDS）后自动跟随，不假设死 7。
def _max_target():
    from .. import config as C
    return max(0, C.board_size() - 1)


def max_target():
    """当前板子的最大 target pid（= 板子人数 - 1），随 C.BOARD 实时变化。"""
    return _max_target()


# 兼容旧引用：等于"当前板子的 max_target"（模块导入时刻读到默认板）。
# 切换板子后请用 max_target() 而非本常量。
MAX_TARGET = _max_target()

# ---- 动作类型常量 ----
SILENCE = "SILENCE"
SELF_CLAIM = "SELF_CLAIM"
ACCUSE = "ACCUSE"
VOTE = "VOTE"
ABSTAIN = "ABSTAIN"
KILL = "KILL"
WITCH = "WITCH"          # {"type":"WITCH","potion":"NONE"|"SAVE"|"POISON","target":pid|-1}
CHECK = "CHECK"
SHOOT = "SHOOT"
NO_SHOOT = "NO_SHOOT"
SHERIFF_VOTE = "SHERIFF_VOTE"    # task_LRS_030：警长选举投票 {"type":"SHERIFF_VOTE","target":pid}
BADGE = "BADGE"                  # task_LRS_030：警长死时指定继承人 {"type":"BADGE","target":pid}
BADGE_DROP = "BADGE_DROP"        # task_LRS_030：警徽流失（无继承人）

# 女巫药水
POTION_NONE = "NONE"
POTION_SAVE = "SAVE"
POTION_POISON = "POISON"

# ==================== 警长动作编码（task_LRS_030）====================
# ★★★ 为什么不用"100 起"（设计书 task_LRS_029 §1.5 原写法）：
#   `features.encode_view` 里 mask 的长度是 **LEGAL_DIM = 100**（features.py:88, 274）,
#   用 `mask[A.encode(a)] = 1` 填充 ⇒ 编码 >= 100 会直接 IndexError，
#   且扩 LEGAL_DIM 会让状态向量 309→329、旧 ckpt 全失效（判 0 明令不许）。
#   ⇒ 改为**占用 0..99 里的现有空槽**（7 人板已用 80 个，空 20 个，本机制需 15 个，够）。
#   已用空槽分配见下表；**0..79 的既有编码一个都不碰**。
#
#   7 人板空槽实测：6,7,8,9, 45,46,47,48,49, 57,58, 67,68,69, 79,80, 88,89, 98,99
#     选举投票 8 个：6..9 (t=0..3), 45..48 (t=4..6), 49 (弃票)
#     警徽流转 7 个：57..62 → t=0..5 用 57,58,67,68,69,79；流失用 80
#   （取用顺序即下面的表；表是**唯一真源**，encode/decode 都查它，不做运算。）
SHERIFF_VOTE_CODES = (6, 7, 8, 9, 45, 46, 47)   # t=0..6
SHERIFF_ABSTAIN_CODE = 49                       # 弃票
BADGE_CODES = (57, 58, 67, 68, 69, 79, 80)      # t=0..6
BADGE_DROP_CODE = 88                            # 警徽流失


# ==================== 构造函数（环境内部与 baseline 共用）====================

def silence():
    return {"type": SILENCE}


def self_claim(role):
    assert role in ROLE_INDEX, role
    return {"type": SELF_CLAIM, "role": role}


def accuse(target, role):
    assert role in ROLE_INDEX, role
    return {"type": ACCUSE, "target": int(target), "role": role}


def vote(target):
    return {"type": VOTE, "target": int(target)}


def abstain():
    return {"type": ABSTAIN}


def kill(target):
    return {"type": KILL, "target": int(target)}


def witch_none():
    return {"type": WITCH, "potion": POTION_NONE, "target": -1}


def witch_save():
    return {"type": WITCH, "potion": POTION_SAVE, "target": -1}


def witch_poison(target):
    return {"type": WITCH, "potion": POTION_POISON, "target": int(target)}


def check(target):
    return {"type": CHECK, "target": int(target)}


def shoot(target):
    return {"type": SHOOT, "target": int(target)}


def no_shoot():
    return {"type": NO_SHOOT}


def sheriff_vote(target):
    """警长选举投票（task_LRS_030）。target=pid 或 None 表示弃票。"""
    if target is None:
        return {"type": SHERIFF_VOTE, "target": -1}
    return {"type": SHERIFF_VOTE, "target": int(target)}


def badge(target):
    """警长死时指定继承人（task_LRS_030）。"""
    return {"type": BADGE, "target": int(target)}


def badge_drop():
    """警徽流失（无继承人，task_LRS_030）。"""
    return {"type": BADGE_DROP}


# ==================== 整数编码 ====================

def encode(action):
    """动作 dict -> int code。未知动作直接报错（宁可炸，不要静默写成 -1）。"""
    t = action["type"]
    if t == SILENCE:
        return 0
    if t == SELF_CLAIM:
        return ROLE_INDEX[action["role"]]
    if t == ACCUSE:
        return 10 + int(action["target"]) * 5 + (ROLE_INDEX[action["role"]] - 1)
    if t == VOTE:
        return 50 + int(action["target"])
    if t == ABSTAIN:
        return 59
    if t == KILL:
        return 60 + int(action["target"])
    if t == WITCH:
        p = action["potion"]
        if p == POTION_NONE:
            return 70
        if p == POTION_SAVE:
            return 71
        if p == POTION_POISON:
            return 72 + int(action["target"])
        raise ValueError(f"未知药水 {p}")
    if t == CHECK:
        return 81 + int(action["target"])
    if t == SHOOT:
        return 91 + int(action["target"])
    if t == NO_SHOOT:
        return 90
    # ---- task_LRS_030：警长动作（占 0..99 的空槽，见文件上方码表注释）----
    if t == SHERIFF_VOTE:
        tg = int(action.get("target", -1))
        if tg < 0:
            return SHERIFF_ABSTAIN_CODE
        if tg >= len(SHERIFF_VOTE_CODES):
            raise ValueError("警长选举目标越界 pid=%d（码表只到 %d）"
                             % (tg, len(SHERIFF_VOTE_CODES) - 1))
        return SHERIFF_VOTE_CODES[tg]
    if t == BADGE:
        tg = int(action["target"])
        if tg < 0 or tg >= len(BADGE_CODES):
            raise ValueError("警徽继承目标越界 pid=%d（码表只到 %d）"
                             % (tg, len(BADGE_CODES) - 1))
        return BADGE_CODES[tg]
    if t == BADGE_DROP:
        return BADGE_DROP_CODE
    raise ValueError(f"未知动作类型 {t}")


def decode(code):
    """int code -> 动作 dict（写文档/调试/回放用）。
    区间上界由 MAX_TARGET（=板子人数-1）决定，支持最大 8 人板。"""
    c = int(code)
    if c == 0:
        return silence()
    if 1 <= c <= 5:
        return self_claim(INDEX_ROLE[c])
    if 10 <= c <= 10 + max_target() * 5 + 4:
        rest = c - 10
        return accuse(rest // 5, INDEX_ROLE[rest % 5 + 1])
    if 50 <= c <= 50 + max_target():
        return vote(c - 50)
    if c == 59:
        return abstain()
    if 60 <= c <= 60 + max_target():
        return kill(c - 60)
    if c == 70:
        return witch_none()
    if c == 71:
        return witch_save()
    if 72 <= c <= 72 + max_target():
        return witch_poison(c - 72)
    if 81 <= c <= 81 + max_target():
        return check(c - 81)
    if c == 90:
        return no_shoot()
    if 91 <= c <= 91 + max_target():
        return shoot(c - 91)
    # ---- task_LRS_030：警长动作（码表反查；未命中才报错，避免误吞旧码）----
    if c in SHERIFF_VOTE_CODES:
        return sheriff_vote(SHERIFF_VOTE_CODES.index(c))
    if c == SHERIFF_ABSTAIN_CODE:
        return sheriff_vote(None)
    if c in BADGE_CODES:
        return badge(BADGE_CODES.index(c))
    if c == BADGE_DROP_CODE:
        return badge_drop()
    raise ValueError(f"未知动作编码 {c}")


# ==================== 合法动作集 ====================

def legal_speeches(pid, alive):
    """发言合法集：SILENCE / SELF_CLAIM(5 种) / ACCUSE(每个其他存活者 × 5 种)。"""
    out = [silence()]
    for r in ALL_ROLES:
        out.append(self_claim(r))
    for t in alive:
        if t == pid:
            continue
        for r in ALL_ROLES:
            out.append(accuse(t, r))
    return out


def legal_votes(pid, alive, allow_self=False, allow_abstain=True):
    out = [vote(t) for t in alive if allow_self or t != pid]
    if allow_abstain:
        out.append(abstain())
    return out


def legal_kills(alive, wolves, allow_self_kill=False):
    if allow_self_kill:
        return [kill(t) for t in alive]
    return [kill(t) for t in alive if t not in wolves]


def legal_witch(victim, witch_pid, alive, has_antidote, has_poison,
                can_self_save=False):
    """
    女巫合法集。victim=None 表示今夜无人被刀。
    规则：同夜只能用一瓶；不可自救（自救 = 解药给自己）。
    """
    out = [witch_none()]
    if has_antidote and victim is not None:
        if can_self_save or victim != witch_pid:
            out.append(witch_save())
    if has_poison:
        for t in alive:
            if t != witch_pid:
                out.append(witch_poison(t))
    return out


def legal_checks(pid, alive, already_checked, allow_self=False, allow_recheck=True):
    out = []
    for t in alive:
        if t == pid and not allow_self:
            continue
        if (not allow_recheck) and t in already_checked:
            continue
        out.append(check(t))
    # ⚠ 禁重复验时，若存活者都验过了，合法集为空会崩（RandomAgent.choice 空序列）。
    #   此时退化为"验任意存活者"—— 保证预言家每夜总有一个合法动作，
    #   同时保留"能避开重复就避开"的偏好语义。
    if not out:
        out = [check(t) for t in alive if allow_self or t != pid]
    return out


def legal_shots(pid, alive):
    return [shoot(t) for t in alive if t != pid] + [no_shoot()]


# ==================== 警长合法集（task_LRS_030）====================

def legal_sheriff_votes(pid, alive, allow_abstain=True):
    """警长选举：投存活的其他玩家，或弃票（不能投自己，与 VOTE 同口径）。"""
    out = [sheriff_vote(t) for t in alive if t != pid]
    if allow_abstain:
        out.append(sheriff_vote(None))
    return out


def legal_badge(sheriff_pid, alive):
    """警长死时的继承指定：只能给存活的其他玩家；无人可给时只剩 BADGE_DROP。"""
    out = [badge(t) for t in alive if t != sheriff_pid]
    out.append(badge_drop())
    return out


# ==================== 展示 ====================

def describe(action):
    """动作 dict -> 中文短句（日志/报告用）。"""
    t = action["type"]
    if t == SILENCE:
        return "沉默"
    if t == SELF_CLAIM:
        from .roles import role_cn
        return f"自称{role_cn(action['role'])}"
    if t == ACCUSE:
        from .roles import role_cn
        return f"指认 {action['target']} 号是{role_cn(action['role'])}"
    if t == VOTE:
        return f"投 {action['target']} 号"
    if t == ABSTAIN:
        return "弃票"
    if t == KILL:
        return f"刀 {action['target']} 号"
    if t == WITCH:
        p = action["potion"]
        if p == POTION_NONE:
            return "不用药"
        if p == POTION_SAVE:
            return "用解药"
        return f"毒 {action['target']} 号"
    if t == CHECK:
        return f"验 {action['target']} 号"
    if t == SHOOT:
        return f"开枪带走 {action['target']} 号"
    if t == NO_SHOOT:
        return "不开枪"
    if t == SHERIFF_VOTE:
        tg = action.get("target", -1)
        return "警长票弃票" if int(tg) < 0 else f"选 {tg} 号当警长"
    if t == BADGE:
        return f"警徽交给 {action['target']} 号"
    if t == BADGE_DROP:
        return "警徽流失"
    return str(action)
