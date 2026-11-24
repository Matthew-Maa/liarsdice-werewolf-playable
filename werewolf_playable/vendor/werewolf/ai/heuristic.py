"""
heuristic.py - baseline 3：发言加权启发式
==========================================
卡定口径：在"完全诚实"基础上**只改投票**（发言、夜晚行动与诚实完全相同）。

嫌疑分（Ⓚ 见 config.W_ACCUSE / W_SILENCE）：
    score(p) = W_ACCUSE * (p 被 ACCUSE(·, 狼人) 的累计次数)
             + W_SILENCE * (p 沉默的累计次数)
投给 score 最高者，平分随机，**始终不弃票**。

⚠ 任务卡第 2、3 条把"诚实"和"启发式"的投票都写成了"投被指控为狼次数最多者"，
   字面上两者等价。为了让第三条真有"加权"内容、并直接检验卡里关心的
   "狼人全沉默是否反而暴露"，这里加了沉默项（W_SILENCE=0.5）。
   把 W_SILENCE 改成 0.0 即严格退回卡面口径（此时与 baseline2 完全一致）。

狼人投票也用同一套分数，但**排除自己的同伴**（狼人知道同伴身份，这是它的私有信息）。
"""
from .. import config as C
from ..game.roles import FACTION_WOLF
from .base import suspicion_scores
from .honest import HonestAgent


class HeuristicAgent(HonestAgent):
    name = "heuristic"

    def _vote(self, view, legal):
        alive = view["alive"]
        pid = view["pid"]
        mates = set(view.get("teammates") or []) if view["my_faction"] == FACTION_WOLF else set()
        cand = [t for t in alive if t != pid and t not in mates]
        if not cand:
            return self.pick(legal)
        scored = suspicion_scores(view["public"], cand,
                                  w_accuse=C.W_ACCUSE, w_silence=C.W_SILENCE)
        a = self.pick_top(legal, scored)
        if a is not None:
            return a
        a = self.pick_among(legal, cand)
        return a if a is not None else self.pick(legal)
