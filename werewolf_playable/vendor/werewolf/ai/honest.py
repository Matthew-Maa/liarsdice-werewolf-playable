"""
honest.py - baseline 2：完全诚实（不说谎、或沉默）
===================================================
口径（任务卡第二节 + Ⓚ 默认值）：
  - 发言：只发与自己私有信息一致的声明，否则沉默。
      好人 → SELF_CLAIM(真实身份)；预言家查到狼 → ACCUSE(该狼, WOLF)；
      狼人 → 任何真实声明都不利 → 恒 SILENCE（HONEST_WOLF_SPEECH）。
  - 夜晚：按角色正常行动（刀人/用药/验人都是真实动作，无"谎言"维度）。
      狼人：在合法刀目标里随机（狼不知道谁是神谁是民，只有"非狼"）；
      女巫：能救就救（解药可用且死者不是自己），无信息时不毒人；
      预言家：优先验没验过的人。
  - 投票：不是声明、无真假。好人投"被公开指控为狼次数最多者"，
      无指控则随机，不弃票；狼人随机投非同伴。
  - 猎人开枪：按 HUNTER_SHOT_MODE（默认先打被指控最多者，无指控随机）。
"""
from .. import config as C
from ..game import actions as A
from ..game.roles import WOLF, FACTION_WOLF, SEER, WITCH
from .base import BaseAgent, accuse_wolf_counts


class HonestAgent(BaseAgent):
    name = "honest"

    # ==================== 入口 ====================

    def act(self, view, legal):
        phase = view["phase"]
        if phase == "day_speech":
            return self._speak(view, legal)
        if phase == "day_vote":
            return self._vote(view, legal)
        if phase == "night_wolf":
            return self._wolf_kill(view, legal)
        if phase == "night_witch":
            return self._witch(view, legal)
        if phase == "night_seer":
            return self._seer(view, legal)
        if phase == "hunter_shot":
            return self._shoot(view, legal)
        # ---- task_LRS_030：警长两阶段（预注册口径见任务卡 §0）----
        if phase == "day_sheriff_vote":
            return self._sheriff_vote(view, legal)
        if phase == "sheriff_death_badge":
            return self._badge(view, legal)
        return self.pick(legal)

    # ==================== 发言 ====================

    def _speak(self, view, legal):
        role = view["my_role"]
        if role == WOLF:
            a = A.silence()
            return a if a in legal else self.pick(legal)
        if role == SEER:
            target = self._found_wolf(view)
            if target is not None:
                a = A.accuse(target, WOLF)
                if a in legal:
                    return a
        a = A.self_claim(role)
        return a if a in legal else self.pick(legal)

    def _found_wolf(self, view):
        """预言家：返回一个"验出来是狼且还活着"的 pid（多个随机挑一个）。"""
        res = view.get("seer_results") or {}
        alive = set(view["alive"])
        hits = sorted(t for t, is_w in res.items() if is_w and t in alive)
        return self.rng.choice(hits) if hits else None

    # ==================== 投票 ====================

    def _vote(self, view, legal):
        alive = view["alive"]
        pid = view["pid"]
        if view["my_faction"] == FACTION_WOLF:
            mates = set(view.get("teammates") or [])
            targets = [t for t in alive if t != pid and t not in mates]
            if not targets:
                return self.pick(legal)
            a = self.pick_among(legal, targets)
            return a if a is not None else self.pick(legal)

        # 好人：被公开指控为狼次数最多者；无指控随机；不弃票
        cand = [t for t in alive if t != pid]
        counts = accuse_wolf_counts(view["public"])
        scored = {t: counts.get(t, 0) for t in cand}
        if any(v > 0 for v in scored.values()):
            a = self.pick_top(legal, scored)
            if a is not None:
                return a
        a = self.pick_among(legal, cand)
        return a if a is not None else self.pick(legal)

    # ==================== 夜晚 ====================

    def _wolf_kill(self, view, legal):
        # 狼不知道谁是神/民，只知道"非狼"（合法集已排除狼，除非开了自刀）
        return self.pick(legal)

    def _witch(self, view, legal):
        w = view.get("witch") or {}
        if w.get("has_antidote") and w.get("night_victim") is not None:
            a = A.witch_save()
            if a in legal:
                return a
        if C.HONEST_WITCH_USE_POISON:
            cand = [x for x in legal if x["type"] == A.WITCH and x["potion"] == A.POTION_POISON]
            if cand:
                return self.pick(cand)
        a = A.witch_none()
        return a if a in legal else self.pick(legal)

    def _seer(self, view, legal):
        seen = set((view.get("seer_results") or {}).keys())
        fresh = [t for t in view["alive"] if t != view["pid"] and t not in seen]
        a = self.pick_among(legal, fresh)
        if a is not None:
            return a
        return self.pick(legal)

    # ==================== 警长（task_LRS_030）====================

    def _sheriff_vote(self, view, legal):
        """警长选举投票：预注册口径 = 随机投一个存活他人（用 agent 自己的 rng）。

        ★ 为什么用 self.rng 而不是 env 的 sheriff_rng：
          agent 的 rng 由 env._setup 按 (seed*1009 + pid*7919) 播种，
          与主线 rng/tie_rng 完全独立 ⇒ 不会扰动既有随机序列。
        """
        pid = view["pid"]
        cand = [t for t in view["alive"] if t != pid]
        a = self.pick_among(legal, cand)
        if a is not None:
            return a
        return self.pick(legal)

    def _badge(self, view, legal):
        """警徽继承：预注册口径 = 指定预言家；没有存活预言家⇒找自称预言家的人；再没有⇒流失。

        ★ 注意：honest 不知道谁是预言家（除非自己是）。
          能用的公开信息只有"谁自称过预言家"（public.speeches 里的 SELF_CLAIM(SEER)）。
          这是**刻意只用公开信息**，不偷看角色表。
        """
        me = view["pid"]
        alive = set(view["alive"])

        # ① 若我自己就是预言家且还活着（不可能：我是死者），跳过
        # ② 公开自称过 SEER 的存活者
        claimed = []
        for sp in (view["public"].get("speeches") or []):
            act = sp.get("action") or {}
            if act.get("type") == A.SELF_CLAIM and act.get("role") == SEER:
                t = sp.get("pid")
                if t in alive and t != me and t not in claimed:
                    claimed.append(t)
        a = self.pick_among(legal, claimed)
        if a is not None:
            return a
        # ③ 没有自称者 ⇒ 随机给一个存活他人（预注册允许的兜底）
        cand = [t for t in view["alive"] if t != me]
        a = self.pick_among(legal, cand)
        if a is not None:
            return a
        # ④ 真的没人可给 ⇒ 警徽流失
        a = A.badge_drop()
        return a if a in legal else self.pick(legal)

    # ==================== 猎人开枪 ====================

    def _shoot(self, view, legal):
        if C.HUNTER_SHOT_MODE == "random":
            return self.pick(legal)
        pid = view["pid"]
        cand = [t for t in view["alive"] if t != pid]
        counts = accuse_wolf_counts(view["public"])
        scored = {t: counts.get(t, 0) for t in cand}
        if any(v > 0 for v in scored.values()):
            a = self.pick_top(legal, scored)
            if a is not None:
                return a
        a = self.pick_among(legal, cand)
        return a if a is not None else self.pick(legal)
