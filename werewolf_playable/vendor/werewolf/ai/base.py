"""
base.py - agent 基类 + 公开信息的统计小工具
=============================================
agent 的接口只有一个：
    act(view, legal) -> action
view 是 dict（见 docs/对局记录格式.md 的"视图"一节），legal 是合法动作 dict 列表。
**agent 不得修改 view / legal**（环境里传的是引用，图快）。
"""
import random

from .. import config as C
from ..game import actions as A
from ..game.roles import WOLF, FACTION_WOLF, SEER, WITCH, HUNTER


class BaseAgent(object):
    name = "base"

    def __init__(self):
        self.rng = random.Random(0)
        self.pid = None

    def reset(self, seed):
        """每局开始由环境调用；同一个 seed 必得同一条随机序列。"""
        self.rng = random.Random(seed)

    def act(self, view, legal):
        raise NotImplementedError

    # ---- 小工具 ----
    def pick(self, legal):
        return self.rng.choice(legal)

    def pick_among(self, legal, targets, default=None):
        """
        在 legal 里挑一个 target 属于 targets 的动作 —— **均匀随机**，不是取第一个。
        （取第一个会让"无信息时的随机"退化成"永远选最小 pid"，
         会把 0 号位变成结构性靶子，整张表都会歪。）
        """
        tset = set(targets)
        cand = [a for a in legal if a.get("target") in tset]
        if cand:
            return self.rng.choice(cand)
        return default

    def pick_top(self, legal, scored):
        """
        scored: {target: score}；取分数最高者，平分随机。
        只在 legal 里出现过的 target 上挑。
        """
        cand = {}
        for a in legal:
            t = a.get("target")
            if t is not None and t in scored:
                cand.setdefault(t, scored[t])
        if not cand:
            return None
        mx = max(cand.values())
        top = [t for t, s in cand.items() if s == mx]
        t = self.rng.choice(sorted(top))
        return self.pick_among(legal, [t])


# ==================== 公开信息统计 ====================

def accuse_wolf_counts(public, day=None):
    """每个玩家被 ACCUSE(·, WOLF) 的累计次数（day=None 表示全部已发生发言）。"""
    cnt = {}
    for s in public["speeches"]:
        if day is not None and s["day"] != day:
            continue
        a = s["action"]
        if a["type"] == A.ACCUSE and a["role"] == WOLF:
            cnt[a["target"]] = cnt.get(a["target"], 0) + 1
    return cnt


def silence_counts(public):
    """每个玩家 SILENCE 的累计次数（每天发言一次，故 = 沉默天数）。"""
    cnt = {}
    for s in public["speeches"]:
        if s["action"]["type"] == A.SILENCE:
            cnt[s["pid"]] = cnt.get(s["pid"], 0) + 1
    return cnt


def accuse_role_counts(public, role):
    """每个玩家被 ACCUSE(·, role) 的累计次数（任意 role，含非狼指控）。"""
    cnt = {}
    for s in public["speeches"]:
        a = s["action"]
        if a["type"] == A.ACCUSE and a["role"] == role:
            cnt[a["target"]] = cnt.get(a["target"], 0) + 1
    return cnt


def suspicion_scores(public, targets, w_accuse=None, w_silence=None):
    """
    启发式嫌疑分：score = W_ACCUSE * 被指控为狼次数 + W_SILENCE * 沉默次数
    （权重默认取 config，W_SILENCE=0 时退化为"完全诚实"的投票口径）
    """
    if w_accuse is None:
        w_accuse = C.W_ACCUSE
    if w_silence is None:
        w_silence = C.W_SILENCE
    acc = accuse_wolf_counts(public)
    sil = silence_counts(public)
    return {t: w_accuse * acc.get(t, 0) + w_silence * sil.get(t, 0)
            for t in targets}
