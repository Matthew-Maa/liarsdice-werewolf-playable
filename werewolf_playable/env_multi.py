# -*- coding: utf-8 -*-
"""env_multi.py —— 对外游戏「多发言」实验档环境（LRS_084）

★ 独立新文件，**不改 `vendor/` 任何一个字节**。
   逻辑与 `狼人杀/scripts/lrs083_env.py` 一致（那边已在研究侧跑过 1000 局、非法 0），
   这里只把依赖从 `werewolf.*` 换成 `vendor.werewolf.*`，并加一个「人类序列」分支。

## 语法（与 LRS_083 完全一致）

    每天每座：0/1 条 SELF_CLAIM  +  0~2 条 ACCUSE
    约束：不重复目标、不指自己、不指死者、总条数 ≤ 3、空序列 == 沉默（记 1 条 SILENCE）

## 与 LRS_083 的两点差别

1. **依赖路径**：`vendor.werewolf.*`（游戏包自带的那份）。
2. **人类可以一次给整段序列**：`SuspendAgent.submit_sequence([a1, a2, a3])`。
   环境会**逐条真的调 `_ask`**（每条都进 `_view` / `_ask` / 记录），
   只是把"问人"这一步换成"从队列里取下一句"。
   **这不是 UI 气泡：每条都会改变 `public.speeches`，都会影响后续所有座位的 view 与特征。**

## 为什么要这么写

- 如果只在 UI 上多画几个气泡，环境仍然只执行一条 ⇒ **别人看不到、记录里也没有**，
  那就不是机制，只是装饰。卡面明令不许。
- 所以：**环境真的循环调用 `_ask`，人类只是提前把队列填好。**
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from vendor.werewolf.game import actions as A          # noqa: E402
from vendor.werewolf.game import rules as R            # noqa: E402
from vendor.werewolf.game.env import WerewolfEnv       # noqa: E402
from vendor.werewolf.game.roles import ALL_ROLES, WOLF, SEER  # noqa: E402

MAX_SPEECH = 3
EMPTY_SAID = {"claims": frozenset(), "targets": frozenset(), "n": 0}


# ---------------------------------------------------------------- 语法规格

def legal_speeches_multi(pid, alive, said):
    """`legal_speeches` 的去重版：剔除已自指、已指认过的目标；到上限只给 SILENCE。"""
    n = (said or EMPTY_SAID)["n"]
    if n >= MAX_SPEECH:
        return [A.silence()]
    claims = set((said or EMPTY_SAID)["claims"])
    targets = set((said or EMPTY_SAID)["targets"])
    out = [A.silence()]
    if not claims:
        for r in ALL_ROLES:
            out.append(A.self_claim(r))
    for t in alive:
        if t == pid or t in targets:
            continue
        for r in ALL_ROLES:
            out.append(A.accuse(t, r))
    return out


def validate_sequence(pid, alive, seq):
    """校验人类提交的整段序列是否**符合语法**。返回 (ok, 错误说明, 规范化后的序列)。

    规则（与 legal_speeches_multi 同口径）：
      · 总条数 1~MAX_SPEECH
      · 最多 1 条 SELF_CLAIM，且必须排在最前
      · 指认目标不能是自己、必须在 alive 里、不能重复
      · 空列表 == 沉默（返回 [SILENCE]）
    """
    if not seq:
        return True, "", [A.silence()]
    if len(seq) > MAX_SPEECH:
        return False, "最多 %d 条" % MAX_SPEECH, None
    out = []
    claims = 0
    targets = set()
    seen_accuse = False
    for i, a in enumerate(seq):
        t = a.get("type")
        if t == A.SILENCE:
            if len(seq) > 1:
                return False, "沉默只能单独出现", None
            return True, "", [A.silence()]
        if t == A.SELF_CLAIM:
            if seen_accuse:
                return False, "自报身份必须在指认之前", None
            claims += 1
            if claims > 1:
                return False, "最多 1 条自报", None
            out.append(a)
        elif t == A.ACCUSE:
            seen_accuse = True
            tgt = a.get("target")
            if tgt == pid:
                return False, "不能指认自己", None
            if tgt not in alive:
                return False, "目标不在场", None
            if tgt in targets:
                return False, "同一个目标不能指认两次", None
            targets.add(tgt)
            out.append(a)
        else:
            return False, "发言只允许 沉默 / 自报 / 指认", None
    return True, "", out


def coherent_extra_speech(view, legal, said, agent=None):
    """AI 的第 2/3 条发言：只做「指认某人是狼」或沉默，且不重复目标。

    2026-11-16 玩家反馈修：
      单发言 agent 被连续问 3 次时，第 2/3 次它的自报身份已经不在 legal 里，
      `HonestAgent._speak` 会退到 `self.pick(legal)`，于是随机指认
      "某人是女巫 / 某人是预言家"——出现"我自称女巫，又指认 6 号是女巫"
      这种同一唯一神职被说两次的废话。
      这里把追加发言收窄成「指认狼」：好人跟公开指控走，狼人继续咬自己的目标。
    这只改游戏侧的多发言包装，不改 vendor / 单发言模型。
    """
    if view.get("phase") != "day_speech" or not legal:
        return A.silence() if A.silence() in legal else legal[0]
    pid = view.get("pid")
    alive = set(view.get("alive") or [])
    role = view.get("my_role")
    mates = set(view.get("teammates") or []) if view.get("my_faction") == "wolf" else set()
    used = set((said or {}).get("targets") or set())

    def accuse_legal(t):
        a = A.accuse(t, WOLF)
        return a if a in legal else None

    # 狼：优先继续咬自己已经选定的诬陷目标
    if role == WOLF:
        frame = None
        for obj in (agent, getattr(agent, "real", None), getattr(agent, "inner", None)):
            if obj is None:
                continue
            w = getattr(obj, "wolf", None)
            frame = getattr(w, "_frame_target", None)
            if frame is not None:
                break
        cands = []
        if frame in alive and frame != pid and frame not in mates and frame not in used:
            cands.append(frame)
        cands += [t for t in sorted(alive)
                  if t != pid and t not in mates and t not in used]
        for t in cands:
            a = accuse_legal(t)
            if a is not None:
                return a
        return A.silence()

    # 好人：只有预言家有新信息（查到的狼）；其余人不重复跟风指控。
    #   ★ 2026-11-16 实测：如果让好人跟公开指控走，多发言会把狼人的
    #     2 条额外诬陷放大成好人的 2 条跟风，300 局好人胜率从 51.7% 掉到 15%。
    #     好人没有新信息时沉默，比"为了多说话而多说话"更诚实。
    if role == SEER:
        hits = [t for t, is_w in (view.get("seer_results") or {}).items()
                if is_w and t in alive and t != pid and t not in used]
        for t in sorted(hits):
            a = accuse_legal(t)
            if a is not None:
                return a
    return A.silence() if A.silence() in legal else legal[0]


# ---------------------------------------------------------------- agent 侧

class SpeakingAgent(object):
    """给 AI 用的多发言包装（与 LRS_083 同构）。"""

    def __init__(self, inner):
        self.inner = inner
        self.name = getattr(inner, "name", inner.__class__.__name__)
        self._day = None
        self.reset_said()

    def reset(self, seed):
        if hasattr(self.inner, "reset"):
            self.inner.reset(seed)

    def reset_said(self):
        self.said = {"claims": set(), "targets": set(), "n": 0}
        self.end = False

    def _sync_day(self, view):
        d = view.get("day")
        if d != self._day:
            self._day = d
            self.reset_said()

    def act(self, view, legal):
        self._sync_day(view)
        if view.get("phase") != "day_speech":
            return self.inner.act(view, legal)
        if self.end:
            return A.silence()
        lg = legal if self.said["n"] == 0 else \
            legal_speeches_multi(view["pid"], view["alive"], self.said)
        if self.said["n"] == 0:
            a = self.inner.act(view, lg)
        else:
            # 第 2/3 条不再问单发言 agent（它会随机指认神职）
            a = coherent_extra_speech(view, lg, self.said, self.inner)
        if a not in lg:
            a = lg[0]
        typ = a.get("type")
        if typ == A.SELF_CLAIM:
            self.said["claims"].add(a.get("role"))
        elif typ == A.ACCUSE:
            self.said["targets"].add(a.get("target"))
        elif typ == A.SILENCE:
            self.end = True
        self.said["n"] += 1
        return a


class HumanSeqAgent(object):
    """给人类用的「攒一段、再逐条执行」包装（LRS_084）。

    人类在 GUI 上点完"结束发言"后，`submit_sequence(seq)` 把整段塞进队列并**放行**；
    之后环境每调一次 `act` 就弹出一条（每条都真的走 `_view` → `ask` → 记录）。
    队列空了 ⇒ 返回 SILENCE（= 本座今天结束）。

    ★ 非发言阶段（投票/夜间）也走这里：阻塞等人从 GUI 点，然后返回那个动作。
      ⇒ 所以人类在 multi 档下**整个会话**都由它驱动，不需要另开 human 通道。
    """

    def __init__(self, name="玩家"):
        self.name = name
        self.queue = []
        self.gate_ev = None          # threading.Event，由 GUI 注入
        self.gate_action = None
        self._day = None
        self.said = {"claims": set(), "targets": set(), "n": 0}
        self.end = False

    def reset(self, seed):
        self.queue = []
        self._day = None
        self.said = {"claims": set(), "targets": set(), "n": 0}
        self.end = False

    def submit_sequence(self, seq):
        self.queue = list(seq or [])

    def _sync_day(self, view):
        d = view.get("day")
        if d != self._day:
            self._day = d
            self.said = {"claims": set(), "targets": set(), "n": 0}
            self.end = False

    def act(self, view, legal):
        self._sync_day(view)
        # ★ 先把自己今天说过几条记下来（这是"要不要等下一次闸门"的依据）
        n_before = self.said["n"]
        if view.get("phase") == "day_speech":
            # ★ 队列空 = 还没攒/已说完 ⇒ 等 GUI 放行（gate_ev），不是立刻沉默。
            #   点"结束发言"时 GUI 会 submit 序列（可能为空）再 set 闸门；
            #   若确实为空 ⇒ 沉默。
            if not self.queue and not self.end and self.gate_ev is not None:
                self.gate_ev.clear()
                self.gate_ev.wait()
            if self.end or not self.queue:
                self.end = True
                return A.silence()
            a = self.queue.pop(0)
            if a not in legal:
                self.end = True
                return A.silence()
            typ = a.get("type")
            if typ == A.SELF_CLAIM:
                self.said["claims"].add(a.get("role"))
            elif typ == A.ACCUSE:
                self.said["targets"].add(a.get("target"))
            elif typ == A.SILENCE:
                self.end = True
            self.said["n"] += 1
            return a
        # 非发言阶段：等 GUI 给动作
        if self.gate_ev is None:
            return legal[0] if legal else A.silence()
        self.gate_ev.clear()
        self.gate_ev.wait()
        a = self.gate_action
        self.gate_action = None
        if a is None or a not in legal:
            return legal[0] if legal else A.silence()
        return a

    def gate_respond(self, action):
        """GUI 调用：把非发言阶段的动作交给这里并放行。"""
        self.gate_action = action
        self.gate_ev.set()


# ---------------------------------------------------------------- env

class MultiSpeechEnv(WerewolfEnv):
    """只重写 `_daytime` 的**发言段**，其余全部继承 `vendor` 的原实现。"""

    def __init__(self, agents, seed=0, record=True, roles=None,
                 speech_mode="single", on_speech=None, **kw):
        assert speech_mode in ("single", "multi"), speech_mode
        # 先设好本类自己的状态，再交给基类（基类 __init__ 会设 forced_roles 等）
        self.speech_mode = speech_mode
        self.speech_log = []          # [(day, pid, order, code)]
        self.on_speech = on_speech    # 回调：每说一句就通知 UI
        self.human_gate = {}          # pid -> threading.Event（人类"继续发言"闸门）
        super(MultiSpeechEnv, self).__init__(agents, seed=seed, record=record,
                                             roles=roles, **kw)

    def register_gate(self, pid, ev):
        self.human_gate[pid] = ev

    def _ask(self, pid, phase, legal, note=None):
        # ★ 不再在 _ask 里拦人：人类的所有等待都在 `HumanSeqAgent.act` 里（单一等待点）。
        #   发言阶段：队列有货就吐一条；队列空就 SILENCE（本次发言结束）。
        #   非发言阶段：阻塞在 gate_ev，由 GUI 的 send() 放行。
        #   注：`human_gate` 保留只为兼容旧的注册调用，这里不再使用。
        return super(MultiSpeechEnv, self)._ask(pid, phase, legal, note=note)

    def _daytime(self):
        if self.speech_mode == "single":
            return super(MultiSpeechEnv, self)._daytime()
        return self._daytime_multi()

    def _reset_day_speech(self):
        """每天发言前清掉上一轮的 said/end。

        2026-11-16 玩家反馈修：
          原来只在 agent 自己下一次 act 时才 `_sync_day`；但 `_daytime_multi`
          在 act 之前先看 `ag.end`。第 1 天结束发言后 end=True，
          第 2 天所有座位被直接跳过 ⇒ 从第 2 天起全场不发言，只剩投票。
          这里在每天循环开始前显式重置；WebWolfAgent / SpeakingAgent /
          HumanSeqAgent / GuiWolfAgent 的 real 都覆盖到。
        """
        view = {"phase": "day_speech", "day": self.day}
        for ag in self.agents:
            targets = [ag]
            for attr in ("real", "inner"):
                child = getattr(ag, attr, None)
                if child is not None and child is not ag:
                    targets.append(child)
            for obj in targets:
                sync = getattr(obj, "_sync_speech", None)
                if callable(sync):
                    try:
                        sync(view)
                        continue
                    except Exception:
                        pass
                sync = getattr(obj, "_sync_day", None)
                if callable(sync):
                    try:
                        sync(view)
                        continue
                    except Exception:
                        pass
                if hasattr(obj, "said"):
                    obj.said = {"claims": set(), "targets": set(), "n": 0}
                if hasattr(obj, "end"):
                    obj.end = False

    def _daytime_multi(self):
        cfg = self.cfg
        alive = self.alive_pids()
        order = self._speech_order(alive)
        self._reset_day_speech()

        for pid in order:
            ag = self.agents[pid]
            for k in range(MAX_SPEECH):
                if getattr(ag, "end", False):
                    break
                legal = A.legal_speeches(pid, alive) if k == 0 else \
                    legal_speeches_multi(pid, alive, getattr(ag, "said", None))
                a = self._ask(pid, "day_speech", legal)
                self.public["speeches"].append(
                    {"day": self.day, "pid": pid, "action": a,
                     "speech_order": k})
                self.speech_log.append((self.day, pid, k, A.encode(a)))
                if self.on_speech:
                    try:
                        self.on_speech(self.day, pid, k, a)
                    except Exception:
                        pass
                if a.get("type") == A.SILENCE:
                    break

        # 以下与单动作完全相同
        if cfg.SHERIFF and not self.sheriff_elected and self.day == cfg.SHERIFF_ELECT_DAY:
            self._sheriff_election(alive)
            alive = self.alive_pids()

        collected = []
        for pid in alive:
            legal = A.legal_votes(pid, alive,
                                  allow_self=cfg.VOTE_ALLOW_SELF,
                                  allow_abstain=cfg.VOTE_ALLOW_ABSTAIN)
            a = self._ask(pid, "day_vote", legal)
            collected.append((pid, None if a["type"] == A.ABSTAIN else a["target"]))

        for pid, tgt in collected:
            self.public["votes"].append(
                {"day": self.day, "voter": pid, "target": tgt})

        if cfg.SHERIFF and self.sheriff is not None:
            tally = R.tally_votes(collected, alive, sheriff=self.sheriff,
                                  sheriff_weight=cfg.SHERIFF_VOTE_WEIGHT)
        else:
            tally = R.tally_votes(collected, alive)
        if tally["tie"]:
            self.tie_days += 1
        out_pid = tally["eliminated"]
        if tally["tie"] and cfg.TIE_RULE == "random_elimination" and tally["top"]:
            out_pid = self.tie_rng.choice(sorted(tally["top"]))
        self._ev({"kind": "vote_tally", "day": self.day,
                  "counts": {str(k): v for k, v in tally["counts"].items()},
                  "top": tally["top"], "tie": tally["tie"],
                  "abstain": tally["abstain"],
                  "eliminated": out_pid})

        if out_pid is not None:
            out = out_pid
            self._kill(out, R.CAUSE_VOTE, by=None, phase="day")
            self.last_eliminated = out
            self.public["rounds"].append({
                "day": self.day, "phase": "day",
                "pids": [out], "causes": {str(out): R.CAUSE_VOTE},
            })
            if R.hunter_can_shoot(self.players[out].role, R.CAUSE_VOTE):
                self._hunter_shot(out, R.CAUSE_VOTE)
        else:
            self.public["rounds"].append({
                "day": self.day, "phase": "day", "pids": [], "causes": {},
            })

    def _record(self):
        rec = super(MultiSpeechEnv, self)._record()
        rec["speech_mode"] = self.speech_mode
        if self.speech_mode == "multi":
            seq = {}
            for day, pid, order, code in self.speech_log:
                seq.setdefault(str(day), {}).setdefault(str(pid), []).append(code)
            rec["speech_seq"] = seq
        return rec
