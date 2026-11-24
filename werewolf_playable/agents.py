# -*- coding: utf-8 -*-
"""狼人杀可玩版：人类玩家、AI 包装、桌面播报。

AI 直接复用狼人杀线 v0 的 baseline：
  · 好人：HeuristicAgent（发言加权启发式，baseline 3）
  · 狼人：LyingWolfAgent（冒充预言家 + 诬陷，baseline 4，lie_prob=1）
角色由环境按 seed 分配，所以用"按身份自适应"的包装器：看到自己是狼就走狼 AI，
否则走好人 AI。默认两只狼里只有 1 只伪装（n_liars=1），另一只沉默；
n_liars=2 时两只都伪装。
"""
import copy
import os
import random
import sys
from pathlib import Path

from vendor.werewolf import config as C
from vendor.werewolf.game import actions as A
from vendor.werewolf.game.roles import (
    ALL_ROLES, ROLE_CN, role_cn, faction_cn, WOLF,
)
from vendor.werewolf.ai.heuristic import HeuristicAgent
from vendor.werewolf.ai.lying_wolf import LyingWolfAgent

PHASE_CN = {
    "night_wolf": "狼人行动",
    "night_witch": "女巫行动",
    "night_seer": "预言家查验",
    "day_speech": "白天发言",
    "day_vote": "投票",
    "hunter_shot": "猎人开枪",
}

AI_META = {
    "agent_id": "werewolf.adaptive.v0",
    "agent_version": "good=heuristic v0; wolf=lying_wolf v0 (lie_prob=0.55, 1只) + heuristic v0 沉默狼 (1只)",
    "card": "狼人杀线 baseline 3 / baseline 4（无训练 ckpt，启发式；说谎预算按可玩性调到 0.55）",
    "level": "启发式 · 默认约五五开（不是研究最强配置）",
}

ROLE_ORDER = list(ALL_ROLES)          # 1..5 = 狼/民/巫/预/猎

# LRS_076：研究模型档（模型来自 LRS_075 接口包）
MODEL_META = {
    "symmetric_q_only": {
        "agent_id": "werewolf.research_model.v1",
        "version": "werewolf_playable_iface/1.0.0",
        "cards": "LRS_070 (Q-only)",
        "level": "研究模型 · 双向部署档（LRS_070 唯一双正）",
        "warning": None,
    },
    "auto_hybrid": {
        "agent_id": "werewolf.research_model.v1",
        "version": "werewolf_playable_iface/1.0.0",
        "cards": "LRS_069 + LRS_070 (hybrid, untested)",
        "level": "研究模型 · 混合档（未联合验证，仅供娱乐）",
        "warning": "auto_hybrid 未联合验证，仅供娱乐；研究口径请用 symmetric_q_only。",
    },
    "good_three_piece": {
        "agent_id": "werewolf.research_model.v1",
        "version": "werewolf_playable_iface/1.0.0",
        "cards": "LRS_069 (good three-piece)",
        "level": "研究模型 · 好人侧部署档",
        "warning": "good_three_piece 的狼座不挂；适合演示好人侧强度。",
    },
    "q_seer_fair": {
        "agent_id": "werewolf.research_model.v1",
        "version": "werewolf_playable_iface/1.0.0",
        "cards": "LRS_069 (Q+Seer, no Head-A)",
        "level": "研究模型 · 公平对局档（关 Head-A）",
        "warning": "q_seer_fair 关了 Head-A；适合不希望 AI 使用角色表的对局。",
    },
    "wolf_kit_experimental": {
        "agent_id": "werewolf.research_model.v1",
        "version": "werewolf_playable_iface/1.0.0",
        "cards": "LRS_070 (WK+WS+Q, not deployable)",
        "level": "研究模型 · 狼套件实验档（不部署）",
        "warning": "wolf_kit_experimental 已被 LRS_070 判死（狼 +12.31 / 好人 −6.02），仅实验。",
    },
}

_WW_IFACE = None


def _load_ww_iface():
    """惰性加载 LRS_075 接口包；只有选研究模型时才 import torch。"""
    global _WW_IFACE
    if _WW_IFACE is not None:
        return _WW_IFACE
    env = os.environ.get("WEREWOLF_IFACE")
    if env:
        pkg = Path(env)
    else:
        pkg = Path(__file__).resolve().parents[2] / "shared" / "packages" / "werewolf_playable_iface"
    if not (pkg / "ww_iface.py").exists():
        raise RuntimeError("找不到 LRS_075 接口包：%s" % pkg)
    if str(pkg) not in sys.path:
        sys.path.insert(0, str(pkg))
    import ww_iface  # noqa: E402
    _WW_IFACE = ww_iface
    return ww_iface


class ResearchModelAI(object):
    """把接口包的模型代理包成可玩版的 reset/act 接口。"""

    def __init__(self, inner, profile, pid):
        self.inner = inner
        self.profile = profile
        self.pid = int(pid)
        self.name = "研究模型%d" % self.pid
        self.meta = MODEL_META[profile]

    def reset(self, seed):
        self.inner.reset(seed)

    def act(self, view, legal):
        return self.inner.act(view, legal)


def clear():
    import os
    if os.environ.get("RA_NO_CLEAR"):
        return
    os.system("cls" if os.name == "nt" else "clear")


class AdaptiveAI(object):
    """按自己的真实身份选 AI：狼 → 说谎狼（默认只放 1 只）或沉默狼；好人 → 启发式。

    n_liars=1：两只狼里座号最小的那只冒充预言家，另一只按诚实狼（沉默）打。
      这是为了把难度拉回可玩区（研究线实测：1 只说谎狼就能把好人胜率打掉很多，
      2 只都说谎时好人几乎必败）。
    n_liars=2：两只狼都伪装（研究线最难的配置）。
    """

    name = "AI（自适应）"

    def __init__(self, n_liars=1, lie_prob=0.55):
        self.n_liars = int(n_liars)
        self.good = HeuristicAgent()
        self.wolf = LyingWolfAgent(lie_prob=float(lie_prob))
        self.silent = HeuristicAgent()

    def reset(self, seed):
        self.good.reset(seed)
        self.wolf.reset(seed)
        self.silent.reset(seed)

    def act(self, view, legal):
        if view["my_role"] != WOLF:
            return self.good.act(view, legal)
        wolves = sorted([view["pid"]] + list(view.get("teammates") or []))
        if wolves.index(view["pid"]) < self.n_liars:
            return self.wolf.act(view, legal)
        return self.silent.act(view, legal)


class Table(object):
    """一桌人：名字、公开信息播报。"""

    def __init__(self, names, human_count):
        self.names = list(names)
        self.human_count = int(human_count)
        self.seen_deaths = 0
        self.quiet = False

    def pname(self, pid):
        return "座位%d·%s" % (pid, self.names[pid])

    def announce_deaths(self, view):
        if self.quiet:
            return
        pub = view["public"]
        deaths = pub.get("deaths") or []
        if len(deaths) <= self.seen_deaths:
            return
        for d in deaths[self.seen_deaths:]:
            cause = {"WOLF_KILL": "被狼刀", "POISON": "被毒",
                     "VOTE": "被投票出局", "SHOT": "被枪杀"}.get(d.get("cause"),
                                                                 d.get("cause"))
            when = "白天" if d.get("cause") == "VOTE" or d.get("phase") == "day" else "夜间"
            print("  ※ 第%d天%s：%s %s" % (
                d.get("day", "?"), when, self.pname(d["pid"]), cause))
        self.seen_deaths = len(deaths)

    def print_action(self, view, action, name):
        if self.quiet:
            return
        phase = PHASE_CN.get(view["phase"], view["phase"])
        print("  [%s] %s：%s" % (phase, name, A.describe(action)))
        if getattr(self, "delay", 0):
            import time
            time.sleep(self.delay)

    def full_log(self, view):
        pub = view["public"]
        print("\n———— 公开信息（所有人都能看到）————")
        by_day = {}
        for s in pub.get("speeches") or []:
            by_day.setdefault(s["day"], {"speeches": [], "votes": [], "deaths": []})
            by_day[s["day"]]["speeches"].append(s)
        for v in pub.get("votes") or []:
            by_day.setdefault(v["day"], {"speeches": [], "votes": [], "deaths": []})
            by_day[v["day"]]["votes"].append(v)
        for d in pub.get("deaths") or []:
            by_day.setdefault(d["day"], {"speeches": [], "votes": [], "deaths": []})
            by_day[d["day"]]["deaths"].append(d)
        for day in sorted(by_day):
            print("第 %d 天：" % day)
            for s in by_day[day]["speeches"]:
                print("  发言 %s：%s" % (self.pname(s["pid"]),
                                        A.describe(s["action"])))
            for v in by_day[day]["votes"]:
                tgt = "弃票" if v["target"] is None else self.pname(v["target"])
                print("  投票 %s → %s" % (self.pname(v["voter"]), tgt))
            for d in by_day[day]["deaths"]:
                cause = {"WOLF_KILL": "被狼刀", "POISON": "被毒",
                         "VOTE": "被投票出局", "SHOT": "被枪杀"}.get(
                             d.get("cause"), d.get("cause"))
                print("  死亡 %s（%s）" % (self.pname(d["pid"]), cause))
        if not by_day:
            print("  （还没有发生任何公开事件）")
        print("存活：%s" % "、".join(self.pname(p) for p in view["alive"]))
        print("————————————————————————————\n")
        self.seen_deaths = len(pub.get("deaths") or [])


# ==================== 人类玩家 ====================

class HumanAgent(object):
    human = True

    def __init__(self, name):
        self.name = name

    def reset(self, seed):
        pass

    def _handover(self, view):
        clear()
        print("=" * 60)
        if self.human_count > 1:
            print("请把屏幕交给  %s" % self.name)
            print("（其他人请回避。按回车后才显示身份和信息）")
        else:
            print("%s · 白天/夜晚行动" % self.name)
        input("按回车继续……")
        clear()
        print("你的座位：%d 号" % view["pid"])
        print("你的身份：%s（%s）" % (role_cn(view["my_role"]),
                                      faction_cn(view["my_faction"])))
        if view.get("teammates"):
            print("你的狼同伴：%s" % "、".join(
                "座位%d" % p for p in view["teammates"]))
        if view["my_role"] == "SEER":
            res = view.get("seer_results") or {}
            if res:
                print("你验过的人：" + "；".join(
                    "%d号=%s" % (t, "狼" if w else "好人") for t, w in sorted(res.items())))
            else:
                print("你还没验过人。")
        if view["my_role"] == "WITCH":
            w = view.get("witch") or {}
            print("药：解药%s，毒药%s；今夜被刀：%s" % (
                "有" if w.get("has_antidote") else "无",
                "有" if w.get("has_poison") else "无",
                ("%d号" % w["night_victim"]) if w.get("night_victim") is not None else "无人"))
        print("第 %d 天 · %s" % (view["day"], PHASE_CN.get(view["phase"], view["phase"])))
        print()

    def act(self, view, legal):
        self._handover(view)
        table = self.table
        table.full_log(view)
        phase = view["phase"]
        if phase == "day_speech":
            return self._speech(view, legal)
        if phase == "day_vote":
            return self._vote(view, legal)
        if phase == "night_wolf":
            return self._wolf(view, legal)
        if phase == "night_witch":
            return self._witch(view, legal)
        if phase == "night_seer":
            return self._seer(view, legal)
        if phase == "hunter_shot":
            return self._shoot(view, legal)
        return self._pick_legal(legal)

    # ---- 各阶段 ----
    def _speech(self, view, legal):
        print("发言三选一：")
        print("  1. 沉默")
        print("  2. 我是 X（自报身份）")
        print("  3. 我认为 X 是 Y（指认某人是什么）")
        while True:
            c = input("选 1/2/3：").strip()
            if c == "1":
                a = A.silence()
                if a in legal:
                    return a
            if c == "2":
                print("身份：1狼人 2平民 3女巫 4预言家 5猎人")
                try:
                    r = ROLE_ORDER[int(input("选 1-5：").strip()) - 1]
                except (ValueError, IndexError):
                    print("输入不对。")
                    continue
                a = A.self_claim(r)
                if a in legal:
                    return a
                print("这个动作不合法。")
                continue
            if c == "3":
                targets = [t for t in view["alive"] if t != view["pid"]]
                print("指认谁：" + "  ".join("%d=%s" % (t, self.table.names[t])
                                             for t in targets))
                try:
                    t = int(input("座位号：").strip())
                except ValueError:
                    print("输入不对。")
                    continue
                print("说他是：1狼人 2平民 3女巫 4预言家 5猎人")
                try:
                    r = ROLE_ORDER[int(input("选 1-5：").strip()) - 1]
                except (ValueError, IndexError):
                    print("输入不对。")
                    continue
                a = A.accuse(t, r)
                if a in legal:
                    return a
                print("这个动作不合法。")
                continue
            print("请输入 1/2/3。")

    def _vote(self, view, legal):
        targets = [t for t in view["alive"] if t != view["pid"]]
        print("投票给谁（-1=弃票）：")
        for t in targets:
            print("  %d = %s" % (t, self.table.names[t]))
        while True:
            try:
                t = int(input("座位号：").strip())
            except ValueError:
                print("输入数字。")
                continue
            a = A.abstain() if t < 0 else A.vote(t)
            if a in legal:
                return a
            print("不能投这个人（可能已出局/不能投自己）。")

    def _wolf(self, view, legal):
        print("今晚刀谁（你们狼人各自出刀，主刀狼的选择生效；系统轮换主刀）：")
        for a in legal:
            print("  %d = %s" % (a["target"], self.table.names[a["target"]]))
        while True:
            try:
                t = int(input("座位号：").strip())
            except ValueError:
                print("输入数字。")
                continue
            a = A.kill(t)
            if a in legal:
                return a
            print("这个目标不合法（不能刀狼）。")

    def _witch(self, view, legal):
        print("女巫行动（同夜只能用一瓶，不能自救）：")
        opts = []
        save = [a for a in legal if a["type"] == A.WITCH and a["potion"] == A.POTION_SAVE]
        pois = [a for a in legal if a["type"] == A.WITCH and a["potion"] == A.POTION_POISON]
        if save:
            opts.append(("1", "用解药救今晚被刀的人", save[0]))
        if pois:
            opts.append(("2", "用毒药毒一个人", None))
        opts.append(("0", "不用药", A.witch_none()))
        for k, label, _ in opts:
            print("  %s. %s" % (k, label))
        while True:
            c = input("选 0/1/2：").strip()
            for k, _, a in opts:
                if c == k and a is not None:
                    return a
            if c == "2" and pois:
                for a in pois:
                    print("  %d = %s" % (a["target"], self.table.names[a["target"]]))
                try:
                    t = int(input("毒谁：").strip())
                except ValueError:
                    print("输入数字。")
                    continue
                a = A.witch_poison(t)
                if a in legal:
                    return a
                print("不能毒这个人。")
                continue
            print("输入不对。")

    def _seer(self, view, legal):
        print("今晚验谁（只有你知道结果）：")
        for a in legal:
            print("  %d = %s" % (a["target"], self.table.names[a["target"]]))
        while True:
            try:
                t = int(input("座位号：").strip())
            except ValueError:
                print("输入数字。")
                continue
            a = A.check(t)
            if a in legal:
                return a
            print("不能验这个人。")

    def _shoot(self, view, legal):
        print("你是猎人，可以开枪带走一个人（也可以不开）：")
        for a in legal:
            if a["type"] == A.SHOOT:
                print("  %d = %s" % (a["target"], self.table.names[a["target"]]))
        print("  -1 = 不开枪")
        while True:
            try:
                t = int(input("座位号：").strip())
            except ValueError:
                print("输入数字。")
                continue
            a = A.no_shoot() if t < 0 else A.shoot(t)
            if a in legal:
                return a
            print("这个目标不合法。")

    def _pick_legal(self, legal):
        for i, a in enumerate(legal, 1):
            print("  %d. %s" % (i, A.describe(a)))
        while True:
            try:
                i = int(input("选一个：").strip())
                return legal[i - 1]
            except (ValueError, IndexError):
                print("输入不对。")


class ScriptedHuman(HumanAgent):
    """自测用：不读键盘，按简单策略代打（记录标 selftest=true）。"""

    def __init__(self, name="自测玩家", seed=20261113):
        super().__init__(name)
        self.rng = random.Random(seed)

    def act(self, view, legal):
        phase = view["phase"]
        if phase == "day_speech":
            targets = [t for t in view["alive"] if t != view["pid"]]
            cand = [a for a in legal if a["type"] == A.ACCUSE
                    and a.get("role") == WOLF and a.get("target") in targets]
            if cand and self.rng.random() < 0.7:
                return self.rng.choice(cand)
            claims = [a for a in legal if a["type"] == A.SELF_CLAIM]
            if claims and self.rng.random() < 0.5:
                return self.rng.choice(claims)
            a = A.silence()
            return a if a in legal else legal[0]
        if phase == "day_vote":
            if self.rng.random() < 0.1:
                a = A.abstain()
                if a in legal:
                    return a
            cand = [a for a in legal if a["type"] == A.VOTE and a["target"] != view["pid"]]
            return self.rng.choice(cand) if cand else legal[0]
        if phase == "night_witch":
            save = [a for a in legal if a["type"] == A.WITCH and a["potion"] == A.POTION_SAVE]
            if save:
                return save[0]
            return A.witch_none() if A.witch_none() in legal else legal[0]
        # 狼刀 / 验人 / 开枪：随机合法目标
        return self.rng.choice(legal)


class RecordedAgent(object):
    """记录 + 播报包装。observations 存的是 view（只含该座位可见信息）。"""

    def __init__(self, real, table, records):
        self.real = real
        self.table = table
        self.records = records
        self.name = getattr(real, "name", real.__class__.__name__)
        if getattr(real, "human", False):
            real.table = table
            real.human_count = table.human_count

    def reset(self, seed):
        self.real.reset(seed)

    def act(self, view, legal):
        if not getattr(self.real, "human", False):
            self.table.announce_deaths(view)
        action = self.real.act(view, legal)
        if action not in legal:
            # 与环境的兜底保持一致，避免记录里出现"环境实际没执行"的动作
            action = legal[0]
        rec = {
            "t": len(self.records),
            "day": view["day"],
            "night_no": view["night_no"],
            "phase": view["phase"],
            "seat": view["pid"],
            "observation": copy.deepcopy(view),
            "legal_codes": [A.encode(a) for a in legal],
            "action": copy.deepcopy(action),
            "code": A.encode(action),
            "text": A.describe(action),
        }
        self.records.append(rec)
        if not getattr(self.real, "human", False):
            self.table.print_action(view, action, self.name)
        return action


_MODEL_ERR = None


def model_available():
    """GUI 用：当前 Python 是否有 torch / 接口包 / 模型文件。"""
    global _MODEL_ERR
    if _MODEL_ERR is not None:
        return False
    try:
        ww = _load_ww_iface()
        ww.load_models()
        return True
    except Exception as e:
        _MODEL_ERR = repr(e)
        return False


def model_unavailable_reason():
    if model_available():
        return ""
    return _MODEL_ERR or "未检测到 torch / 模型文件"


def roles_for_seed(seed):
    return _load_ww_iface().roles_for_seed(seed)


def make_agents(n_humans, human_names, scripted=False, seed=0, n_liars=1,
                lie_prob=0.55, ai_mode="heuristic",
                model_profile="symmetric_q_only", roles=None):
    agents = []
    model_map = None
    if ai_mode == "model":
        ww = _load_ww_iface()
        if roles is None:
            roles = ww.roles_for_seed(seed)
        model_seats = [p for p in range(C.board_size()) if p >= n_humans]
        model_map, _ctx = ww.make_adaptive_agents(
            seed, profile=model_profile, roles=roles,
            model_seats=model_seats)
    for pid in range(C.board_size()):
        if pid < n_humans:
            if scripted:
                real = ScriptedHuman(human_names[pid], seed + pid)
            else:
                real = HumanAgent(human_names[pid])
        elif model_map is not None:
            real = ResearchModelAI(model_map[pid], model_profile, pid)
        else:
            real = AdaptiveAI(n_liars=n_liars, lie_prob=lie_prob)
            real.name = "AI助手%d" % pid
        agents.append(real)
    return agents
