"""
rules.py - 纯规则函数（无状态，方便单测逐条打）
================================================
这里只放"给定输入算输出"的函数，不碰随机数、不碰日志。
env.py 只负责按顺序调用它们。
"""
from .roles import WOLF, GOD_ROLES, VILLAGER_ROLES, FACTION_WOLF, FACTION_GOOD, faction_of

# 死因常量（也写进 jsonl，是公开信息）
CAUSE_WOLF = "WOLF_KILL"      # 被狼刀
CAUSE_POISON = "POISON"       # 被女巫毒
CAUSE_VOTE = "VOTE"           # 被投票放逐
CAUSE_SHOT = "SHOT"           # 被猎人枪杀
CAUSE_NONE = "NONE"


# ==================== 胜负 ====================

def _split(roles_alive):
    wolves = [p for p, r in roles_alive.items() if r == WOLF]
    gods = [p for p, r in roles_alive.items() if r in GOD_ROLES]
    villagers = [p for p, r in roles_alive.items() if r in VILLAGER_ROLES]
    return wolves, gods, villagers


def check_winner(roles_alive, mode=None):
    """
    roles_alive: dict {pid: role}，只含存活玩家。
    返回 FACTION_WOLF / FACTION_GOOD / None（未结束）。

    口径（2026-10-23 17:20 项目作者改定，默认）：
      **parity**：狼人全灭 -> good；**狼人数 >= 好人数（神职算好人）-> wolf**。
    旧口径（只为复现 17:07 那版数据保留）：
      **annihilation**（屠边）：狼全灭 -> good；神职全灭 或 平民全灭 -> wolf。
    """
    from .. import config as C
    mode = mode or C.WIN_MODE
    wolves, gods, villagers = _split(roles_alive)
    n_wolves = len(wolves)
    n_good = len(roles_alive) - n_wolves

    if n_wolves == 0:
        return FACTION_GOOD
    if mode == "parity":
        return FACTION_WOLF if n_wolves >= n_good else None
    if mode == "annihilation":
        if len(gods) == 0 or len(villagers) == 0:
            return FACTION_WOLF
        return None
    raise ValueError("未知胜负口径 %r" % (mode,))


def winner_reason(roles_alive, mode=None):
    """给胜负一句可读的原因（写进 jsonl，报告直接用）。"""
    from .. import config as C
    mode = mode or C.WIN_MODE
    wolves, gods, villagers = _split(roles_alive)
    if len(wolves) == 0:
        return "狼人全灭"
    if mode == "parity":
        if len(wolves) >= len(roles_alive) - len(wolves):
            return "狼人达到人数均势"
        return "未结束"
    if mode == "annihilation":
        if len(gods) == 0:
            return "神职全灭"
        if len(villagers) == 0:
            return "平民全灭"
        return "未结束"
    raise ValueError("未知胜负口径 %r" % (mode,))


# ==================== 投票 ====================

def tally_votes(votes, alive, sheriff=None, sheriff_weight=1.0):
    """
    votes: list of (voter_pid, target_pid_or_None)
    alive: 参与投票的存活玩家列表
    sheriff: 警长 pid（task_LRS_030）。**默认 None = 不加权 = 与改动前逐位一致**。
    sheriff_weight: 警长票权（仅 sheriff 不为 None 时生效）

    返回 dict：
      counts    {pid: 票数}
      max_votes 最高票数
      top       得票最高者列表
      eliminated 出局者 pid 或 None
      tie       是否平票
      abstain   弃票数

    ★ 为什么用浮点 counts（task_LRS_030）：警长 1.5 票会产生 .5 票。
      浮点比较用 `mx = max(...)` + `c == mx` 在同一批浮点数上是安全的
      （它们来自同一次加法序列，不存在"应相等却差 1 ulp"的情况：
       每个 counts 值都是 0.0 / 1.0 / 1.5 这类二进制可精确表示的数，
       或它们的整数和，因此严格相等成立）。
      ★★ sheriff=None 时完全走原路径（加 1.0 与加 1 整数结果相同），不引入任何浮点。
    """
    counts = {p: 0 for p in alive}
    abstain = 0
    for voter, tgt in votes:
        if tgt is None:
            abstain += 1
        else:
            # ★ 只有传了 sheriff 且投票人正是警长时才加权；否则与原逻辑一模一样
            if sheriff is not None and voter == sheriff and sheriff_weight != 1.0:
                counts[tgt] = counts.get(tgt, 0) + sheriff_weight
            else:
                counts[tgt] = counts.get(tgt, 0) + 1
    if not counts:
        return {"counts": counts, "max_votes": 0, "top": [],
                "eliminated": None, "tie": False, "abstain": abstain}
    mx = max(counts.values())
    top = [p for p, c in counts.items() if c == mx]
    if mx == 0:
        # 全员弃票：等同平票，无人出局
        return {"counts": counts, "max_votes": 0, "top": [],
                "eliminated": None, "tie": True, "abstain": abstain}
    if len(top) > 1:
        return {"counts": counts, "max_votes": mx, "top": sorted(top),
                "eliminated": None, "tie": True, "abstain": abstain}
    return {"counts": counts, "max_votes": mx, "top": top,
            "eliminated": top[0], "tie": False, "abstain": abstain}


def tally_sheriff_votes(votes, alive, weight=None):
    """警长选举计票（task_LRS_030）。votes: list of (voter_pid, target_pid_or_None)。

    ★ 与 tally_votes 的区别：**这轮没有 1.5 倍权重**（选警长时还没人当警长），
      所以直接用整数计数；平票返回 tie=True，由 env 决定"本局无警长"。
    """
    counts = {p: 0 for p in alive}
    abstain = 0
    for _, tgt in votes:
        if tgt is None:
            abstain += 1
        else:
            counts[tgt] = counts.get(tgt, 0) + 1
    if not counts:
        return {"counts": counts, "max_votes": 0, "top": [],
                "eliminated": None, "tie": False, "abstain": abstain}
    mx = max(counts.values())
    top = [p for p, c in counts.items() if c == mx]
    if mx == 0:
        return {"counts": counts, "max_votes": 0, "top": [],
                "eliminated": None, "tie": True, "abstain": abstain}
    if len(top) > 1:
        return {"counts": counts, "max_votes": mx, "top": sorted(top),
                "eliminated": None, "tie": True, "abstain": abstain}
    return {"counts": counts, "max_votes": mx, "top": top,
            "eliminated": top[0], "tie": False, "abstain": abstain}


# ==================== 夜晚死亡结算 ====================

def resolve_night(kill_target, saved, poison_target):
    """
    kill_target: 狼刀目标 pid 或 None
    saved:       女巫是否用了解药救该目标
    poison_target: 女巫毒的目标 pid 或 None
    返回 list of (pid, cause)，按 pid 升序；同一人既被刀又被毒 -> cause 记 POISON
    （毒优先：这是"被毒死不开枪"的口径落点）。
    """
    deaths = {}
    if kill_target is not None and not saved:
        deaths[kill_target] = CAUSE_WOLF
    if poison_target is not None:
        deaths[poison_target] = CAUSE_POISON     # 覆盖刀，毒优先
    return sorted(deaths.items())


def hunter_can_shoot(role, cause):
    """猎人开枪资格：被刀/被票出可开；被毒不可开；被枪杀（理论上不存在双猎人）也不可。"""
    if role != "HUNTER":
        return False
    if cause == CAUSE_POISON:
        return False
    return cause in (CAUSE_WOLF, CAUSE_VOTE)
