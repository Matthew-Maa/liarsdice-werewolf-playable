# -*- coding: utf-8 -*-
"""四个研究对手（骰子）。

规则内核复用参考实现/研究线的 logic.py（is_valid_bid / binomial_probability_fast /
advanced_first_bid / advanced_raise_bid），只重写"决策"这一层。

★ 对外说明的口径：
  · 这里的胜率数字来自研究实验（1v1、5 颗、1 为百搭、特定对手族），
    不等于对本游戏里某个真人的胜率。
  · "记忆版"在原卡里叫四档，实际只有三档（mid 与 long 实现等价，DZ_134 §四）。
"""
import math
import os
import random
import sys
import threading
from pathlib import Path

from vendor.liarsdice.game.logic import (
    is_valid_bid, binomial_probability_fast,
    advanced_first_bid, advanced_raise_bid,
)

AI_INFO = {
    "baseline": {
        "name": "阿诚（基线）",
        "agent_id": "liarsdice.baseline.heuristic",
        "agent_version": "v1",
        "card": "参考实现 MediumAI（logic.py 诚实启发式）",
        "level": "入门 · 诚实打法",
        "desc": "按自己手牌和二项概率叫牌/开盅，不记你的习惯，也不会诈唬。",
    },
    "peak": {
        "name": "峰顶（+0.10 门控）",
        "agent_id": "liarsdice.peak.memory_gate010",
        "agent_version": "DZ-133-fixed",
        "card": "DZ_133 §三：记忆 + 固定门控 +0.10（新棋盘峰顶 71.70%）",
        "level": "研究口径最强 · 高门槛族 71.7%（本作是行为层重实现，不保证在本作里最强）",
        "desc": "在诚实启发式上挂 +0.10 固定门控，并记住你过去几局爱不爱诈唬、爱不爱开盅。研究数字来自原实验的特定对手族，不是对本作其他 AI 的排名。",
    },
    "memory": {
        "name": "记事本（三档记忆）",
        "agent_id": "liarsdice.memory.3tier",
        "agent_version": "DZ-129/134",
        "card": "DZ_129 / DZ_134：记忆型对手（三档，不是四档）",
        "level": "研究型 · 会按你的开盅率调自己",
        "desc": "记录你的开盅频率，按 DZ_129 公式对称调整自己的挑战门槛：你不爱开，它就更爱开。",
    },
    "bayes": {
        "name": "读牌员（贝叶斯）",
        "agent_id": "liarsdice.bayes.betabinomial",
        "agent_version": "DZ-054-bayes",
        "card": "DZ_054 bayes 臂：贝叶斯对手建模（Beta-Binomial 手牌后验）",
        "level": "研究型 · 每局开盅后更新对你的判断",
        "desc": "把你的手牌当成未知参数，用 Beta 后验估计「你下一颗骰子出某点数」的概率，再算叫牌成真的概率。",
    },
}

# ---- 真实权重档（Liar's dice 研究线；rule1=1 是百搭，与本游戏一致）----
# 胜率来自 research_line/results/special/task_DZ_026/anchor_eval_fix20261014.md：
# 5000 局/格、AI 坐 P0、对修复版 MediumAI、seed=42、95% Wilson CI。
RESEARCH_ROOT = Path(__file__).resolve().parents[2] / "Liar's dice不完全信息博弈初探"
RESEARCH_MODEL_DIR = RESEARCH_ROOT / "models"
RESEARCH_INFO = {
    "baseline": {
        "name": "阿诚（研究权重·入门）",
        "agent_id": "liarsdice.research.lstm_bc",
        "agent_version": "lstm_bc_special.pth",
        "card": "Liar's dice 研究线 LSTM-BC（百搭线）；5000 局锚 39.12% [37.78,40.48] vs 修复版 MediumAI",
        "level": "入门 · 真实权重",
    },
    "peak": {
        "name": "老江湖（研究权重·进阶）",
        "agent_id": "liarsdice.research.lstm_bc",
        "agent_version": "lstm_bc_special_fix20261014.pth",
        "card": "Liar's dice 研究线 LSTM-BC 修复版（百搭线）；5000 局锚 47.54% [46.16,48.93]",
        "level": "进阶 · 真实权重",
    },
    "memory": {
        "name": "记忆师（研究权重·高手）",
        "agent_id": "liarsdice.research.dqn_lstm",
        "agent_version": "dqn_lstm_rand_special_fix20261014.pth",
        "card": "Liar's dice 研究线 LSTM-DQN（随机初始化，百搭线）；5000 局锚 56.80% [55.42,58.17]",
        "level": "高手 · 真实权重",
    },
    "bayes": {
        "name": "读心者（研究权重·专家）",
        "agent_id": "liarsdice.research.dqn_lstm",
        "agent_version": "dqn_lstm_bc_special_fix20261014.pth",
        "card": "Liar's dice 研究线 LSTM-DQN（BC 热启动，百搭线）；5000 局锚 57.52% [56.14,58.88]（与前一档 CI 重叠）",
        "level": "专家 · 真实权重",
    },
}
RESEARCH_SPECS = {
    "baseline": {"kind": "bc", "file": "lstm_bc_special.pth"},
    "peak": {"kind": "bc", "file": "lstm_bc_special_fix20261014.pth"},
    "memory": {"kind": "dqn", "file": "dqn_lstm_rand_special_fix20261014.pth"},
    "bayes": {"kind": "dqn", "file": "dqn_lstm_bc_special_fix20261014.pth"},
}
_RESEARCH_CACHE = {}
_RESEARCH_LOCK = threading.Lock()


# ==================== 共用小工具 ====================

def counts(own):
    c = [0] * 7
    for d in own:
        c[d] += 1
    return c


def best_face(stats, special):
    return max(range(2, 7), key=lambda f: stats[f] + (stats[1] if special else 0))


def match_count(stats, face, special):
    if face == 1:
        return stats[1]
    return stats[face] + (stats[1] if special else 0)


def bid_true_prob(stats, n, special, qty, face):
    """给定自己手牌，对手 n 颗骰子把叫牌补足的概率 P(X >= need)。

    p = 2/6（百搭：目标面或 1）或 1/6（无百搭）。
    注意：研究线的 `binomial_probability_fast` 是"恰好 k 个"的概率，
    这里要的是尾概率，所以自己算。
    """
    need = qty - match_count(stats, face, special)
    if need <= 0:
        return 1.0
    if need > n:
        return 0.0
    p = (2.0 / 6.0) if special else (1.0 / 6.0)
    q = 1.0 - p
    return sum(math.comb(n, k) * (p ** k) * (q ** (n - k))
               for k in range(need, n + 1))


def safe_first_bid(stats, n, special):
    qty, face = advanced_first_bid(stats, n, special)
    if qty > 2 * n:
        qty = 2 * n
    while not is_valid_bid(qty, face, 0, 0, 1, special):
        qty += 1
        if qty > 2 * n:
            qty, face = (2 if face == 1 else 3), best_face(stats, special)
            break
    return qty, face


def safe_raise(stats, last_q, last_f, n, special, aggression=0):
    qty, face = advanced_raise_bid(stats, last_q, last_f, n, special)
    qty += int(aggression)
    if qty > 2 * n:
        return None
    guard = 0
    while not is_valid_bid(qty, face, last_q, last_f, 0, special):
        qty += 1
        guard += 1
        if qty > 2 * n or guard > 20:
            return None
    return qty, face


def minimal_raises(last_q, last_f, n, special, extra=1):
    """返回最小的若干合法抬叫（不含"开"）。"""
    out = []
    for dq in range(0, extra + 1):
        q = last_q + dq
        for f in range(1, 7):
            if is_valid_bid(q, f, last_q, last_f, 0, special):
                out.append((q, f))
    # 最小优先：数量小、点数小（1 当最大）
    out.sort(key=lambda qf: (qf[0], 7 if qf[1] == 1 else qf[1]))
    return out


class BaseAI(object):
    name = "base"
    key = "baseline"

    def __init__(self):
        self.i_made_last_bid = False
        self.i_challenged = False
        self._last_hist = []

    def start_game(self):
        self.i_made_last_bid = False
        self.i_challenged = False
        self._last_hist = []

    def decide(self, state):
        raise NotImplementedError

    def on_challenge_result(self, bid_qty, actual_count):
        pass

    def on_dice_revealed(self, payload, seat):
        pass

    # 观测"上一手是不是对手叫的"
    def _note_state(self, state):
        hist = state.get("history") or []
        self._last_hist = hist
        if hist and hist[-1][2] is False:
            # 轮到我们时，上一手通常是对手叫的；此时"最后一手"不是我们的
            self.i_made_last_bid = False

    def _call(self, qty, face):
        self.i_made_last_bid = True
        return ("call", int(qty), int(face))

    def _challenge(self):
        self.i_challenged = True
        return ("challenge", 0, 0)


# ==================== 1. 基线：诚实启发式 ====================

class BaselineAI(BaseAI):
    key = "baseline"
    name = AI_INFO["baseline"]["name"]

    def decide(self, state):
        self._note_state(state)
        own, n = state["own_dice"], state["dice_num"]
        special = state["special_rule"]
        stats = counts(own)
        if state["first_turn"]:
            qty = max(3, stats[best_face(stats, special)] + random.randint(0, 2))
            if qty > 2 * n:
                qty = 2 * n
            return self._call(qty, best_face(stats, special))
        p = bid_true_prob(stats, n, special, state["last_qty"], state["last_face"])
        if p < 0.30:
            return self._challenge()
        bf = best_face(stats, special)
        if bf > state["last_face"] and random.random() < 0.6:
            qty, face = state["last_qty"] + 1, bf
        else:
            qty, face = state["last_qty"] + 1, state["last_face"]
        if qty > 2 * n:
            return self._challenge()
        while not is_valid_bid(qty, face, state["last_qty"], state["last_face"], 0, special):
            qty += 1
            if qty > 2 * n:
                return self._challenge()
        return self._call(qty, face)


# ==================== 2. 峰顶：记忆 + 固定 +0.10 门控 ====================

class PeakAI(BaseAI):
    key = "peak"
    name = AI_INFO["peak"]["name"]
    BASE_THRESHOLD = 0.40          # 未挂门控的诚实阈值
    GATE = 0.10                    # DZ_133 峰顶：+0.10

    def __init__(self):
        super().__init__()
        self.bluff_ewma = 0.5      # 对手"叫牌不实"的指数滑动估计
        self.opp_challenges = 0
        self.opp_decisions = 0
        self._game_opp_calls = 0

    def start_game(self):
        super().start_game()
        self._game_opp_calls = 0

    def decide(self, state):
        self._note_state(state)
        own, n = state["own_dice"], state["dice_num"]
        special = state["special_rule"]
        stats = counts(own)
        if state["first_turn"]:
            q, f = safe_first_bid(stats, n, special)
            return self._call(q, f)

        # 记忆修正：对手越爱诈唬 → 门槛略升；对手开盅越频繁 → 门槛略降
        c_hat = (self.opp_challenges / self.opp_decisions) if self.opp_decisions else 0.26
        th = self.BASE_THRESHOLD + self.GATE
        th += 0.08 * (self.bluff_ewma - 0.5)
        th -= 0.05 * (c_hat - 0.26)
        th = min(0.62, max(0.40, th))

        p = bid_true_prob(stats, n, special, state["last_qty"], state["last_face"])
        if p < th:
            return self._challenge()
        r = safe_raise(stats, state["last_qty"], state["last_face"], n, special)
        return self._call(*r) if r else self._challenge()

    def on_challenge_result(self, bid_qty, actual_count):
        opp_calls = sum(1 for _, _, by_self in self._last_hist if not by_self)
        if self.i_challenged:
            self.opp_decisions += opp_calls
        else:
            self.opp_decisions += opp_calls + 1
            self.opp_challenges += 1
        truth = 1.0 if actual_count >= bid_qty else 0.0
        if not self.i_made_last_bid:
            self.bluff_ewma = 0.8 * self.bluff_ewma + 0.2 * truth

    def on_dice_revealed(self, payload, seat):
        # 对手这局最后一手叫牌"自己手里能撑多少"，作为诈唬证据
        if self.i_made_last_bid:
            return
        opp = 1 - int(seat)
        qty, face = payload.get("last_qty", 0), payload.get("last_face", 0)
        if not qty or not face:
            return
        hand = payload["dice"][opp]
        support = sum(1 for d in hand
                      if d == face or (payload.get("special_rule") and d == 1))
        ratio = min(1.0, support / max(1, qty))
        self.bluff_ewma = 0.7 * self.bluff_ewma + 0.3 * ratio


# ==================== 3. 记忆版：三档（off / 上一局 / 整场） ====================

class MemoryAI(BaseAI):
    key = "memory"
    name = AI_INFO["memory"]["name"]
    KAPPA = 1.5
    CT_REF = 0.26
    BASE_THRESHOLD = 0.40

    def __init__(self, tier="whole"):
        super().__init__()
        assert tier in ("off", "short", "whole")
        self.tier = tier
        self.history = []        # [(opp_challenges, opp_decisions), ...]
        self._g_calls = 0

    def start_game(self):
        super().start_game()
        self._g_calls = 0

    def _window(self):
        if self.tier == "off":
            return []
        if self.tier == "short":
            return self.history[-1:]
        return self.history[:]

    def threshold(self):
        w = self._window()
        if not w:
            return self.BASE_THRESHOLD
        c = sum(a for a, _ in w) / max(1, sum(b for _, b in w))
        th = self.BASE_THRESHOLD * (1.0 - self.KAPPA * (c - self.CT_REF))
        return min(0.70, max(0.15, th))

    def decide(self, state):
        self._note_state(state)
        own, n = state["own_dice"], state["dice_num"]
        special = state["special_rule"]
        stats = counts(own)
        if state["first_turn"]:
            q, f = safe_first_bid(stats, n, special)
            return self._call(q, f)
        p = bid_true_prob(stats, n, special, state["last_qty"], state["last_face"])
        if p < self.threshold():
            return self._challenge()
        r = safe_raise(stats, state["last_qty"], state["last_face"], n, special)
        return self._call(*r) if r else self._challenge()

    def on_challenge_result(self, bid_qty, actual_count):
        opp_calls = sum(1 for _, _, by_self in self._last_hist if not by_self)
        if self.i_challenged:
            self.history.append((0, opp_calls))
        else:
            self.history.append((1, opp_calls + 1))


# ==================== 4. 对手建模：Beta-Binomial 手牌后验 ====================

def _beta_binom_sf(n, need, a, b):
    """P(X >= need), X ~ BetaBinomial(n, a, b)。n 很小（≤5），直接求和。"""
    if need <= 0:
        return 1.0
    if need > n:
        return 0.0
    lb = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    total = 0.0
    for k in range(need, n + 1):
        logp = (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
                + math.lgamma(a + k) + math.lgamma(b + n - k)
                - math.lgamma(a + b + n) - lb)
        total += math.exp(logp)
    return min(1.0, max(0.0, total))


class BayesAI(BaseAI):
    key = "bayes"
    name = AI_INFO["bayes"]["name"]
    THRESHOLD = 0.45

    def __init__(self):
        super().__init__()
        # 每个点数一颗骰子的后验：Beta(1,5) 先验（均值 1/6）
        self.alpha = [0.0] + [1.0] * 6
        self.beta = [0.0] + [5.0] * 6
        self.games_seen = 0

    def _p_match_params(self, face):
        if face == 1:
            return self.alpha[1], self.beta[1]
        # 百搭：P(出 face 或 1) ≈ Beta(a_f+a_1, b_f+b_1)（矩近似）
        return self.alpha[face] + self.alpha[1], self.beta[face] + self.beta[1]

    def _true_prob(self, stats, n, special, qty, face):
        need = qty - match_count(stats, face, special)
        if need <= 0:
            return 1.0
        if need > n:
            return 0.0
        a, b = self._p_match_params(face)
        return _beta_binom_sf(n, need, a, b)

    def decide(self, state):
        self._note_state(state)
        own, n = state["own_dice"], state["dice_num"]
        special = state["special_rule"]
        stats = counts(own)
        if state["first_turn"]:
            q, f = safe_first_bid(stats, n, special)
            return self._call(q, f)

        p = self._true_prob(stats, n, special, state["last_qty"], state["last_face"])
        if p < self.THRESHOLD:
            return self._challenge()

        # 在最小抬叫里选"成真概率"最高的；偶尔多抬一档诈唬
        cands = minimal_raises(state["last_qty"], state["last_face"], n, special,
                               extra=2)
        if not cands:
            return self._challenge()
        scored = [(self._true_prob(stats, n, special, q, f), q, f) for q, f in cands]
        scored.sort(key=lambda x: (-x[0], x[1]))
        if random.random() < 0.12 and len(scored) > 2:
            _, q, f = scored[2]
        else:
            _, q, f = scored[0]
        return self._call(q, f)

    def on_dice_revealed(self, payload, seat):
        opp = 1 - int(seat)
        hand = payload["dice"][opp]
        n = len(hand)
        cnt = [0] * 7
        for d in hand:
            cnt[d] += 1
        for f in range(1, 7):
            self.alpha[f] += cnt[f]
            self.beta[f] += n - cnt[f]
        self.games_seen += 1


# ==================== 工厂 ====================

class ResearchDiceAI(BaseAI):
    """研究线真实权重（LSTM-BC / LSTM-DQN），接口与 BaseAI 对齐。"""

    def __init__(self, key):
        super().__init__()
        self.key = key
        self.spec = RESEARCH_SPECS[key]
        self.inner = None
        self._load_lock = threading.Lock()

    def _ensure(self):
        if self.inner is not None:
            return
        with self._load_lock:
            if self.inner is not None:
                return
            root = str(RESEARCH_ROOT)
            if root not in sys.path:
                sys.path.insert(0, root)
            import torch  # noqa: F401
            try:
                torch.set_num_threads(int(os.environ.get("RA_DICE_THREADS", "3")))
            except Exception:
                pass
            path = str(RESEARCH_MODEL_DIR / self.spec["file"])
            if self.spec["kind"] == "bc":
                from liarsdice.ai.lstm_bc import LSTMBC_AI
                self.inner = LSTMBC_AI(model_path=path)
            else:
                from liarsdice.ai.dqn_lstm.policy import LSTMDQNAI
                self.inner = LSTMDQNAI(model_path=path)

    def start_game(self):
        # 模型只读；跨局不需要重置
        pass

    def decide(self, state):
        self._ensure()
        with _RESEARCH_LOCK:
            return self.inner.decide(state)


def research_available():
    """当前 Python 是否有 torch + 研究线 + 四个模型文件。"""
    try:
        root = str(RESEARCH_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        import torch  # noqa: F401
        for spec in RESEARCH_SPECS.values():
            if not (RESEARCH_MODEL_DIR / spec["file"]).exists():
                return False, "缺模型文件 " + spec["file"]
        return True, ""
    except Exception as e:
        return False, repr(e)


def make_ai(key, tier="whole", research=False):
    if research:
        if key not in RESEARCH_SPECS:
            raise KeyError(key)
        if key not in _RESEARCH_CACHE:
            _RESEARCH_CACHE[key] = ResearchDiceAI(key)
        return _RESEARCH_CACHE[key]
    if key == "baseline":
        return BaselineAI()
    if key == "peak":
        return PeakAI()
    if key == "memory":
        return MemoryAI(tier)
    if key == "bayes":
        return BayesAI()
    raise KeyError(key)


def label_of(key):
    return AI_INFO[key]
