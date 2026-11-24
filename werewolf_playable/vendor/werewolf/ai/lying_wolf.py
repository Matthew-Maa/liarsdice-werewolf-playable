"""
lying_wolf.py - baseline 4：会说谎的狼（冒充预言家的狼）
=========================================================
v0 的三个 baseline 里狼人要么沉默（诚实/启发式里的 HONEST_WOLF_SPEECH=SILENCE）
要么乱说（随机），所以"发言加权"看着极强其实是吃 baseline 的退化行为——
**没有一只狼会说谎**，这是 v0 的天花板。

本 baseline 只改**狼人**的行为，给一只"会冒充预言家的狼"，专门检验：
  - 狼人不沉默、而是用现有动作集（SELF_CLAIM / ACCUSE）伪造身份与验人，
    能不能捡回"沉默信号"被利用的劣势？
  - 好人阵营在"存在伪装者"时，'被指控为狼次数'和'沉默'这两个公开信号还靠得住吗？

**关键约束（与 v0 信息相同）**：狼只知道"谁是同伴（狼）"，不知道谁是神谁是民，
所以它**无法精确定点咬预言家**——只能诬陷"不是同伴的好人"。除非有人当众
SELF_CLAIM(SEER) 自曝——那样狼反而能锁定真预言家、咬它（最有价值的靶子）。

动作集不变（SILENCE / SELF_CLAIM / ACCUSE / VOTE），**没加任何新动作**。
说谎 = 在动作语义上撒谎：冒充身份 + 假验人指控。

策略参数（可调，便于预实验扫描）：
  IMPERSONATE     冒充的角色（默认 SEER=预言家）
  FRAME_STRATEGY  诬陷目标怎么选：
      'random_good'   诬陷一个随机非狼好人（基线）
      'follow_claim'  若有人自称预言家/神职，优先诬陷他（认得出真预言家）
  TARGET_PRIORITY  狼人投票/发言时优先咬谁（按上面的诬陷目标）
"""
from .. import config as C
from ..game import actions as A
from ..game.roles import WOLF, SEER, FACTION_WOLF
from .honest import HonestAgent


class LyingWolfAgent(HonestAgent):
    name = "lying_wolf"

    def __init__(self, impersonate=SEER, frame_strategy="follow_claim", lie_prob=1.0):
        super().__init__()
        self.impersonate = impersonate           # 狼冒充的角色（默认预言家）
        self.frame_strategy = frame_strategy
        self.lie_prob = float(lie_prob)          # 伪装预算：狼此局"会不会说谎"的概率（0=恒沉默对标，1=恒说谎）
        self._frame_target = None                # 本局决定诬陷谁（跨决策点记住）
        self._lie_roll = None                    # 本局"伪不伪装"的掷签结果

    def reset(self, seed):
        """每局重开：重掷"这局说不说谎"，清掉上一局的诬陷目标 ⇒ 同 seed 跨格决策一致。"""
        super().reset(seed)
        self._frame_target = None
        self._lie_roll = None

    # ---- 发言 ----

    def _speak(self, view, legal):
        role = view["my_role"]
        if role != WOLF:
            # 非狼角色退化为"完全诚实"（与 baseline2 一致）
            return super()._speak(view, legal)

        # 伪装预算：以 lie_prob 决定这只狼"这局说不说谎"（决策用 per-seat RNG ⇒ 同 seed 可配对）
        #  &self 记录一次，避免每轮发言反复掷；掷决定"恒沉默"走 HONEST_WOLF_SPEECH
        if getattr(self, "_lie_roll", None) is None:
            self._lie_roll = self.rng.random() < self.lie_prob
        if not self._lie_roll:
            # 保守：退回"诚实狼人"的恒沉默行为
            a = A.silence()
            return a if a in legal else self.pick(legal)

        # 狼人：冒充 + 诬陷
        target = self._choose_frame_target(view)
        # 若已有目标在案且还活着，继续咬；否则换一个
        if target is not None:
            a = A.accuse(target, WOLF)
            if a in legal:
                return a
        # 还没咬上：先冒充身份（首日/没人可咬时）
        claim = A.self_claim(self.impersonate)
        if claim in legal:
            return claim
        return self.pick(legal)

    def _choose_frame_target(self, view):
        """
        决定诬陷谁。优先级：
          1. 若本局已定过目标且还活着，沿用（咬到死）。
          2. follow_claim：看公开发言里有没有 SELF_CLAIM(SEER/WITCH/...)
             的人 —— 那很可能是真神职（尤其真预言家），优先诬陷。
          3. 随机挑一个非狼、非同伴的好人。
        """
        alive = set(view["alive"])
        pid = view["pid"]
        mates = set(view.get("teammates") or [])

        good_candidates = [t for t in alive
                           if t != pid and t not in mates]

        if self._frame_target is not None and self._frame_target in alive:
            return self._frame_target

        # follow_claim：认得出当众自曝的神职（尤其预言家）
        if self.frame_strategy == "follow_claim":
            claimers = set()
            for s in view["public"]["speeches"]:
                if s["action"]["type"] == A.SELF_CLAIM:
                    claimers.add(s["pid"])
            # 优先咬自称预言家的；其次任何自称神职的
            for pre in (SEER, "WITCH", "HUNTER", "VILLAGER"):
                hits = [t for t in good_candidates
                        if t in claimers and \
                           any(s["pid"] == t and s["action"].get("role") == pre
                               for s in view["public"]["speeches"])]
                if hits:
                    self._frame_target = self.rng.choice(sorted(hits))
                    return self._frame_target

        if good_candidates:
            self._frame_target = self.rng.choice(good_candidates)
            return self._frame_target
        return None

    # ---- 投票 ----

    def _vote(self, view, legal):
        if view["my_faction"] != FACTION_WOLF:
            # 非狼角色退化为诚实
            return super()._vote(view, legal)

        alive = view["alive"]
        pid = view["pid"]
        mates = set(view.get("teammates") or [])

        # 狼优先投自己诬陷的目标（一致性：发言咬谁、投票就投谁）；
        # 但若本局"伪装预算"掷为不说谎（_lie_roll=False），就不带节奏，退回诚实狼的随机乱投
        if getattr(self, "_lie_roll", True) and self._frame_target is not None and self._frame_target in alive:
            a = self.pick_among(legal, [self._frame_target])
            if a is not None:
                return a

        # 退路：投"被指控为狼次数最多"的好人（跟好人舆论）
        cand = [t for t in alive if t != pid and t not in mates]
        scored = _accuse_counts(view["public"])
        if any(v > 0 for v in scored.values()):
            a = self.pick_top(legal, {t: scored.get(t, 0) for t in cand})
            if a is not None:
                return a
        a = self.pick_among(legal, cand)
        return a if a is not None else self.pick(legal)


def _accuse_counts(public):
    """每个玩家被 ACCUSE(·, WOLF) 的累计次数（本地小工具，避免和 base 耦合过深）。"""
    from .base import accuse_wolf_counts
    return accuse_wolf_counts(public)
