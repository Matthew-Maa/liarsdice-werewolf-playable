# -*- coding: utf-8 -*-
"""RA-027 网页版服务端（标准库，无额外依赖）。

本地默认 127.0.0.1:8105。
复用现有骰子/狼人杀规则与 AI，不修改它们。
"""
import argparse
import copy
import io
import json
import mimetypes
import os
import random
import sys
import threading
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE = Path(__file__).resolve().parent.parent
DICE_DIR = BASE / "liarsdice_playable"
WOLF_DIR = BASE / "werewolf_playable"
STATIC = Path(__file__).resolve().parent / "static"
ASSETS = BASE / "assets"
RECORD_ROOT = BASE / "对局记录"
sys.path.insert(0, str(DICE_DIR))
import record_lib as RL  # noqa: E402

# ---- 分别加载两个游戏的 agents，避免同名 vendor 包互相覆盖 ----
import agents as DiceAG  # noqa: E402
from vendor.liarsdice.game.env import LiarDiceEnv  # noqa: E402
from vendor.liarsdice.game.logic import is_valid_bid  # noqa: E402

for _k in [k for k in list(sys.modules) if k == "agents" or k == "vendor" or k.startswith("vendor.")]:
    sys.modules.pop(_k, None)
sys.path.remove(str(DICE_DIR))
sys.path.insert(0, str(WOLF_DIR))
import agents as WolfAG  # noqa: E402
from vendor.werewolf.game.env import WerewolfEnv  # noqa: E402
from vendor.werewolf.game import actions as WA  # noqa: E402
from vendor.werewolf.game.roles import role_cn, faction_cn  # noqa: E402

import user_db as UDB  # noqa: E402
import achievements as ACH  # noqa: E402

FACES = {1: "①", 2: "②", 3: "③", 4: "④", 5: "⑤", 6: "⑥"}
CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六",
      7: "七", 8: "八", 9: "九", 10: "十"}
DICE_N = 5
SPECIAL = 1
AI_DELAY = 1.5          # 人机模式：AI 思考时间（秒），避免秒回
WOLF_MODEL = None       # 可选：研究模型档（需要 torch + LRS_075 接口包）
DICE_MODEL = None       # 可选：骰子研究权重档（LSTM-BC/LSTM-DQN）
WOLF_MODEL_LABEL = {
    "symmetric_q_only": "LRS_070 Q-only（唯一双正 / 推荐部署档）",
    "auto_hybrid": "LRS_069+LRS_070 混合（未联合验证）",
    "good_three_piece": "LRS_069 好人侧",
    "q_seer_fair": "LRS_069 Q+Seer（关 Head-A）",
    "wolf_kit_experimental": "LRS_070 狼套件（已判死）",
}

DICE_OPP = {
    "baseline": dict(name="老实人", portrait="portrait_1.jpg", diff="入门",
                     style="稳健 · 不诈唬", stars=1,
                     blurb="按自己的牌算概率，不记你，也不会故意骗你。"),
    "peak": dict(name="老江湖", portrait="portrait_3.jpg", diff="进阶",
                 style="压迫 · 敢开敢加", stars=2,
                 blurb="气场很足，喜欢把叫价往上顶；你一犹豫，他就加码。"),
    "memory": dict(name="记忆师", portrait="portrait_5.jpg", diff="高手",
                   style="记仇 · 盯你的习惯", stars=3,
                   blurb="你上一局怎么开、怎么加，它都记着。"),
    "bayes": dict(name="读心者", portrait="portrait_2.jpg", diff="专家",
                  style="读牌 · 越玩越懂你", stars=4,
                  blurb="把你的手牌当未知数，一局一局更新判断。"),
}
WOLF_NAMES = ["阿岚", "老周", "小满", "阿泽", "林夕", "阿川", "小北"]
PORTRAITS = ["portrait_1.jpg", "portrait_2.jpg", "portrait_3.jpg",
             "portrait_4.jpg", "portrait_5.jpg"]


def bid_text(q, f):
    return "叫 %s 个 %s" % (CN.get(q, q), FACES.get(f, f))


def _grant(user_id, codes):
    out = []
    for code in dict.fromkeys(codes):
        if UDB.award(user_id, code):
            name = ACH.DEFS.get(code, (code, ""))[0]
            out.append({"code": code, "name": name})
    return out


def _award_common(user_id, game, win):
    """首次/累计/连胜类成就；在 record_game 之后调用。"""
    if not user_id:
        return []
    st = UDB.stats(user_id)
    codes = []
    if st["games"] == 1:
        codes.append("first_game")
    if win and st["wins"] == 1:
        codes.append("first_win")
    if st["games"] == 10:
        codes.append("ten_games")
    if win:
        recent = UDB.list_games(user_id, 3)
        if len(recent) == 3 and all(r["result"] == "win" for r in recent):
            codes.append("win3")
    gs = UDB.stats(user_id, game)
    if win and game == "liarsdice" and gs["wins"] >= 10:
        codes.append("dice_win10")
    if win and game == "werewolf" and gs["wins"] >= 3:
        codes.append("wolf_win3")
    return _grant(user_id, codes)


def now_seed():
    return random.randrange(1, 10 ** 9)


# ==================== 骰子 ====================

class DiceGame:
    def __init__(self, mode="human", opponent="peak", ai2="bayes", name="你",
                 seed=None, research=False, user=None):
        self.mode = mode
        self.opponent = opponent
        self.ai2 = ai2
        self.name = name or "你"
        self.research = bool(research)
        self.user = user or None
        self.new_achievements = []
        self.seed = int(seed or now_seed())
        self.special = SPECIAL
        self.hands = [[random.randint(1, 6) for _ in range(DICE_N)]
                      for _ in range(2)]
        self.turn = random.randint(0, 1)
        self.first = True
        self.last_q = 0
        self.last_f = 0
        self.special_now = self.special
        self.history = []
        self.actions = []
        self.log = []
        self.over = False
        self.result = None
        self.record_path = None
        self.payload = None
        self.total = None
        self.thinking = False
        self.ai_timer = None
        self.aborted = False
        self.created = time.time()
        self.lock = threading.Lock()
        if mode == "ai":
            self.ai = [DiceAG.make_ai(opponent, "whole", research=self.research),
                       DiceAG.make_ai(ai2, "whole", research=self.research)]
        else:
            self.ai = [None, DiceAG.make_ai(opponent, "whole",
                                            research=self.research)]
        for a in self.ai:
            if a:
                a.start_game()
        if mode == "human" and self.turn == 1:
            self._schedule_ai()
        elif mode == "ai" and self.turn == 0:
            pass

    # ---- 内部 ----
    def _schedule_ai(self, delay=None):
        """人机模式：让 AI 隔一会儿再动，前端能看到“正在想”。"""
        self.thinking = True
        t = threading.Timer(AI_DELAY if delay is None else delay,
                            self._ai_after_delay)
        t.daemon = True
        self.ai_timer = t
        t.start()

    def _ai_after_delay(self):
        with self.lock:
            if self.over or self.aborted:
                self.thinking = False
                return
            if self.mode != "human" or self.turn != 1:
                self.thinking = False
                return
            self.thinking = False
            self._ai_step()
    def _entry(self, seat):
        if self.mode == "human" and seat == 0:
            return {"seat": 0, "kind": "human", "name": self.name}
        key = self.opponent if (seat == 0 or self.mode == "human") else self.ai2
        info = (DiceAG.RESEARCH_INFO if self.research else DiceAG.AI_INFO)[key]
        o = DICE_OPP[key]
        version = info["agent_version"]
        if not self.research and key == "memory":
            version += "-whole"
        return {"seat": seat, "kind": "ai", "name": o["name"],
                "agent_id": info["agent_id"],
                "agent_version": version,
                "card": info["card"],
                # 研究权重是固定策略，不会在对局中学习；
                # 不能再沿用行为卡里"记仇 / 越玩越懂你"的难度文案。
                "level": info["level"] if self.research else
                         ("%s · %s" % (o["diff"], o["style"]))}

    def _record(self, seat, own, action):
        hist = [{"seat": s, "qty": q, "face": f} for q, f, s in self.history]
        obs = {"seat": seat, "round": len(self.actions) + 1,
               "own_dice": list(own), "dice_total": DICE_N * 2,
               "special_rule": int(self.special_now), "history": hist,
               "last_bid": (None if not hist else
                            {"seat": hist[-1]["seat"], "qty": hist[-1]["qty"],
                             "face": hist[-1]["face"]}),
               "visible": "只有自己的骰子 + 公开叫牌历史（非上帝视角）"}
        if action[0] == "call":
            act = {"type": "CALL", "qty": int(action[1]), "face": int(action[2])}
            code = "CALL:%d:%d" % (action[1], action[2])
            txt = bid_text(action[1], action[2])
            self.history.append((int(action[1]), int(action[2]), seat))
            self.log.append("%s：%s" % (self._entry(seat)["name"], txt))
        else:
            act = {"type": "OPEN"}
            code, txt = "OPEN", "开！"
            self.log.append("%s：开！" % self._entry(seat)["name"])
        self.actions.append({"t": len(self.actions), "seat": seat,
                             "phase": "bidding" if action[0] == "call" else "challenge",
                             "observation": obs, "action": act,
                             "code": code, "text": txt})

    def _apply(self, seat, action):
        if action[0] == "challenge" and self.first:
            return False, "先手不能开盅"
        if action[0] == "call":
            if not is_valid_bid(action[1], action[2], self.last_q, self.last_f,
                                self.first, self.special_now):
                return False, "这手不合法"
            self._record(seat, self.hands[seat], action)
            self.last_q, self.last_f = int(action[1]), int(action[2])
            if self.last_f == 1:
                self.special_now = 0
            self.first = False
            self.turn = 1 - seat
            return True, None
        # open
        self._record(seat, self.hands[seat], action)
        target = self.last_f
        total = sum(1 for d in self.hands[0] + self.hands[1]
                    if d == target or (self.special_now and d == 1))
        self.total = total
        caller = 1 - seat
        winner = caller if total >= self.last_q else seat
        self.over = True
        self.payload = {"dice": [list(self.hands[0]), list(self.hands[1])],
                        "last_qty": self.last_q, "last_face": self.last_f,
                        "special_rule": self.special_now, "winner": winner}
        for a in self.ai:
            if a:
                a.on_challenge_result(self.last_q, total)
                f = getattr(a, "on_dice_revealed", None)
                if f:
                    f(self.payload, 0 if a is self.ai[0] else 1)
        self.result = {
            "winner_seat": int(winner),
            "winner_name": self._entry(winner)["name"],
            "loser_seat": 1 - int(winner),
            "reason": ("开盅成功：叫牌不成立" if winner == seat
                       else "开盅失败：叫牌成立"),
            "challenger_seat": seat, "caller_seat": caller,
            "final_bid": {"qty": self.last_q, "face": self.last_f,
                          "text": bid_text(self.last_q, self.last_f)},
            "revealed_total": total,
            "special_rule": int(self.special_now),
            "hands": {"p0": list(self.hands[0]), "p1": list(self.hands[1])},
            "rounds": len(self.actions),
        }
        self._write()
        return True, None

    def _ai_step(self):
        seat = self.turn
        ai = self.ai[seat]
        if not ai:
            return
        hist = [(q, f, p == seat) for q, f, p in self.history]
        st = {"own_dice": self.hands[seat], "dice_num": DICE_N,
              "special_rule": self.special_now, "first_turn": self.first,
              "last_qty": self.last_q, "last_face": self.last_f,
              "history": hist}
        act = ai.decide(st)
        self._apply(seat, act)

    def _write(self, aborted=False):
        if not self.result:
            return
        players = [self._entry(0), self._entry(1)]
        meta = {"rules": {"dice_per_player": DICE_N, "special_rule": self.special,
                          "special_rule_cn": "1 是百搭（叫过 1 之后百搭失效）",
                          "dice_total": DICE_N * 2},
                "source": "web"}
        if self.research:
            meta["ai_engine"] = "research_model"
            meta["dice_engine"] = "LSTM-BC / LSTM-DQN（rule1 百搭线，真实权重）"
            meta["note"] = "seed 可复现发牌与先手；模型只读，不跨局更新"
        else:
            meta["ai_engine"] = "behavior_card"
            meta["note"] = "seed 可复现发牌与先手；AI 跨局记忆会改变后续决策"
        if self.user:
            meta["user"] = {"id": self.user["id"], "name": self.user["nick"]}
        if aborted:
            meta["aborted"] = True
            meta["ended_by"] = "human_quit"
        rec = RL.make_record("liarsdice", self.seed,
                             "ai_vs_ai" if self.mode == "ai" else "human_vs_ai",
                             players, self.actions, self.result, meta=meta)
        path = RL.write_record(rec, str(RECORD_ROOT), "liarsdice")
        self.rec = rec
        self.record_path = str(path)
        print("[record] liarsdice ->", path, flush=True)
        self._on_saved(aborted)

    def _on_saved(self, aborted=False):
        if not self.user:
            return
        uid = self.user["id"]
        if aborted:
            UDB.record_game(uid, "liarsdice", "aborted", "human",
                            "中途退出（未完成）", self.record_path)
            return
        if self.mode == "ai":
            UDB.record_game(uid, "liarsdice", "spectate", "spectator",
                            self.result.get("reason", ""), self.record_path)
            self.new_achievements = _award_common(uid, "liarsdice", False)
            return
        win = self.result.get("winner_seat") == 0
        UDB.record_game(uid, "liarsdice", "win" if win else "loss", "human",
                        self.result.get("reason", ""), self.record_path)
        codes = ACH.evaluate_dice(self.result, self.mode, self.opponent)
        self.new_achievements = _grant(uid, codes)
        self.new_achievements += _award_common(uid, "liarsdice", win)

    def abort(self):
        """中途退出：把已发生的步骤写成“未完成”记录。"""
        with self.lock:
            if self.over:
                return False, "对局已结束"
            self.aborted = True
            self.thinking = False
            self.over = True
            last = {}
            for x in reversed(self.actions):
                if x["action"].get("type") == "CALL":
                    last = x["action"]
                    break
            q = int(last.get("qty", 0))
            f = int(last.get("face", 0))
            self.result = {
                "aborted": True,
                "winner_seat": -1,
                "winner_name": "",
                "loser_seat": -1,
                "reason": "中途退出（未完成）",
                "challenger_seat": None,
                "caller_seat": None,
                "final_bid": {"qty": q, "face": f,
                              "text": bid_text(q, f) if q else "未开盅"},
                "revealed_total": None,
                "special_rule": int(self.special_now),
                "hands": {"p0": list(self.hands[0]) if self.mode == "human" else [],
                          "p1": []},
                "rounds": len(self.actions),
            }
            self._write(aborted=True)
            return True, None

    # ---- 对外 ----
    def human_action(self, action):
        with self.lock:
            if self.over:
                return False, "对局已结束"
            if self.mode != "human" or self.turn != 0:
                return False, "还没轮到你"
            ok, err = self._apply(0, action)
            if not ok:
                return False, err
            # 轮到 AI：隔 1.5 秒再动（AI 内战走 tick，不在这里）
            if not self.over and self.turn == 1 and self.mode == "human":
                self._schedule_ai()
            return True, None

    def tick(self):
        with self.lock:
            if self.over:
                return False, "对局已结束"
            if self.mode != "ai":
                return False, "只有 AI 内战可以 tick"
            self._ai_step()
            return True, None

    def snapshot(self):
        with self.lock:
            hist = [{"seat": s, "qty": q, "face": f} for q, f, s in self.history]
            s = {
                "mode": self.mode, "over": self.over, "turn": self.turn,
                "first": self.first,
                "players": [{k: v for k, v in self._entry(i).items()
                             if k in ("seat", "kind", "name", "level")}
                            for i in (0, 1)],
                "history": hist, "log": self.log[-20:],
                "current_bid": (bid_text(hist[-1]["qty"], hist[-1]["face"])
                                if hist else ""),
                "waiting": (self.mode == "human" and not self.over and self.turn == 0),
                "thinking": bool(self.thinking and not self.over),
                "you": self.name,
            }
            if self.mode == "human":
                s["own_dice"] = list(self.hands[0])
            if self.over:
                s["result"] = self.result
                s["new_achievements"] = self.new_achievements
            return s


# ==================== 狼人杀 ====================

class HumanStub:
    human = True

    def __init__(self, name):
        self.name = name

    def reset(self, seed):
        pass

    def act(self, view, legal):
        return legal[0]


class WolfAborted(Exception):
    """玩家中途退出：从环境里抛出，用来结束这一局并落“未完成”记录。"""


class WebWolfAgent:
    def __init__(self, real, pid, session, human=False):
        self.real = real
        self.pid = pid
        self.session = session
        self.human = human
        self.name = getattr(real, "name", "AI")
        # LRS_084 多发言：环境会按 ag.said 收紧下一次合法集
        self.said = {"claims": set(), "targets": set(), "n": 0}
        self.end = False
        self._said_day = None

    def reset(self, seed):
        f = getattr(self.real, "reset", None)
        if f:
            f(seed)

    def _sync_speech(self, view):
        if view.get("phase") != "day_speech":
            return
        d = view.get("day")
        if d != self._said_day:
            self._said_day = d
            self.said = {"claims": set(), "targets": set(), "n": 0}
            self.end = False

    def _note_speech(self, view, action):
        if self.session.speech_mode != "multi":
            return
        if view.get("phase") != "day_speech":
            return
        self._sync_speech(view)
        typ = (action or {}).get("type")
        if typ == "SELF_CLAIM":
            self.said["claims"].add(action.get("role"))
        elif typ == "ACCUSE":
            self.said["targets"].add(action.get("target"))
        elif typ == "SILENCE":
            self.end = True
        self.said["n"] += 1
        if self.said["n"] >= 3:
            self.end = True

    def act(self, view, legal):
        if self.session.stop:
            raise WolfAborted()
        with self.session.cv:
            self.session.last_view = copy.deepcopy(view)
            self.session.last_pid = self.pid
            if self.human:
                self.session.pending = {"pid": self.pid,
                                        "view": copy.deepcopy(view),
                                        "legal": copy.deepcopy(legal)}
                self.session.cv.notify_all()
                while self.session.pending is not None and not self.session.stop:
                    self.session.cv.wait()
                if self.session.stop:
                    self.session.pending = None
                    raise WolfAborted()
                action = self.session.response
            else:
                action = None
        if not self.human:
            self.session.ai_sleep(0.9)
            if self.session.stop:
                raise WolfAborted()
            # 多发言第 2/3 条：不再把单发言 agent 随机挑到的
            # "某人是女巫/预言家"原样放出去；只让它继续指认狼或沉默。
            if (self.session.speech_mode == "multi"
                    and view.get("phase") == "day_speech"
                    and self.said.get("n", 0) >= 1):
                import env_multi as _EM
                action = _EM.coherent_extra_speech(
                    view, legal, self.said, self.real)
            else:
                action = self.real.act(view, legal)
        if action is None or action not in legal:
            action = legal[0]
        self._note_speech(view, action)
        self.session.record(view, legal, action, self.pid)
        with self.session.cv:
            self.session.last_view = copy.deepcopy(view)
            self.session.cv.notify_all()
        return action


class WolfGame:
    def __init__(self, humans=1, names=None, mode="mixed", seed=None,
                 model_profile=None, speech_mode="single", user=None):
        self.mode = mode
        self.humans = int(humans)
        self.names = list(names or [])
        self.user = user or None
        self.new_achievements = []
        self.speech_mode = speech_mode if speech_mode in ("single", "multi") else "single"
        requested = (model_profile or WOLF_MODEL or "").strip() or None
        self.requested_model = requested
        # LRS_084：多发言档强制启发式（研究模型输入分布不匹配）
        self.model_profile = None if self.speech_mode == "multi" else requested
        self.seed = int(seed or now_seed())
        self.cv = threading.Condition()
        self.pending = None
        self.response = None
        self.stop = False
        self.last_view = None
        self.last_pid = None
        self.env = None
        self.actions = []
        self.over = False
        self.result = None
        self.rec = None
        self.record_path = None
        self.paused = False
        self.speed = 0.5
        self.resume_ev = threading.Event()
        self.resume_ev.set()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def ai_sleep(self, base):
        remain = base / max(0.25, float(self.speed))
        while remain > 0:
            if self.stop:
                return
            with self.cv:
                if self.paused:
                    self.cv.wait_for(lambda: not self.paused or self.stop, timeout=0.2)
            step = min(0.05, remain)
            time.sleep(step)
            remain -= step

    def _players(self):
        out = []
        ai_i = 0
        for pid in range(7):
            if pid < self.humans:
                nm = self.names[pid] if pid < len(self.names) else "玩家%d" % (pid + 1)
                out.append(RL.player_entry(pid, "human", nm))
            else:
                nm = WOLF_NAMES[ai_i % len(WOLF_NAMES)]
                ai_i += 1
                if self.model_profile:
                    meta = WolfAG.MODEL_META[self.model_profile]
                    e = RL.player_entry(
                        pid, "ai", nm,
                        agent_id=meta["agent_id"],
                        agent_version=meta["version"],
                        card=meta["cards"],
                        level=meta["level"])
                    e["model_profile"] = self.model_profile
                    out.append(e)
                else:
                    out.append(RL.player_entry(
                        pid, "ai", nm,
                        agent_id=WolfAG.AI_META["agent_id"],
                        agent_version=WolfAG.AI_META["agent_version"],
                        card=WolfAG.AI_META["card"],
                        level="AI · 标准"))
        return out

    def name_of(self, pid):
        if pid < self.humans and pid < len(self.names):
            return self.names[pid]
        return WOLF_NAMES[(pid - self.humans) % len(WOLF_NAMES)]

    def record(self, view, legal, action, pid):
        with self.cv:
            e = {
                "t": len(self.actions), "day": view["day"],
                "night_no": view["night_no"], "phase": view["phase"],
                "seat": pid, "observation": copy.deepcopy(view),
                "legal_codes": [WA.encode(x) for x in legal],
                "action": copy.deepcopy(action), "code": WA.encode(action),
                "text": WA.describe(action)}
            if self.speech_mode == "multi" and view["phase"] == "day_speech":
                e["speech_order"] = sum(
                    1 for a in self.actions
                    if a["phase"] == "day_speech"
                    and a.get("day") == view["day"] and a.get("seat") == pid)
            self.actions.append(e)

    def _write_record(self, players, result, aborted=False):
        meta = {"rules": {"board": {"WOLF": 2, "VILLAGER": 2, "WITCH": 1,
                                    "SEER": 1, "HUNTER": 1},
                          "win_mode": "parity",
                          "night_order": ["WOLF", "WITCH", "SEER"],
                          "speech": "SILENCE / SELF_CLAIM / ACCUSE",
                          "vote": "VOTE / ABSTAIN"},
                "agents": {str(p["seat"]): p["name"] for p in players},
                "source": "web"}
        if self.user:
            meta["user"] = {"id": self.user["id"], "name": self.user["nick"]}
        if self.model_profile:
            m = WolfAG.MODEL_META[self.model_profile]
            meta.update({"ai_engine": "research_model",
                         "model_profile": self.model_profile,
                         "model_version": m["version"],
                         "model_cards": m["cards"]})
            if m.get("warning"):
                meta["model_warning"] = m["warning"]
            model_info = {
                "model_profile": self.model_profile,
                "model_version": m["version"],
                "model_cards": m["cards"],
                "model_note": "游戏胜率不是研究结果；研究口径见 LRS_069/070。",
            }
        else:
            meta.update({"ai_engine": "heuristic",
                         "model_profile": "heuristic",
                         "model_version": "baseline_v0",
                         "model_cards": "baseline 3/4（heuristic + lying_wolf）"})
            model_info = {
                "model_profile": "heuristic",
                "model_version": "baseline_v0",
                "model_cards": "baseline 3/4（heuristic + lying_wolf）",
                "model_note": "启发式 AI，非研究模型。",
            }
        if self.speech_mode == "multi":
            meta["speech_mode"] = "multi"
            meta["speech_rule"] = ("每天每座最多 1 条自报 + 2 条指认；"
                                   "不重复目标、不指自己/死者；空序列=沉默")
            meta["speech_warning"] = ("多发言实验档：研究模型未适配（输入分布不匹配），"
                                      "本局强制启发式；不得据此说多发言有效")
            if self.requested_model:
                meta["model_disabled_for_multi"] = self.requested_model
        if aborted:
            meta["aborted"] = True
            meta["ended_by"] = "human_quit"
        rec = RL.make_record("werewolf", self.seed,
                             "ai_vs_ai" if self.humans == 0 else "mixed",
                             players, self.actions, result, meta=meta,
                             model_info=model_info)
        self.rec = rec
        path = RL.write_record(rec, str(RECORD_ROOT), "werewolf")
        self.record_path = str(path)
        print("[record] werewolf ->", path, flush=True)
        self._on_saved(result, aborted)

    def _on_saved(self, result, aborted=False):
        if not self.user:
            return
        uid = self.user["id"]
        if aborted:
            UDB.record_game(uid, "werewolf", "aborted", "human",
                            "中途退出（未完成）", self.record_path)
            return
        if self.humans <= 0:
            UDB.record_game(uid, "werewolf", "spectate", "spectator",
                            result.get("reason", ""), self.record_path)
            self.new_achievements = _award_common(uid, "werewolf", False)
            return
        reveal = result.get("reveal_after_game") or {}
        roles = reveal.get("roles") or {}
        role = roles.get("0") or ""
        winner = result.get("winner")
        win = (winner == "wolf" and role == "WOLF") or \
              (winner == "good" and role and role != "WOLF")
        UDB.record_game(uid, "werewolf", "win" if win else "loss", role,
                        result.get("reason", ""), self.record_path)
        codes = ACH.evaluate_wolf(result, self.actions, list(range(self.humans)))
        self.new_achievements = _grant(uid, codes)
        self.new_achievements += _award_common(uid, "werewolf", win)

    def _aborted_result(self, env):
        roles = {str(p.pid): p.role for p in env.players} if env else {}
        players = [p.to_dict() for p in env.players] if env else []
        r = {
            "aborted": True,
            "winner": "aborted",
            "winner_cn": "中途退出",
            "reason": "玩家中途退出（未完成）",
            "days": env.day if env else 0,
            "steps": env.steps if env else 0,
            "tie_days": env.tie_days if env else 0,
            "invalid_actions": env.invalid_actions if env else 0,
            "reveal_after_game": {"roles": roles, "players": players},
            "deaths": ([e for e in env.events if e.get("kind") == "death"]
                       if env else []),
        }
        if self.speech_mode == "multi":
            r["speech_mode"] = "multi"
            r["n_speech"] = len(getattr(env, "speech_log", [])) if env else 0
        return r

    def abort(self):
        """中途退出：停掉线程，把已发生的步骤写成“未完成”记录。"""
        with self.cv:
            if self.over:
                return False, "对局已结束"
            self.stop = True
            self.pending = None
            self.response = None
            self.last_view = None
            self.cv.notify_all()
        self.thread.join(timeout=6)
        with self.cv:
            if not self.over:
                return False, "退出记录未完成"
        return True, None

    def _run(self):
        players = []
        env = None
        try:
            players = self._players()
            roles = None
            model_map = None
            if self.model_profile:
                roles = WolfAG.roles_for_seed(self.seed)
                ai_seats = [p for p in range(7) if p >= self.humans]
                ww = WolfAG._load_ww_iface()
                model_map, _ctx = ww.make_adaptive_agents(
                    self.seed, profile=self.model_profile, roles=roles,
                    model_seats=ai_seats)
            reals = []
            for pid in range(7):
                if pid < self.humans:
                    reals.append(WebWolfAgent(
                        HumanStub(self.name_of(pid)), pid, self, human=True))
                elif model_map is not None:
                    reals.append(WebWolfAgent(
                        WolfAG.ResearchModelAI(
                            model_map[pid], self.model_profile, pid),
                        pid, self, human=False))
                else:
                    ai = WolfAG.AdaptiveAI(n_liars=1, lie_prob=0.55)
                    ai.name = self.name_of(pid)
                    reals.append(WebWolfAgent(ai, pid, self, human=False))
            if self.speech_mode == "multi":
                import env_multi as EM
                env = EM.MultiSpeechEnv(
                    reals, seed=self.seed, record=True,
                    roles=list(roles) if roles else None,
                    speech_mode="multi",
                    cell=("web_%dh" % self.humans) if self.humans else "web_ai")
            else:
                env = WerewolfEnv(
                    reals, seed=self.seed, record=True,
                    cell=("web_%dh" % self.humans) if self.humans else "web_ai",
                    roles=list(roles) if roles else None)
            with self.cv:
                self.env = env
                self.cv.notify_all()
            raw = env.run()
            result = {
                "winner": raw["winner"],
                "winner_cn": {"wolf": "狼人阵营", "good": "好人阵营",
                              "draw": "平局"}.get(raw["winner"], raw["winner"]),
                "reason": raw["winner_reason"], "days": raw["days"],
                "steps": raw["steps"], "tie_days": raw["tie_days"],
                "invalid_actions": raw["invalid_actions"],
                "reveal_after_game": {"roles": raw["roles"],
                                      "players": raw["players"]},
                "deaths": [e for e in raw["events"] if e.get("kind") == "death"],
            }
            if self.speech_mode == "multi":
                result["speech_mode"] = "multi"
                result["n_speech"] = len(getattr(env, "speech_log", []))
            self._write_record(players, result)
            with self.cv:
                self.result = result
                self.over = True
                self.cv.notify_all()
        except WolfAborted:
            result = self._aborted_result(env)
            if not players:
                players = self._players()
            self._write_record(players, result, aborted=True)
            with self.cv:
                self.result = result
                self.over = True
                self.pending = None
                self.cv.notify_all()
        except Exception as e:
            with self.cv:
                self.result = {"error": repr(e)}
                self.over = True
                self.cv.notify_all()

    def submit(self, pid, action):
        with self.cv:
            if self.over or not self.pending:
                return False, "现在没有等待中的行动"
            if pid != self.pending["pid"]:
                return False, "不是这个座位"
            if action not in self.pending["legal"]:
                return False, "动作不合法"
            self.response = action
            self.pending = None
            self.cv.notify_all()
            return True, None

    def snapshot(self):
        with self.cv:
            s = {"mode": self.mode, "humans": self.humans,
                 "names": [self.name_of(i) for i in range(7)],
                 "speech_mode": self.speech_mode,
                 "over": self.over, "paused": self.paused, "speed": self.speed,
                 "waiting": self.pending is not None,
                 "pending_pid": (self.pending["pid"] if self.pending else None),
                 "last_phase": (self.last_view or {}).get("phase"),
                 "log": []}
            view = self.last_view or {}
            s["day"] = view.get("day", 1)
            s["phase"] = view.get("phase", "day_speech")
            s["alive"] = view.get("alive", list(range(7)))
            s["public"] = copy.deepcopy(view.get("public") or {})
            # 公开信息/存活名单以环境实时状态为准：
            # last_view 是"决策前"的快照，若只用它，死亡和发言会晚一整轮才出现。
            env = self.env
            if env is not None and env.players:
                try:
                    s["day"] = env.day or s["day"]
                    s["alive"] = env.alive_pids()
                    s["public"] = copy.deepcopy(env.public)
                except Exception:
                    pass
            if self.pending:
                s["view"] = self.pending["view"]
                s["legal"] = self.pending["legal"]
                s["phase"] = self.pending["view"].get("phase", s["phase"])
            if self.over:
                s["result"] = self.result
                s["new_achievements"] = self.new_achievements
            return s


# ==================== HTTP ====================

SESSIONS = {}
LOCK = threading.Lock()


def new_token():
    t = uuid.uuid4().hex
    return t


class Handler(BaseHTTPRequestHandler):
    server_version = "RA027Web/1.0"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200, headers=None):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _cookie_token(self):
        raw = self.headers.get("Cookie", "") or ""
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip() == "ra_auth":
                    return v.strip()
        return ""

    def _is_host(self):
        """只有从本机 localhost 打开、且没有经过隧道转发，才允许清空本机记录。"""
        if self.headers.get("X-Forwarded-For"):
            return False
        host = (self.headers.get("Host", "") or "").split(":")[0].lower()
        return host in ("localhost", "127.0.0.1", "::1")

    def _user(self):
        try:
            return UDB.user_by_token(self._cookie_token())
        except Exception:
            return None

    @staticmethod
    def _auth_cookie(token, max_age=30 * 86400):
        return "ra_auth=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d" % (
            token, int(max_age))

    def _file(self, path, ctype=None):
        try:
            data = path.read_bytes()
        except Exception:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype or
                         mimetypes.guess_type(str(path))[0] or
                         "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)
        if path == "/api/health":
            return self._json({"ok": True, "wolf_model": WOLF_MODEL,
                               "dice_model": DICE_MODEL})
        if path.startswith("/api/dice/state"):
            tok = q.get("token", [""])[0]
            g = SESSIONS.get(tok)
            if not g:
                return self._json({"ok": False, "error": "没有这局"}, 404)
            return self._json({"ok": True, "state": g.snapshot()})
        if path.startswith("/api/werewolf/state"):
            tok = q.get("token", [""])[0]
            g = SESSIONS.get(tok)
            if not g:
                return self._json({"ok": False, "error": "没有这局"}, 404)
            return self._json({"ok": True, "state": g.snapshot()})
        if path == "/api/account":
            u = self._user()
            total = len(ACH.DEFS)
            if not u:
                return self._json({"ok": True, "user": None,
                                   "defs": [], "ach_total": total,
                                   "host": self._is_host()})
            unlocked = UDB.list_achievements(u["id"])
            names = [{"code": c, "name": ACH.DEFS[c][0]}
                     for c in ACH.DEFS if c in {x["code"] for x in unlocked}]
            return self._json({"ok": True, "user": u,
                               "stats": UDB.stats(u["id"]),
                               "games": UDB.list_games(u["id"], 60),
                               "achievements": unlocked,
                               "defs": names, "ach_total": total,
                               "host": self._is_host()})
        if path == "/api/record":
            tok = q.get("token", [""])[0]
            g = SESSIONS.get(tok)
            if not g or not getattr(g, "rec", None):
                return self._json({"ok": False, "error": "没有可下载的记录"}, 404)
            rec = g.rec
            data = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
            name = "%s_%s.jsonl" % (rec.get("game", "game"), rec.get("seed", 0))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % name)
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/export":
            u = self._user()
            if not u:
                return self._json({"ok": False, "error": "先登录"}, 401)
            lines = []
            try:
                for fp in sorted(RECORD_ROOT.glob("*/*.jsonl")):
                    with open(fp, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                r = json.loads(line)
                            except Exception:
                                continue
                            mu = (r.get("meta") or {}).get("user") or {}
                            if int(mu.get("id", -1)) == int(u["id"]):
                                lines.append(line)
            except Exception:
                pass
            if not lines:
                return self._json({"ok": False, "error": "这个账号还没有记录"}, 404)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("records.jsonl", "\n".join(lines) + "\n")
                z.writestr("说明.txt",
                           "对外游戏本地记录（账号：%s）。\n"
                           "records.jsonl 一行一局；可直接发回给组织者。\n"
                           "不包含密码、cookie 或本机绝对路径。\n" % u["nick"])
            data = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition",
                             'attachment; filename="records_%s.zip"' % u["id"])
            self.end_headers()
            self.wfile.write(data)
            return
        # static
        if path.startswith("/assets/"):
            rel = path[len("/assets/"):]
            p = (ASSETS / rel).resolve()
            if not str(p).startswith(str(ASSETS.resolve())):
                return self.send_error(403)
            return self._file(p)
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            p = (STATIC / rel).resolve()
            if not str(p).startswith(str(STATIC.resolve())):
                return self.send_error(403)
            return self._file(p)
        if path in ("/", "/index.html"):
            return self._file(STATIC / "index.html", "text/html; charset=utf-8")
        if path in ("/dice", "/dice/"):
            return self._file(STATIC / "dice.html", "text/html; charset=utf-8")
        if path in ("/werewolf", "/werewolf/"):
            return self._file(STATIC / "werewolf.html", "text/html; charset=utf-8")
        if path == "/favicon.ico":
            return self.send_error(404)
        return self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)
        body = self._body()
        try:
            if u.path == "/api/register":
                try:
                    user = UDB.register(body.get("username", ""),
                                        body.get("password", ""),
                                        body.get("nick", ""))
                except ValueError as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
                token = UDB.create_session(user["id"])
                return self._json({"ok": True, "user": user},
                                  headers={"Set-Cookie": self._auth_cookie(token)})
            if u.path == "/api/login":
                user = UDB.login(body.get("username", ""),
                                 body.get("password", ""))
                if not user:
                    return self._json({"ok": False, "error": "用户名或密码不对"}, 400)
                token = UDB.create_session(user["id"])
                return self._json({"ok": True, "user": user},
                                  headers={"Set-Cookie": self._auth_cookie(token)})
            if u.path == "/api/logout":
                UDB.delete_session(self._cookie_token())
                return self._json({"ok": True}, headers={
                    "Set-Cookie": "ra_auth=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"})
            if u.path == "/api/records/clear":
                if not self._is_host():
                    return self._json({"ok": False,
                                       "error": "只能在开服务的本机清空记录"}, 403)
                mode = body.get("mode")
                if mode not in ("delete", "backup"):
                    return self._json({"ok": False, "error": "未知清空方式"}, 400)
                files = sorted(RECORD_ROOT.glob("*/*.jsonl"))
                n_lines = 0
                for fp in files:
                    try:
                        with open(fp, "r", encoding="utf-8") as f:
                            n_lines += sum(1 for _ in f)
                    except Exception:
                        pass
                backup_name = None
                if mode == "backup":
                    try:
                        bdir = BASE / "备份"
                        bdir.mkdir(parents=True, exist_ok=True)
                        backup_name = "记录备份_%s.zip" % time.strftime("%Y%m%d_%H%M%S")
                        bpath = bdir / backup_name
                        with zipfile.ZipFile(bpath, "w", zipfile.ZIP_DEFLATED) as z:
                            for fp in files:
                                rel = fp.relative_to(RECORD_ROOT)
                                z.write(fp, "records/" + rel.as_posix())
                            z.writestr("account_data.json",
                                       json.dumps(UDB.backup_game_data(),
                                                  ensure_ascii=False, indent=2))
                            z.writestr("说明.txt",
                                       "对外游戏本机记录备份。records/ 下是 JSONL；"
                                       "account_data.json 是账号历史和成就索引（不含密码）。\n")
                    except Exception as e:
                        return self._json({"ok": False, "error": repr(e)}, 500)
                removed = 0
                for fp in files:
                    try:
                        fp.unlink()
                        removed += 1
                    except Exception:
                        pass
                games_cleared = UDB.clear_game_data()
                return self._json({"ok": True, "mode": mode,
                                   "files": removed, "lines": n_lines,
                                   "db_games": games_cleared,
                                   "backup": backup_name})
            if u.path == "/api/dice/new":
                g = DiceGame(mode=body.get("mode", "human"),
                             opponent=body.get("opponent", "peak"),
                             ai2=body.get("ai2", "bayes"),
                             name=body.get("name", "你"),
                             research=bool(DICE_MODEL), user=self._user())
                tok = new_token()
                with LOCK:
                    SESSIONS[tok] = g
                return self._json({"ok": True, "token": tok,
                                   "state": g.snapshot()})
            if u.path == "/api/dice/action":
                g = SESSIONS.get(body.get("token"))
                if not g:
                    return self._json({"ok": False, "error": "没有这局"}, 404)
                a = body.get("action") or {}
                act = ("call", int(a.get("qty", 0)), int(a.get("face", 0))) \
                    if a.get("type") == "call" else ("challenge", 0, 0)
                ok, err = g.human_action(act)
                if not ok:
                    return self._json({"ok": False, "error": err}, 400)
                return self._json({"ok": True, "state": g.snapshot()})
            if u.path == "/api/dice/tick":
                g = SESSIONS.get(body.get("token"))
                if not g:
                    return self._json({"ok": False, "error": "没有这局"}, 404)
                ok, err = g.tick()
                if not ok:
                    return self._json({"ok": False, "error": err}, 400)
                return self._json({"ok": True, "state": g.snapshot()})
            if u.path == "/api/dice/quit":
                g = SESSIONS.get(body.get("token"))
                if not g:
                    return self._json({"ok": False, "error": "没有这局"}, 404)
                ok, err = g.abort()
                if not ok:
                    return self._json({"ok": False, "error": err}, 400)
                st = g.snapshot()
                with LOCK:
                    SESSIONS.pop(body.get("token"), None)
                return self._json({"ok": True, "state": st})
            if u.path == "/api/werewolf/new":
                g = WolfGame(humans=int(body.get("humans", 1)),
                             names=body.get("names") or [],
                             mode=body.get("mode", "mixed"),
                             speech_mode=body.get("speech_mode", "single"),
                             user=self._user())
                tok = new_token()
                with LOCK:
                    SESSIONS[tok] = g
                time.sleep(0.15)
                return self._json({"ok": True, "token": tok,
                                   "state": g.snapshot()})
            if u.path == "/api/werewolf/action":
                g = SESSIONS.get(body.get("token"))
                if not g:
                    return self._json({"ok": False, "error": "没有这局"}, 404)
                ok, err = g.submit(int(body.get("pid", -1)),
                                   body.get("action") or {})
                if not ok:
                    return self._json({"ok": False, "error": err}, 400)
                return self._json({"ok": True, "state": g.snapshot()})
            if u.path == "/api/werewolf/quit":
                g = SESSIONS.get(body.get("token"))
                if not g:
                    return self._json({"ok": False, "error": "没有这局"}, 404)
                ok, err = g.abort()
                if not ok:
                    return self._json({"ok": False, "error": err}, 400)
                st = g.snapshot()
                with LOCK:
                    SESSIONS.pop(body.get("token"), None)
                return self._json({"ok": True, "state": st})
            if u.path == "/api/werewolf/control":
                g = SESSIONS.get(body.get("token"))
                if not g:
                    return self._json({"ok": False, "error": "没有这局"}, 404)
                with g.cv:
                    if "paused" in body:
                        g.paused = bool(body["paused"])
                    if "speed" in body:
                        g.speed = float(body["speed"])
                    g.cv.notify_all()
                return self._json({"ok": True, "state": g.snapshot()})
            return self._json({"ok": False, "error": "未知接口"}, 404)
        except Exception as e:
            return self._json({"ok": False, "error": repr(e)}, 500)


def main():
    global RECORD_ROOT, WOLF_MODEL, DICE_MODEL
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8105)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--record-root", default=str(RECORD_ROOT))
    ap.add_argument("--wolf-model", default="",
                    choices=[""] + list(WolfAG.MODEL_META.keys()),
                    help="狼人杀 AI 用研究权重档（需要 torch + LRS_075 接口包）")
    ap.add_argument("--dice-model", default="", choices=["", "research"],
                    help="骰子 AI 用研究真实权重（LSTM-BC/LSTM-DQN，需要 torch）")
    a = ap.parse_args()
    UDB.init()
    WOLF_MODEL = a.wolf_model.strip() or None
    if WOLF_MODEL:
        if not WolfAG.model_available():
            raise SystemExit("研究模型不可用：%s" % WolfAG.model_unavailable_reason())
        print("werewolf AI: research model /", WOLF_MODEL)
    DICE_MODEL = a.dice_model.strip() or None
    if DICE_MODEL:
        ok, why = DiceAG.research_available()
        if not ok:
            raise SystemExit("骰子研究权重不可用：%s" % why)
        print("dice AI: research weights / LSTM-BC + LSTM-DQN")
    RECORD_ROOT = Path(a.record_root)
    RECORD_ROOT.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print("RA-027 web on http://%s:%d" % (a.host, a.port))
    print("records:", RECORD_ROOT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
