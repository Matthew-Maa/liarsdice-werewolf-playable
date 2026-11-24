# -*- coding: utf-8 -*-
"""狼人杀 · pygame 画面版（RA-027）。

玩家只看到座位名、发言和结果；AI 的技术版本只写进记录，不在界面展示。
"""
import argparse
import copy
import json
import math
import queue
import random
import sys
import threading
import time
from pathlib import Path

import pygame

HERE = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else HERE
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from vendor.werewolf import config as C                      # noqa: E402
from vendor.werewolf.game.env import WerewolfEnv             # noqa: E402
from vendor.werewolf.game import actions as A                # noqa: E402
from vendor.werewolf.game.roles import role_cn, faction_cn   # noqa: E402
import agents as AG                                          # noqa: E402
import record_lib as RL                                      # noqa: E402
import env_multi as EM                                       # noqa: E402  LRS_084 多发言实验档
import gui_common as G                                       # noqa: E402
import profile as PF                                         # noqa: E402

GAME = "werewolf"
RECORD_ROOT = APP_DIR.parent / "对局记录"


class GameAborted(Exception):
    """玩家中途退出：结束这一局并写“未完成”记录。"""

AI_NAMES = ["阿岚", "老周", "小满", "阿泽", "林夕", "阿川", "小北"]
PORTRAITS = ["portrait_1.jpg", "portrait_2.jpg", "portrait_3.jpg",
             "portrait_4.jpg", "portrait_5.jpg"]
HUMAN_PORTRAIT = "portrait_4.jpg"

PHASE_CN = {
    "night_wolf": "狼人行动",
    "night_witch": "女巫行动",
    "night_seer": "预言家查验",
    "day_speech": "白天发言",
    "day_vote": "投票",
    "hunter_shot": "猎人开枪",
    "day_sheriff_vote": "警长投票",
    "sheriff_death_badge": "警徽交接",
}
ROLE_ORDER = ["WOLF", "VILLAGER", "WITCH", "SEER", "HUNTER"]


def claim_role_order(view):
    """自报身份按钮：真实身份排第一，狼人放最后（避免误点“我是狼人”）。"""
    role = view.get("my_role")
    out = [r for r in ROLE_ORDER if r == role]
    out += [r for r in ROLE_ORDER if r != role and r != "WOLF"]
    out.append("WOLF")
    return out


def role_color(role):
    return {"WOLF": G.RED_L, "VILLAGER": G.CREAM, "WITCH": (190, 130, 220),
            "SEER": (110, 180, 230), "HUNTER": (230, 170, 90)}.get(role, G.CREAM)


class HumanStub:
    human = True

    def __init__(self, name):
        self.name = name

    def reset(self, seed):
        pass

    def act(self, view, legal):
        return legal[0]


class GuiWolfAgent:
    def __init__(self, real, pid, session, human=False):
        self.real = real
        self.pid = pid
        self.session = session
        self.human = human
        self.name = getattr(real, "name", "AI")

    def reset(self, seed):
        f = getattr(self.real, "reset", None)
        if f:
            f(seed)

    def act(self, view, legal):
        if self.session.aborted:
            raise GameAborted()
        self.session.q.put(("state", self.pid, copy.deepcopy(view)))
        # LRS_084：multi 档下人类由 env 的闸门（HumanSeqAgent.gate_ev）驱动，
        # 这里不能再走下面的 human_request 等待，否则会双等。
        if (self.human and not self.session.auto_human
                and getattr(self.session, "speech_mode", "single") == "single"):
            self.session.q.put(("human_request", self.pid,
                                copy.deepcopy(view), copy.deepcopy(legal)))
            self.session.ev.wait()
            self.session.ev.clear()
            if self.session.aborted:
                raise GameAborted()
            action = self.session.action
        else:
            self.session.q.put(("thinking", self.pid))
            self.session.ai_sleep(0.9)
            if self.session.aborted:
                raise GameAborted()
            action = self.real.act(view, legal)
        if self.session.aborted:
            raise GameAborted()
        if action not in legal:
            action = legal[0]
        self.session.record(view, legal, action, self.pid)
        self.session.q.put(("action", self.pid, copy.deepcopy(view),
                            copy.deepcopy(action)))
        return action


class MultiHumanGuiAgent(GuiWolfAgent):
    """LRS_084：multi 档下的人类座位。

    与 `GuiWolfAgent` 的区别只有一处：**发言阶段不阻塞在 `human_request` 上**，
    而是把 `(view, legal)` 推给 GUI（让 GUI 画出"攒序列"界面），
    自己阻塞在 `HumanSeqAgent.act` 里等闸门放行；闸门一开，环境就逐条执行队列。

    投票/夜间仍走原来的 `human_request` 通道（那些阶段本来是单动作）。
    """

    def act(self, view, legal):
        if self.session.aborted:
            raise GameAborted()
        self.session.q.put(("state", self.pid, copy.deepcopy(view)))
        # ★ 只有**真正需要人做一次新决定**时才发 human_ready：
        #   - 非发言阶段（投票/夜间）：每次都要人点，每次都发；
        #   - 发言阶段：只在**当天第一次**发（此时 real._day 还没同步到今天）。
        #     后面第 2、3 条是环境连续执行已提交的序列，**不能再发**——
        #     否则 GUI 会把用户正在攒的 multi_seq 清空，且队列里积压的
        #     旧请求会被误当成「又轮到我了」。
        is_speech = view.get("phase") == "day_speech"
        # 用独立标记，不借 _day：夜间阶段可能已经把 _day 同步到今天了
        last = getattr(self.real, "ready_sent_day", None)
        need_ready = (not is_speech) or (last != view.get("day"))
        if need_ready:
            if is_speech:
                self.real.ready_sent_day = view.get("day")
            self.session.q.put(("human_ready", self.pid,
                                copy.deepcopy(view), copy.deepcopy(legal)))
        action = self.real.act(view, legal)
        if self.session.aborted:
            raise GameAborted()
        if action not in legal:
            action = legal[0]
        self.session.record(view, legal, action, self.pid)
        self.session.q.put(("action", self.pid, copy.deepcopy(view),
                            copy.deepcopy(action)))
        return action


class WolfSession:
    def __init__(self, n_humans=1, names=None, seed=None, auto_human=False,
                 selftest=False, ai_mode="heuristic",
                 model_profile="symmetric_q_only", speech_mode="single",
                 pending_actions=None):
        self.q = queue.Queue()
        self.ev = threading.Event()
        self.action = None
        self.actions = []
        self.lock = threading.Lock()
        self.n_humans = int(n_humans)
        self.names = list(names or [])
        self.seed = int(seed if seed is not None else random.randrange(1, 10 ** 9))
        self.auto_human = auto_human
        self.selftest = bool(selftest or auto_human)
        self.ai_mode = ai_mode
        self.model_profile = model_profile
        self.speech_mode = speech_mode          # LRS_084: single / multi
        self.pending_actions = pending_actions or {}   # pid -> [action,...]
        self._suspend = {}                      # LRS_084: pid -> HumanSeqAgent
        self.paused = False
        self.speed = 1.0
        self.aborted = False
        self.resume_ev = threading.Event()
        self.resume_ev.set()
        self.fast = False
        self.rec = None
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def set_paused(self, v):
        self.paused = bool(v)
        if self.paused:
            self.resume_ev.clear()
        else:
            self.resume_ev.set()

    def ai_sleep(self, base):
        remain = base / max(0.25, float(self.speed))
        while remain > 0:
            if self.aborted:
                return
            if self.paused:
                self.resume_ev.wait()
            step = min(0.05, remain)
            time.sleep(step)
            remain -= step

    def abort(self):
        """中途退出：叫醒等待中的人/AI，让线程把已发生步骤写成未完成记录。"""
        self.aborted = True
        self.action = None
        self.resume_ev.set()
        self.ev.set()
        if self.thread:
            self.thread.join(timeout=6)
        return self.rec

    def respond(self, action):
        self.action = action
        self.ev.set()

    def record(self, view, legal, action, pid):
        with self.lock:
            n_same = sum(1 for a in self.actions
                         if a["phase"] == "day_speech"
                         and a.get("day") == view["day"] and a.get("seat") == pid)
            e = {
                "t": len(self.actions),
                "day": view["day"],
                "night_no": view["night_no"],
                "phase": view["phase"],
                "seat": pid,
                "observation": copy.deepcopy(view),
                "legal_codes": [A.encode(x) for x in legal],
                "action": copy.deepcopy(action),
                "code": A.encode(action),
                "text": A.describe(action),
            }
            # LRS_084：多发言档才写 speech_order（默认档记录与改动前逐字节同形）
            if self.speech_mode == "multi" and view["phase"] == "day_speech":
                e["speech_order"] = n_same
            self.actions.append(e)

    def _players(self):
        out = []
        ai_i = 0
        for pid in range(C.board_size()):
            if pid < self.n_humans:
                nm = self.names[pid] if pid < len(self.names) else "玩家%d" % (pid + 1)
                out.append(RL.player_entry(pid, "human", nm))
            else:
                nm = AI_NAMES[ai_i % len(AI_NAMES)]
                ai_i += 1
                if self.ai_mode == "model":
                    meta = AG.MODEL_META[self.model_profile]
                    e = RL.player_entry(
                        pid, "ai", nm,
                        agent_id=meta["agent_id"],
                        agent_version=meta["version"],
                        card=meta["cards"],
                        level=meta["level"])
                    e["model_profile"] = self.model_profile
                    e["model_cards"] = meta["cards"]
                    out.append(e)
                else:
                    out.append(RL.player_entry(
                        pid, "ai", nm,
                        agent_id=AG.AI_META["agent_id"],
                        agent_version=AG.AI_META["agent_version"],
                        card=AG.AI_META["card"],
                        level="AI · 标准"))
        return out

    def _meta(self, players, aborted=False):
        if self.ai_mode == "model":
            m_meta = AG.MODEL_META[self.model_profile]
            model_info = {
                "model_profile": self.model_profile,
                "model_version": m_meta["version"],
                "model_cards": m_meta["cards"],
                "model_note": "游戏胜率不是研究结果；研究口径见 LRS_069/070。",
            }
            ai_engine = "research_model"
        else:
            model_info = {
                "model_profile": "heuristic",
                "model_version": "baseline_v0",
                "model_cards": "baseline 3/4（heuristic + lying_wolf）",
                "model_note": "启发式 AI，非研究模型。",
            }
            ai_engine = "heuristic"
        meta = {
            "ai_engine": ai_engine,
            "model_profile": model_info["model_profile"],
            "model_version": model_info["model_version"],
            "model_cards": model_info["model_cards"],
            "rules": {"board": {r: n for r, n in C.BOARD},
                      "n_players": C.board_size(),
                      "win_mode": C.WIN_MODE,
                      "win_mode_cn": "狼人全灭→好人胜；狼人存活数≥好人存活数→狼人胜",
                      "night_order": list(C.NIGHT_ORDER),
                      "speech_actions": "SILENCE / SELF_CLAIM(role) / ACCUSE(target, role)",
                      "vote": "VOTE(target) 或 ABSTAIN",
                      "hunter": "被刀/被票出可开枪；被毒不开枪"},
            "agents": {str(p["seat"]): p["name"] for p in players},
            "wolf_policy": {"n_liars": 1, "lie_prob": 0.55},
            "note": "observation 只含该座位可见信息；roles 只在 result.reveal_after_game 公开",
        }
        # LRS_084：只有多发言档才写这两个字段；默认档 meta 与改动前完全一致
        if self.speech_mode == "multi":
            meta["speech_mode"] = "multi"
            meta["speech_rule"] = ("每天每座最多 1 条自报 + 2 条指认；"
                                   "不重复目标、不指自己/死者；空序列=沉默")
            meta["speech_warning"] = ("多发言实验档：研究模型未适配（输入分布不匹配），"
                                      "仅启发式/纯 AI；不得据此说多发言有效")
        if self.ai_mode == "model":
            meta["model_warning"] = AG.MODEL_META[self.model_profile].get("warning")
        if aborted:
            meta["aborted"] = True
            meta["ended_by"] = "human_quit"
        return model_info, meta

    def _aborted_result(self, env):
        roles = {str(p.pid): p.role for p in env.players} if env else {}
        players = [p.to_dict() for p in env.players] if env else []
        return {
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

    def _run(self):
        players = []
        env = None
        try:
            players = self._players()
            roles = (AG.roles_for_seed(self.seed)
                     if self.ai_mode == "model" else None)
            model_map = None
            if self.ai_mode == "model":
                ww = AG._load_ww_iface()
                ai_seats = [p for p in range(C.board_size())
                            if p >= self.n_humans]
                model_map, _ctx = ww.make_adaptive_agents(
                    self.seed, profile=self.model_profile, roles=roles,
                    model_seats=ai_seats)
            reals = []
            for pid in range(C.board_size()):
                if pid < self.n_humans:
                    if self.auto_human:
                        reals.append(GuiWolfAgent(
                            AG.ScriptedHuman(self.names[pid] if pid < len(self.names)
                                             else "自测玩家", self.seed + pid),
                            pid, self, human=True))
                    else:
                        reals.append(GuiWolfAgent(
                            HumanStub(self.names[pid] if pid < len(self.names)
                                      else "玩家%d" % (pid + 1)),
                            pid, self, human=True))
                elif model_map is not None:
                    reals.append(GuiWolfAgent(
                        AG.ResearchModelAI(model_map[pid], self.model_profile, pid),
                        pid, self, human=False))
                else:
                    nm = players[pid]["name"]
                    ai = AG.AdaptiveAI(n_liars=1, lie_prob=0.55)
                    ai.name = nm
                    reals.append(GuiWolfAgent(ai, pid, self, human=False))
            if self.speech_mode == "multi":
                # LRS_084：人类用「预先攒好的整段序列」逐条喂；AI 用 SpeakingAgent 去重补全
                wrapped = []
                self._suspend = {}
                for pid, a in enumerate(reals):
                    if pid < self.n_humans:
                        if self.auto_human:
                            # ★★★ LRS_091 修的真 bug：
                            #   以前这里**不看 auto_human**，一律包 `HumanSeqAgent`。
                            #   但 HumanSeqAgent 在发言阶段会 `gate_ev.wait()` 等 GUI 点按钮，
                            #   而 `auto_human`（--selftest）路径**没有人点按钮**
                            #   ⇒ multi + 自测**永久卡死**（实测 45 秒 faulthandler 抓到
                            #   env_multi.py:203 gate_ev.wait）。
                            #   正解：auto_human 时人类座位是脚本代理（ScriptedHuman），
                            #   它自己会出招，**不需要闸门**，只包 SpeakingAgent 去重即可。
                            inner = getattr(a, "real", a)
                            wrapped.append(GuiWolfAgent(EM.SpeakingAgent(inner), pid,
                                                        self, human=True))
                            continue
                        sa = EM.HumanSeqAgent(self.names[pid]
                                              if pid < len(self.names)
                                              else "玩家%d" % (pid + 1))
                        sa.submit_sequence(self.pending_actions.get(pid) or [])
                        sa.gate_ev = threading.Event()
                        sa.gate_ev.set()        # 开局默认放行（非发言阶段由 GUI 清）
                        self._suspend[pid] = sa
                        wrapped.append(MultiHumanGuiAgent(sa, pid, self))
                    else:
                        inner = getattr(a, "real", a)
                        wrapped.append(GuiWolfAgent(EM.SpeakingAgent(inner), pid,
                                                    self, human=False))
                env = EM.MultiSpeechEnv(wrapped, seed=self.seed, record=True,
                                        cell=("mixed_%dh" % self.n_humans)
                                        if self.n_humans else "ai_all",
                                        roles=list(roles) if roles else None,
                                        speech_mode="multi")
                for pid, sa in self._suspend.items():
                    env.register_gate(pid, sa.gate_ev)
            else:
                env = WerewolfEnv(reals, seed=self.seed, record=True,
                                  cell=("mixed_%dh" % self.n_humans)
                                  if self.n_humans else "ai_all",
                                  roles=list(roles) if roles else None)
            raw = env.run()
            result = {
                "winner": raw["winner"],
                "winner_cn": {"wolf": "狼人阵营", "good": "好人阵营",
                              "draw": "平局"}.get(raw["winner"], raw["winner"]),
                "reason": raw["winner_reason"],
                "days": raw["days"],
                "steps": raw["steps"],
                "tie_days": raw["tie_days"],
                "invalid_actions": raw["invalid_actions"],
                "reveal_after_game": {"roles": raw["roles"],
                                      "players": raw["players"]},
                "deaths": [e for e in raw["events"] if e.get("kind") == "death"],
            }
            model_info, meta = self._meta(players)
            if self.speech_mode == "multi" and getattr(env, "speech_log", None):
                seq = {}
                for day, pid, order, code in env.speech_log:
                    seq.setdefault(str(day), {}).setdefault(str(pid), []).append(code)
                meta["speech_seq"] = seq
                result["speech_mode"] = "multi"
                result["n_speech"] = len(env.speech_log)
            rec = RL.make_record(GAME, self.seed,
                                 "ai_vs_ai" if self.n_humans == 0 else "mixed",
                                 players, self.actions, result, meta=meta,
                                 selftest=self.selftest,
                                 model_info=model_info)
            RL.write_record(rec, str(RECORD_ROOT), GAME)
            self.rec = rec
            self.q.put(("game_over", rec))
        except GameAborted:
            result = self._aborted_result(env)
            if not players:
                players = self._players()
            model_info, meta = self._meta(players, aborted=True)
            rec = RL.make_record(GAME, self.seed,
                                 "ai_vs_ai" if self.n_humans == 0 else "mixed",
                                 players, self.actions, result, meta=meta,
                                 selftest=self.selftest,
                                 model_info=model_info)
            RL.write_record(rec, str(RECORD_ROOT), GAME)
            self.rec = rec
        except Exception as e:
            self.q.put(("error", repr(e)))

    def run_blocking(self):
        self._run()
        return self.rec


# ==================== 界面 ====================

class WolfApp:
    def __init__(self, record_root=None, headless=False):
        global RECORD_ROOT
        if record_root:
            RECORD_ROOT = Path(record_root)
        pygame.init()
        G.ensure_sfx()
        G.make_ambient(G.ASSETS / "bgm_werewolf.wav")
        flags = pygame.HIDDEN if headless else 0
        self.screen = pygame.display.set_mode((G.W, G.H), flags)
        try:
            pygame.key.start_text_input()
        except Exception:
            pass
        pygame.display.set_caption("狼人杀 · 天黑请闭眼")
        self.clock = pygame.time.Clock()
        self.profile = PF.Profile("werewolf", APP_DIR)
        _vol = self.profile.setting_raw("music_volume", 0.22)
        _mode = self.profile.setting_raw("music_mode", "list")
        _track = self.profile.setting_raw("music_track", 4)
        if isinstance(_vol, bool) or not isinstance(_vol, (int, float)):
            _vol = 0.22
        if _mode not in ("list", "single"):
            _mode = "list"
        if isinstance(_track, bool) or not isinstance(_track, int):
            _track = 4
        self.music = G.Music(volume=float(_vol), mode=_mode, start=_track,
                             enabled=self.profile.setting("music", True))
        self.sfx = G.Sfx()
        self.sfx.enabled = self.profile.setting("sfx", True)
        self.toast = G.Toast()
        self.state = "setup"
        self.manual_page = 0
        self.moon_rect = pygame.Rect(930, 52, 200, 200)
        self.n_humans = 1
        self.ai_mode = "heuristic"
        self.model_profile = "symmetric_q_only"
        self.pending_profile = None
        # LRS_084 多发言实验档（默认关闭；只能配启发式/纯 AI）
        # ★ 档位与原有「发言菜单状态」分开，避免互相覆盖
        self.speech_variant = "single"
        self.multi_seq = []          # 本座本轮已攒的发言动作
        self.multi_ended = False
        self.model_ok = AG.model_available()
        self.model_reason = AG.model_unavailable_reason() if not self.model_ok else ""
        self.name_input = G.TextInput((G.W // 2 - 160, 392, 320, 46),
                                      value="你", max_len=10)
        self.session = None
        self.view = None
        self.day = 1
        self.phase = "day_speech"
        self.thinking = None
        self.bubble = None
        self.log = []
        self.request = None
        self.handover = 0
        self.speech_mode = None
        self.accuse_target = None
        self.result = None
        self.buttons = []
        self.running = True
        self.paused = False
        self.speed = 0.5
        self.last_deaths = 0
        self.seat_centers = []
        self.names = []

    # ---------- 背景 ----------
    def draw_night(self):
        G.draw_night(self.screen, pygame.time.get_ticks() / 1000.0)
        # 桌面
        pygame.draw.ellipse(self.screen, (18, 44, 36),
                            pygame.Rect(180, 250, 920, 360))
        pygame.draw.ellipse(self.screen, (168, 132, 70),
                            pygame.Rect(180, 250, 920, 360), 4)
        pygame.draw.ellipse(self.screen, (26, 60, 48),
                            pygame.Rect(215, 278, 850, 300))

    def draw_day(self):
        G.draw_night(self.screen, pygame.time.get_ticks() / 1000.0)
        ov = pygame.Surface((G.W, G.H), pygame.SRCALPHA)
        ov.fill((220, 170, 90, 38))
        self.screen.blit(ov, (0, 0))
        pygame.draw.ellipse(self.screen, (24, 52, 40),
                            pygame.Rect(180, 250, 920, 360))
        pygame.draw.ellipse(self.screen, (196, 160, 92),
                            pygame.Rect(180, 250, 920, 360), 4)
        pygame.draw.ellipse(self.screen, (30, 66, 50),
                            pygame.Rect(215, 278, 850, 300))

    # ---------- 设置 ----------
    def setup(self):
        G.draw_night(self.screen, 0)
        G.text(self.screen, "狼 人 杀", 72, G.GOLD, (G.W // 2, 82), bold=True,
               align="center")
        G.text(self.screen, "七 个 人 · 两 只 狼 · 一 盏 灯", 22, G.CREAM,
               (G.W // 2, 160), align="center")
        G.text(self.screen, "你的名字", 18, G.MUTED, (G.W // 2 - 160, 368), bold=True)
        self.name_input.draw(self.screen)
        G.text(self.screen, "几个真人？", 22, G.CREAM, (G.W // 2, 466),
               align="center", bold=True)
        self.buttons = []
        for i in range(1, 7):
            r = pygame.Rect(G.W // 2 - 300 + (i - 1) * 100, 494, 84, 54)
            b = G.Button(r, str(i), "primary" if i == self.n_humans else "ghost", 24,
                         tag=("n", i))
            self.buttons.append(b)
            b.draw(self.screen)
        ai = G.Button((G.W // 2 - 170, 548, 340, 46), "纯 A I 旁 观", "ghost", 21,
                      tag="ai")
        self.buttons.append(ai)
        ai.draw(self.screen)
        # LRS_084：多发言档开着时在设置页可见；这里只在不默认的情况下加一行提示
        if self.speech_variant == "multi":
            G.text(self.screen, "★ 多发言实验档：开（设置里可关）", 16, G.GOLD_L,
                   (G.W // 2, 606), align="center")
        for i, (lab, tg) in enumerate((("玩家手册", "manual"), ("成就", "ach"),
                                       ("设置", "settings"), ("退出", "quit"))):
            b = G.Button((250 + i * 170, 636, 150, 44), lab, "ghost", 18, tag=tg)
            self.buttons.append(b)
            b.draw(self.screen)
        wins = int(self.profile.data.get("wins", 0))
        games = int(self.profile.data.get("games", 0))
        got = len(self.profile.data.get("achievements") or {})
        G.text(self.screen, "战绩：%d 胜 / %d 局 · 成就 %d/%d" % (
            wins, games, got, len(PF.ACH["werewolf"])), 18, G.CREAM,
            (G.W // 2, 690), align="center")
        self.music.draw(self.screen)

    def rules(self):
        G.draw_night(self.screen, 0)
        G.panel(self.screen, (190, 70, 900, 590), color=(14, 18, 20), alpha=238)
        G.text(self.screen, "规 则", 44, G.GOLD, (G.W // 2, 94), bold=True,
               align="center")
        lines = [
            "7 人：狼人 2、平民 2、女巫 1、预言家 1、猎人 1。",
            "夜晚顺序：狼人 → 女巫 → 预言家。",
            "女巫解药、毒药各一瓶，同夜只能用一瓶，不能救自己。",
            "白天按顺序发言，只有三种：沉默 / 我是 X / 我认为 X 是 Y。",
            "发言后投票，可以弃票；平票无人出局。",
            "猎人被刀或被票出可以开枪；被毒死不能开枪。",
            "胜负：狼人全灭好人胜；狼人存活数 ≥ 好人存活数 狼人胜。",
            "同机多人：每次行动前清屏，按提示把设备交给对应玩家。",
        ]
        for i, line in enumerate(lines):
            G.text(self.screen, line, 21, G.CREAM, (240, 170 + i * 42))
        b = G.Button((G.W // 2 - 80, 590, 160, 46), "返回", "primary", 20, tag="back")
        self.buttons = [b]
        b.draw(self.screen)
        self.music.draw(self.screen)

    def manual(self):
        G.draw_night(self.screen, 0)
        pages = [
            ("规则", [
                "7 人：狼人 2、平民 2、女巫 1、预言家 1、猎人 1。",
                "夜晚顺序：狼人 → 女巫 → 预言家；女巫解药/毒药各一瓶，同夜只用一瓶。",
                "白天按顺序发言，只有三种：沉默 / 我是 X / 我认为 X 是 Y。",
                "发言后投票，可以弃票；平票无人出局。",
                "猎人被刀或被票出可开枪；被毒死不能开枪。",
                "胜负：狼人全灭好人胜；狼人存活数 ≥ 好人存活数 狼人胜。",
            ]),
            ("角色", [
                "狼人：夜里选刀谁；可以冒充预言家、诬陷好人。",
                "平民：没有夜间技能，靠发言和投票找狼。",
                "女巫：有一瓶解药和一瓶毒药，救被刀的人或毒怀疑对象。",
                "预言家：每夜验一个人，结果只有自己知道。",
                "猎人：出局时可以开枪带走一个人，也可能带错。",
            ]),
            ("发言与投票", [
                "发言不是随便聊天，是三种动作：沉默、报身份、指认。",
                "沉默会被当成可疑；乱报身份会被真预言家盯上。",
                "投票可以弃票；平票时本轮无人出局，狼人白赚一晚。",
                "好人要尽量让票集中；狼人要搅浑水、跟风投好人。",
            ]),
            ("同机多人 / 记录", [
                "每个真人行动前会清屏，按提示把设备交给对应玩家。",
                "按“我准备好了”后才显示身份和私有信息，防上一位偷看。",
                "每局自动写一行到 对局记录/werewolf/日期.jsonl。",
                "不联网、不上传；昵称只留在本机。",
            ]),
        ]
        title, lines = pages[self.manual_page % len(pages)]
        G.text(self.screen, "玩 家 手 册", 46, G.GOLD, (G.W // 2, 42),
               bold=True, align="center")
        G.panel(self.screen, (180, 100, 920, 500), color=(14, 18, 20), alpha=240)
        G.text(self.screen, "%d / %d · %s" % (self.manual_page + 1, len(pages), title),
               24, G.GOLD_L, (220, 120), bold=True)
        for i, line in enumerate(lines):
            G.wrap_text(self.screen, line, 21, G.CREAM, (220, 176 + i * 66),
                        840, line_h=30)
        prev = G.Button((250, 620, 140, 44), "上一页", "ghost", 18, tag="man_prev")
        nxt = G.Button((G.W - 390, 620, 140, 44), "下一页", "ghost", 18, tag="man_next")
        back = G.Button((G.W // 2 - 80, 620, 160, 44), "返回", "primary", 18, tag="back")
        self.buttons = [prev, nxt, back]
        for b in self.buttons:
            b.draw(self.screen)

    def achievements(self):
        G.draw_night(self.screen, 0)
        G.text(self.screen, "成 就", 48, G.GOLD, (G.W // 2, 42), bold=True,
               align="center")
        got = self.profile.data.get("achievements") or {}
        G.text(self.screen, "已解锁 %d / %d" % (
            sum(1 for a in PF.ACH["werewolf"] if got.get(a[0])),
            len(PF.ACH["werewolf"])), 18, G.CREAM, (G.W // 2, 94), align="center")
        G.panel(self.screen, (150, 126, 980, 500), color=(14, 18, 20), alpha=240)
        for i, (aid, name, desc) in enumerate(PF.ACH["werewolf"]):
            col = i % 2
            row = i // 2
            x = 180 + col * 480
            y = 152 + row * 94
            on = bool(got.get(aid))
            G.text(self.screen, "✓" if on else "○", 22,
                   G.GOLD if on else (90, 82, 70), (x, y))
            G.text(self.screen, name if on else "???", 21, G.CREAM if on else G.MUTED,
                   (x + 34, y - 2), bold=on)
            G.text(self.screen, "已解锁" if on else "未解锁", 17,
                   G.GOLD_L if on else (90, 82, 70), (x + 34, y + 28))
        b = G.Button((G.W // 2 - 80, 650, 160, 44), "返回", "primary", 18, tag="back")
        self.buttons = [b]
        b.draw(self.screen)

    def settings(self):
        G.draw_night(self.screen, 0)
        G.text(self.screen, "设 置", 48, G.GOLD, (G.W // 2, 52), bold=True,
               align="center")
        G.panel(self.screen, (280, 100, 720, 540), color=(14, 18, 20), alpha=240)
        self.buttons = []
        y = 148
        G.text(self.screen, "音乐", 21, G.CREAM, (320, y))
        b = G.Button((850, y - 8, 120, 42), "开" if self.music.enabled else "关",
                     "primary" if self.music.enabled else "ghost", 19,
                     tag=("toggle", "music"))
        self.buttons.append(b); b.draw(self.screen)
        y += 64
        G.text(self.screen, "音量", 21, G.CREAM, (320, y))
        G.text(self.screen, "%d%%" % int(self.music.volume * 100), 20, G.GOLD_L,
               (790, y + 2))
        b = G.Button((850, y - 8, 52, 42), "−", "ghost", 22, tag=("vol", -1))
        self.buttons.append(b); b.draw(self.screen)
        b = G.Button((916, y - 8, 52, 42), "+", "ghost", 22, tag=("vol", 1))
        self.buttons.append(b); b.draw(self.screen)
        y += 64
        G.text(self.screen, "播放模式", 21, G.CREAM, (320, y))
        b = G.Button((850, y - 8, 120, 42),
                     "单曲循环" if self.music.mode == "single" else "列表切歌",
                     "primary", 17, tag="music_mode")
        self.buttons.append(b); b.draw(self.screen)
        y += 64
        G.text(self.screen, "当前曲目", 21, G.CREAM, (320, y))
        G.text(self.screen, self.music.name(), 18, G.GOLD_L, (470, y + 3))
        b = G.Button((850, y - 8, 120, 42), "下一首", "ghost", 17, tag="music_next")
        self.buttons.append(b); b.draw(self.screen)
        y += 64
        G.text(self.screen, "音效", 21, G.CREAM, (320, y))
        b = G.Button((850, y - 8, 120, 42), "开" if self.sfx.enabled else "关",
                     "primary" if self.sfx.enabled else "ghost", 19,
                     tag=("toggle", "sfx"))
        self.buttons.append(b); b.draw(self.screen)
        y += 64
        multi_on = (self.speech_variant == "multi")
        G.text(self.screen, "多发言实验档", 21, G.CREAM, (320, y))
        b = G.Button((850, y - 8, 120, 42), "开" if multi_on else "关",
                     "primary" if multi_on else "ghost", 19, tag=("toggle", "multi"))
        self.buttons.append(b); b.draw(self.screen)
        back = G.Button((G.W // 2 - 80, 596, 160, 46), "返回", "primary", 19, tag="back")
        self.buttons.append(back)
        back.draw(self.screen)

    def ai_profiles(self):
        G.draw_night(self.screen, 0)
        G.text(self.screen, "切 换 A I 对 手", 44, G.GOLD, (G.W // 2, 52),
               bold=True, align="center")
        cur = ("启发式" if self.ai_mode == "heuristic"
               else "研究模型：" + self.model_profile)
        G.text(self.screen, "当前：%s" % cur, 20, G.CREAM, (G.W // 2, 104),
               align="center")
        if not self.model_ok:
            G.text(self.screen, "研究模型档不可用：%s" % self.model_reason[:46],
                   16, G.RED_L, (G.W // 2, 132), align="center")
            G.text(self.screen, "启发式仍可玩；装好 torch 后重启即可。",
                   16, G.MUTED, (G.W // 2, 156), align="center")
        labels = {
            "heuristic": "启发式（默认，不加载模型）",
            "symmetric_q_only": "symmetric_q_only（推荐 · LRS_070）",
            "auto_hybrid": "auto_hybrid（未联合验证 · 仅供娱乐）",
            "good_three_piece": "good_three_piece（好人侧）",
            "q_seer_fair": "q_seer_fair（公平档）",
            "wolf_kit_experimental": "wolf_kit（已判死 · 仅实验）",
        }
        self.buttons = []
        order = ("heuristic", "symmetric_q_only", "auto_hybrid",
                 "good_three_piece", "q_seer_fair", "wolf_kit_experimental")
        for i, prof in enumerate(order):
            col, row = i % 2, i // 2
            x = 160 + col * 470
            y = 190 + row * 92
            enabled = (prof == "heuristic") or self.model_ok
            active = ((self.ai_mode == "heuristic" and prof == "heuristic")
                      or (self.ai_mode == "model" and self.model_profile == prof))
            b = G.Button((x, y, 430, 60), labels[prof],
                         "primary" if active else "ghost", 17,
                         enabled=enabled, tag=("ai_pick", prof))
            self.buttons.append(b)
            b.draw(self.screen)
        back = G.Button((G.W // 2 - 80, 596, 160, 46), "返回", "primary", 19,
                        tag="back")
        self.buttons.append(back)
        back.draw(self.screen)

    def ai_confirm(self):
        prof = self.pending_profile
        meta = AG.MODEL_META.get(prof) or {}
        G.draw_night(self.screen, 0)
        G.panel(self.screen, (260, 190, 760, 300), color=(14, 18, 20), alpha=242)
        G.text(self.screen, "确认切换 AI 对手", 38, G.GOLD, (G.W // 2, 220),
               bold=True, align="center")
        G.wrap_text(self.screen,
                    meta.get("warning") or "确定切换到这个档位？",
                    20, G.CREAM, (300, 290), 680, line_h=32)
        G.text(self.screen, "档位：%s" % prof, 18, G.GOLD_L, (300, 390))
        self.buttons = []
        yes = G.Button((G.W // 2 - 170, 470, 150, 46), "确认", "primary", 19,
                       tag=("ai_yes",))
        no = G.Button((G.W // 2 + 20, 470, 150, 46), "取消", "ghost", 19,
                      tag=("ai_no",))
        self.buttons = [yes, no]
        for b in self.buttons:
            b.draw(self.screen)

    def on_game_over(self, rec):
        r = rec["result"]
        winner = r["winner"]
        roles = r["reveal_after_game"]["roles"]
        human_roles = [roles[str(i)] for i in range(self.n_humans)]
        won = any((winner == "wolf" and role == "WOLF") or
                  (winner == "good" and role != "WOLF") for role in human_roles)
        names = self.profile.record_game(won=won, ai_only=(self.n_humans == 0))
        for a in rec["actions"]:
            if a["seat"] >= self.n_humans:
                continue
            if a["phase"] == "night_seer" and a["code"] >= 81:
                tgt = a["code"] - 81
                if roles.get(str(tgt)) == "WOLF":
                    nm = self.profile.unlock("seer_wolf")
                    if nm:
                        names.append(nm)
            if a["phase"] == "night_witch" and a["code"] == 71:
                nm = self.profile.unlock("witch_save")
                if nm:
                    names.append(nm)
            if a["phase"] == "hunter_shot" and a["code"] >= 91:
                tgt = a["code"] - 91
                if roles.get(str(tgt)) == "WOLF":
                    nm = self.profile.unlock("hunter_wolf")
                    if nm:
                        names.append(nm)
        for nm in names:
            self.toast.show("成就解锁：" + nm, G.GOLD, 3200)

    def make_names(self):
        base = self.name_input.value.strip() or "你"
        self.names = [base] + ["玩家%d" % (i + 1) for i in range(1, self.n_humans)]

    def start_game(self):
        self.make_names()
        self.paused = False
        self.speed = 0.5
        self.session = WolfSession(self.n_humans, self.names,
                                   ai_mode=self.ai_mode,
                                   model_profile=self.model_profile,
                                   speech_mode=self.speech_variant)
        self.session.speed = self.speed
        self.view = None
        self.thinking = None
        self.bubble = None
        self.log = []
        self.request = None
        self.result = None
        self.last_deaths = 0
        self.state = "game"
        self.session.start()

    def ai_game(self):
        if self.speech_variant == "multi" and self.ai_mode == "model":
            self.ai_mode = "heuristic"
            self.model_profile = "symmetric_q_only"
            self.toast.show("多发言档只支持启发式：已切回启发式", G.RED_L, 4200)
        self.paused = False
        self.speed = 0.5
        self.session = WolfSession(0, [], ai_mode=self.ai_mode,
                                   model_profile=self.model_profile,
                                   speech_mode=self.speech_variant)
        self.session.speed = self.speed
        self.view = None
        self.thinking = None
        self.bubble = None
        self.log = []
        self.request = None
        self.result = None
        self.last_deaths = 0
        self.state = "game"
        self.session.start()

    def name_of(self, pid):
        if pid < self.n_humans and pid < len(self.names):
            return self.names[pid]
        return AI_NAMES[(pid - self.n_humans) % len(AI_NAMES)]

    # ---------- 游戏 ----------
    def seat_positions(self):
        cx, cy, rx, ry = G.W // 2, 318, 430, 188
        out = []
        for i in range(7):
            ang = math.radians(90 + i * 360 / 7)
            out.append((int(cx + rx * math.cos(ang)),
                        int(cy + ry * math.sin(ang))))
        return out

    def draw_seats(self):
        self.seat_centers = self.seat_positions()
        alive = set(self.view["alive"]) if self.view else set(range(7))
        for pid, (x, y) in enumerate(self.seat_centers):
            human = pid < self.n_humans
            portrait = HUMAN_PORTRAIT if human else PORTRAITS[pid % len(PORTRAITS)]
            dead = pid not in alive
            speaking = self.bubble and self.bubble[0] == pid
            ring = G.GOLD if speaking else (G.CREAM if human else (110, 130, 150))
            G.draw_avatar(self.screen, portrait, (x, y), 36, gray=dead, ring=ring)
            nm = self.name_of(pid)
            G.text(self.screen, "%d·%s" % (pid, nm), 18,
                   G.MUTED if dead else G.CREAM, (x, y + 42), align="center",
                   bold=human)
            if dead:
                G.text(self.screen, "出局", 16, G.RED_L, (x, y + 64),
                       align="center", bold=True)
            if self.thinking == pid:
                G.text(self.screen, "…", 22, G.GOLD, (x + 30, y - 42))

    def draw_center(self):
        G.text(self.screen, "第 %d 天 · %s" % (
            self.view["day"] if self.view else self.day,
            PHASE_CN.get(self.view["phase"] if self.view else self.phase,
                         self.phase)), 26, G.GOLD, (G.W // 2, 258),
            bold=True, align="center")
        if self.bubble:
            pid, txt, until = self.bubble
            if pygame.time.get_ticks() < until:
                G.panel(self.screen, (G.W // 2 - 230, 292, 460, 76),
                        color=(14, 18, 20), alpha=235, border=G.GOLD, radius=14)
                G.text(self.screen, txt, 20, G.CREAM, (G.W // 2, 318),
                       align="center", bold=True)
        # 底部日志
        G.panel(self.screen, (30, 620, G.W - 60, 78), color=(10, 12, 14),
                alpha=210)
        for i, line in enumerate(self.log[-2:]):
            G.text(self.screen, line, 17, G.CREAM, (48, 634 + i * 26))

    def draw_game(self):
        self.buttons = []
        if self.view and self.view["phase"].startswith("night"):
            self.draw_night()
        else:
            self.draw_day()
        pygame.draw.rect(self.screen, (10, 12, 14), (0, 0, G.W, 48))
        G.text(self.screen, "狼人杀", 24, G.GOLD, (24, 10), bold=True)
        G.text(self.screen, "同机多人 · 身份隐藏" if self.n_humans else "纯 AI 旁观",
               17, G.MUTED, (150, 16))
        self.music.draw(self.screen)
        self.draw_seats()
        self.draw_center()
        if self.result:
            self.draw_result()
        if self.state == "game" and not self.request and self.n_humans == 0:
            pb = G.Button((G.W - 300, 658, 120, 34),
                          "继续" if self.paused else "暂停", "ghost", 15, tag="pause")
            sp = G.Button((G.W - 166, 658, 120, 34),
                          "速度 %gx" % self.speed, "ghost", 15, tag="speed")
            self.buttons = [pb, sp]
            for b in self.buttons:
                b.draw(self.screen)
        if self.request:
            self.draw_request()
        if not self.result:
            qb = G.Button((G.W - 330, 8, 130, 32), "退出本局", "ghost", 15,
                          tag="quit_game")
            self.buttons.append(qb)
            qb.draw(self.screen)
        self.toast.draw(self.screen)

    # ---------- 人类面板 ----------
    def public_lines(self, view, n=7):
        pub = view.get("public") or {}
        items = []
        for s in pub.get("speeches") or []:
            items.append(("s", s))
        for v in pub.get("votes") or []:
            items.append(("v", v))
        for d in pub.get("deaths") or []:
            items.append(("d", d))
        items.sort(key=lambda x: (x[1].get("day", 0), x[0]))
        out = []
        for kind, x in items[-n:]:
            if kind == "s":
                nm = self.name_of(x["pid"])
                out.append("%s：%s" % (nm, A.describe(x["action"])))
            elif kind == "v":
                nm = self.name_of(x["voter"])
                tgt = "弃票" if x["target"] is None else self.name_of(x["target"])
                out.append("%s → %s" % (nm, tgt))
            else:
                nm = self.name_of(x["pid"])
                cause = {"WOLF_KILL": "被刀", "POISON": "被毒", "VOTE": "被票出",
                         "SHOT": "被枪杀"}.get(x.get("cause"), x.get("cause"))
                out.append("%s %s" % (nm, cause))
        return out

    def draw_request(self):
        req = self.request
        if not req:
            return
        view = req["view"]
        pid = req["pid"]
        nm = self.names[pid] if pid < len(self.names) else "玩家"
        ov = pygame.Surface((G.W, G.H), pygame.SRCALPHA)
        ov.fill((3, 5, 8, 238))
        self.screen.blit(ov, (0, 0))
        if self.handover == 0:
            G.panel(self.screen, (G.W // 2 - 260, 210, 520, 290),
                    color=(18, 22, 24), alpha=245, border=G.GOLD, border_w=3)
            G.text(self.screen, "请把设备交给", 24, G.MUTED, (G.W // 2, 244),
                   align="center")
            G.text(self.screen, nm, 44, G.GOLD, (G.W // 2, 284), bold=True,
                   align="center")
            G.text(self.screen, "其他人请回避屏幕", 18, G.CREAM,
                   (G.W // 2, 342), align="center")
            b = G.Button((G.W // 2 - 120, 390, 240, 52), "我 准 备 好 了",
                         "primary", 22, tag="ready")
            self.buttons = [b]
            b.draw(self.screen)
            return
        G.panel(self.screen, (G.W // 2 - 450, 54, 900, 610),
                color=(15, 19, 21), alpha=248, border=G.GOLD, border_w=3)
        role = view["my_role"]
        G.text(self.screen, "你的身份", 20, G.MUTED, (G.W // 2 - 420, 74))
        G.text(self.screen, "%s（%s）" % (role_cn(role), faction_cn(view["my_faction"])),
               34, role_color(role), (G.W // 2 - 420, 96), bold=True)
        extras = []
        if view.get("teammates"):
            extras.append("狼同伴：" + "、".join("%d号" % p for p in view["teammates"]))
        if role == "SEER":
            res = view.get("seer_results") or {}
            if res:
                extras.append("验人：" + "；".join("%s号=%s" % (t, "狼" if w else "好人")
                                                   for t, w in sorted(res.items())))
        if role == "WITCH":
            w = view.get("witch") or {}
            extras.append("药：解药%s / 毒药%s；今夜被刀：%s" % (
                "有" if w.get("has_antidote") else "无",
                "有" if w.get("has_poison") else "无",
                ("%d号" % w["night_victim"]) if w.get("night_victim") is not None else "无人"))
        for i, e in enumerate(extras):
            G.text(self.screen, e, 18, G.CREAM, (G.W // 2 - 420, 140 + i * 26))
        G.text(self.screen, "公开信息", 18, G.GOLD, (G.W // 2 + 80, 74), bold=True)
        for i, line in enumerate(self.public_lines(view, 8)):
            G.text(self.screen, line, 17, G.CREAM, (G.W // 2 + 80, 100 + i * 26))
        # 动作区
        self.buttons = []
        ph = view["phase"]
        legal = req["legal"]
        self.action_buttons(view, legal, ph)
        # 公共按钮
        if self.speech_mode or self.accuse_target is not None:
            b = G.Button((G.W // 2 - 420, 588, 120, 40), "取消", "ghost", 17,
                         tag="cancel_speech")
            self.buttons.append(b)
            b.draw(self.screen)

    def add_button(self, rect, label, tag, kind="ghost", size=17, enabled=True):
        b = G.Button(rect, label, kind, size, enabled=enabled, tag=tag)
        self.buttons.append(b)
        b.draw(self.screen)
        return b

    def target_buttons(self, view, legal, typ, tag_prefix, cols=4, y=300):
        targets = [a["target"] for a in legal if a["type"] == typ and a.get("target", -1) >= 0]
        for i, t in enumerate(targets):
            nm = self.names[t] if t < len(self.names) else AI_NAMES[t % len(AI_NAMES)]
            r = pygame.Rect(G.W // 2 - 420 + (i % cols) * 200,
                            y + (i // cols) * 52, 180, 40)
            self.add_button(r, "%d·%s" % (t, nm), (tag_prefix, t))

    def multi_speech_buttons(self, view, legal):
        """LRS_084：多发言档的人类发言界面（自报 → 多条指认 → 结束/撤销）。"""
        x, y = G.W // 2 - 420, 250
        seq = self.multi_seq
        n = len(seq)

        def has(a):
            return any(sb.get("type") == a.get("type")
                       and sb.get("target") == a.get("target")
                       and sb.get("role") == a.get("role") for sb in seq)

        # 已攒的序列（点"撤销"可退最后一条）
        G.text(self.screen, "本次发言（最多 3 条，%d/3）" % n, 18, G.GOLD_L,
               (x, y - 34), bold=True)
        if not seq:
            G.text(self.screen, "（还没有内容；直接点「结束发言」就是沉默）", 16,
                   G.MUTED, (x, y - 10))
        for i, a in enumerate(seq):
            G.text(self.screen, "  %d. %s" % (i + 1, A.describe(a)), 17, G.CREAM,
                   (x, y - 8 + i * 24))

        by = y + 74
        # 1) 自报（最多一条，且必须在指认之前）
        if not any(sb.get("type") == A.SELF_CLAIM for sb in seq) and n < 3:
            if self.speech_mode == "claim":
                for i, role in enumerate(claim_role_order(view)):
                    self.add_button((x + (i % 3) * 190, by + (i // 3) * 52, 170, 42),
                                    "我是" + role_cn(role), ("m_claim", role))
            else:
                self.add_button((x, by, 180, 42), "我是 X（自报）",
                                ("m_claim_menu",), "ghost")
                if n:
                    self.add_button((x + 200, by, 180, 42), "继续指认 ↓",
                                    ("m_accuse_menu",), "ghost")
        elif n < 3:
            # 2) 指认（可多条，不重复目标）
            if self.accuse_target is None:
                targets = [a["target"] for a in legal
                           if a["type"] == A.ACCUSE and a.get("target", -1) >= 0]
                done = {sb.get("target") for sb in seq if sb.get("type") == A.ACCUSE}
                i2 = 0
                for t in targets:
                    if t in done:
                        continue
                    nm = self.names[t] if t < len(self.names) else AI_NAMES[t % len(AI_NAMES)]
                    r = pygame.Rect(x + (i2 % 4) * 200, by + (i2 // 4) * 52, 180, 40)
                    self.add_button(r, "%d·%s" % (t, nm), ("m_accuse_target", t))
                    i2 += 1
                G.text(self.screen, "选一个人 → 再选「你认为他是谁」", 15, G.MUTED,
                       (x, by - 24))
            else:
                for i, role in enumerate(ROLE_ORDER):
                    self.add_button((x + (i % 3) * 190, by + (i // 3) * 52, 170, 42),
                                    "他是" + role_cn(role),
                                    ("m_accuse_role", self.accuse_target, role))
        else:
            G.text(self.screen, "已达 3 条上限（1 自报 + 2 指认）", 16, G.GOLD_L,
                   (x, by))

        # 3) 结束 / 撤销
        cy = by + 140
        self.add_button((x, cy, 180, 44), "结束发言" if n else "沉默（结束）",
                        ("m_finish",), "primary")
        if n:
            self.add_button((x + 200, cy, 150, 44), "撤销最后一条",
                            ("m_undo",), "ghost")
        self.add_button((x + 370, cy, 180, 44), "清空", ("m_clear",), "ghost")
        G.text(self.screen, "提示：说完点「结束发言」，环境会按顺序逐条执行", 15,
               G.MUTED, (x, cy + 52))

    def action_buttons(self, view, legal, ph):
        x, y = G.W // 2 - 420, 250
        if ph == "day_speech" and self.speech_variant == "multi":
            self.multi_speech_buttons(view, legal)
        elif ph == "day_speech":
            if self.speech_mode == "claim":
                for i, role in enumerate(claim_role_order(view)):
                    self.add_button((x + (i % 3) * 190, y + (i // 3) * 54, 170, 42),
                                    "我是" + role_cn(role), ("claim", role))
            elif self.speech_mode == "accuse":
                if self.accuse_target is None:
                    self.target_buttons(view, legal, A.VOTE if False else A.ACCUSE,
                                        "accuse_target", y=300)
                else:
                    for i, role in enumerate(ROLE_ORDER):
                        self.add_button((x + (i % 3) * 190, y + (i // 3) * 54, 170, 42),
                                        "他是" + role_cn(role), ("accuse_role", role))
            else:
                self.add_button((x, y, 180, 44), "沉默", ("silence",), "ghost")
                self.add_button((x + 200, y, 180, 44), "我是 X", ("claim_menu",), "ghost")
                self.add_button((x + 400, y, 210, 44), "我认为 X 是 Y", ("accuse_menu",), "ghost")
        elif ph == "day_vote":
            self.target_buttons(view, legal, A.VOTE, "vote", cols=4, y=300)
            self.add_button((x, 480, 180, 42), "弃票", ("abstain",), "ghost")
        elif ph == "night_wolf":
            self.target_buttons(view, legal, A.KILL, "kill", cols=4, y=310)
        elif ph == "night_witch":
            has_save = any(a["type"] == A.WITCH and a["potion"] == A.POTION_SAVE for a in legal)
            has_poison = any(a["type"] == A.WITCH and a["potion"] == A.POTION_POISON for a in legal)
            self.add_button((x, y, 180, 42), "不用药", ("witch_none",), "ghost")
            if has_save:
                self.add_button((x + 200, y, 180, 42), "用解药救人", ("witch_save",), "primary")
            if has_poison:
                self.add_button((x + 400, y, 180, 42), "用毒药", ("witch_poison_menu",), "danger")
            if self.speech_mode == "poison":
                self.target_buttons(view, legal, A.WITCH, "poison", cols=4, y=330)
        elif ph == "night_seer":
            self.target_buttons(view, legal, A.CHECK, "check", cols=4, y=310)
        elif ph == "hunter_shot":
            self.target_buttons(view, legal, A.SHOOT, "shoot", cols=4, y=310)
            self.add_button((x, 480, 180, 42), "不开枪", ("no_shoot",), "ghost")
        else:
            for i, a in enumerate(legal[:12]):
                self.add_button((x + (i % 3) * 220, y + (i // 3) * 50, 200, 40),
                                A.describe(a), ("legal", i))

    # ---------- 结果 ----------
    def draw_result(self):
        r = self.result["result"]
        ov = pygame.Surface((G.W, G.H), pygame.SRCALPHA)
        ov.fill((3, 5, 8, 215))
        self.screen.blit(ov, (0, 0))
        G.panel(self.screen, (G.W // 2 - 340, 96, 680, 540),
                color=(18, 22, 24), alpha=248, border=G.GOLD, border_w=3)
        G.text(self.screen, "%s 胜" % r["winner_cn"], 44, G.GOLD,
               (G.W // 2, 122), bold=True, align="center")
        G.text(self.screen, r["reason"], 20, G.CREAM, (G.W // 2, 176),
               align="center")
        roles = r["reveal_after_game"]["roles"]
        for i in range(7):
            role = roles[str(i)]
            nm = self.name_of(i)
            G.text(self.screen, "%d·%s：%s" % (i, nm, role_cn(role)), 19,
                   role_color(role), (G.W // 2 - 300, 224 + i * 32))
        again = G.Button((G.W // 2 - 190, 500, 170, 46), "再 来 一 局", "primary", 19, tag="again")
        back = G.Button((G.W // 2 + 20, 500, 170, 46), "返 回", "ghost", 19, tag="menu")
        self.buttons = [again, back]
        again.draw(self.screen); back.draw(self.screen)

    # ---------- 队列 ----------
    def pump(self):
        if not self.session:
            return
        while self.session:
            try:
                item = self.session.q.get_nowait()
            except queue.Empty:
                break
            kind = item[0]
            a = item[1] if len(item) > 1 else None
            b = item[2] if len(item) > 2 else None
            c = item[3] if len(item) > 3 else None
            if kind == "state":
                view = b
                self.view = view
                self.day = view["day"]
                self.phase = view["phase"]
                deaths = (view.get("public") or {}).get("deaths") or []
                if len(deaths) > self.last_deaths:
                    for d in deaths[self.last_deaths:]:
                        nm = self.name_of(d["pid"])
                        self.toast.show("%s 出局" % nm, G.RED_L, 2200)
                    self.last_deaths = len(deaths)
            elif kind == "thinking":
                self.thinking = a
            elif kind == "human_request":
                self.request = {"pid": a, "view": b, "legal": c}
                self.handover = 0
                self.speech_mode = None
                self.accuse_target = None
            elif kind == "human_ready":
                # LRS_084：multi 档——本座现在要决策了。
                # ★ 必须用**本次事件自带的 view/legal**，不能沿用旧 request，
                #   否则 phase 会一直是上一次的（表现为"只看到一个阶段"）。
                self.request = {"pid": a, "view": b, "legal": c}
                self.handover = 1
                self.speech_mode = None
                self.accuse_target = None
                self.multi_seq = []
                # 自增序号：外部脚本/测试用它判定"这是新的一次轮到我"
                self._req_seq = getattr(self, "_req_seq", 0) + 1
            elif kind == "action":
                pid, view, act = a, b, c
                self.thinking = None
                nm = self.name_of(pid)
                if view["phase"] == "day_speech":
                    self.bubble = (pid, "%s：%s" % (nm, A.describe(act)),
                                   pygame.time.get_ticks() + 3500)
                    self.log.append("%s：%s" % (nm, A.describe(act)))
                elif view["phase"] == "day_vote":
                    tgt = "弃票" if act.get("target", -1) < 0 else "%d号" % act["target"]
                    self.bubble = (pid, "%s：投 %s" % (nm, tgt),
                                   pygame.time.get_ticks() + 2600)
                    self.log.append("%s：投 %s" % (nm, tgt))
                elif view["phase"] == "hunter_shot":
                    self.bubble = (pid, "%s：%s" % (nm, A.describe(act)),
                                   pygame.time.get_ticks() + 3000)
                    self.log.append("%s：%s" % (nm, A.describe(act)))
                else:
                    self.log.append("%s：夜间行动" % nm)
                if view["phase"] == "day_speech" or view["phase"] == "day_vote":
                    self.sfx.play("click")
            elif kind == "game_over":
                self.on_game_over(a)
                self.result = a
                self.state = "result"
                self.request = None
                self.sfx.play("win")
                self.session = None
            elif kind == "error":
                self.toast.show("出错：" + a, G.RED_L, 5000)
                self.session = None

    # ---------- 事件 ----------
    def handle(self, event):
        if event.type == pygame.QUIT:
            if self.state == "game" and self.session:
                self.session.abort()
                self.session = None
            self.running = False
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if self.state == "game" and self.session:
                self.quit_game()
            elif self.state in ("game", "result", "manual", "ach", "settings", "ai", "ai_confirm"):
                self.state = "setup"
                self.session = None
            else:
                self.running = False
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.state == "setup" and self.moon_rect.collidepoint(event.pos):
                nm = self.profile.click_title()
                self.sfx.play("click")
                if nm:
                    self.toast.show("成就解锁：" + nm, G.GOLD, 3200)
                return
        for b in self.buttons:
            if b.handle(event):
                self.click(b.tag)
                return
        if self.state == "setup":
            self.name_input.handle(event)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if pygame.Rect(G.W - 170, 8, 160, 40).collidepoint(event.pos):
                self.music.toggle()
                self.profile.set_setting("music", self.music.enabled)

    def quit_game(self):
        """中途退出：先让会话把已发生的步骤写成未完成记录，再回设置页。"""
        if self.session:
            self.session.abort()
            self.session = None
        self.request = None
        self.handover = None
        self.result = None
        self.view = None
        self.state = "setup"
        self.toast.show("本局已保存为未完成", G.GOLD, 2800)

    def click(self, tag):
        self.sfx.play("click")
        if tag == "quit":
            self.running = False
        elif tag == "quit_game":
            self.quit_game()
        elif isinstance(tag, tuple) and tag[0] == "n":
            self.n_humans = tag[1]
            self.start_game()
        elif tag == "ai":
            self.ai_game()
        elif tag == "manual":
            self.state = "manual"
            self.manual_page = 0
        elif tag == "ach":
            self.state = "ach"
        elif tag == "settings":
            self.state = "settings"
        elif tag == "ai_profile":
            self.state = "ai"
        elif isinstance(tag, tuple) and tag[0] == "ai_pick":
            prof = tag[1]
            if prof == "heuristic":
                self.ai_mode = "heuristic"
                self.model_profile = "symmetric_q_only"
                self.state = "setup"
                self.toast.show("AI 对手：启发式", G.GOLD, 1800)
            elif not self.model_ok:
                self.toast.show("模型档不可用：%s" % self.model_reason, G.RED_L, 4200)
            elif AG.MODEL_META[prof].get("warning"):
                self.pending_profile = prof
                self.state = "ai_confirm"
            else:
                self.ai_mode = "model"
                self.model_profile = prof
                self.state = "setup"
                self.toast.show("AI 对手：研究模型 / %s" % prof, G.GOLD, 2200)
        elif tag == ("ai_yes",):
            prof = self.pending_profile
            if prof in AG.MODEL_META and self.model_ok:
                self.ai_mode = "model"
                self.model_profile = prof
                self.state = "setup"
                self.toast.show("AI 对手：研究模型 / %s" % prof, G.GOLD, 2200)
            else:
                self.state = "ai"
            self.pending_profile = None
        elif tag == ("ai_no",):
            self.pending_profile = None
            self.state = "ai"
        elif tag == "man_prev":
            self.manual_page = (self.manual_page - 1) % 4
        elif tag == "man_next":
            self.manual_page = (self.manual_page + 1) % 4
        elif isinstance(tag, tuple) and tag[0] == "toggle":
            key = tag[1]
            if key == "music":
                self.music.toggle()
                self.profile.set_setting("music", self.music.enabled)
            elif key == "sfx":
                self.sfx.enabled = not self.sfx.enabled
                self.profile.set_setting("sfx", self.sfx.enabled)
            elif key == "multi":
                self.speech_variant = ("single" if self.speech_variant == "multi"
                                    else "multi")
                if self.speech_variant == "multi" and self.ai_mode == "model":
                    # 研究模型未适配多发言：强制退回启发式
                    self.ai_mode = "heuristic"
                    self.model_profile = "symmetric_q_only"
                    self.toast.show("多发言档只支持启发式：已切回启发式", G.RED_L, 4200)
                else:
                    self.toast.show(
                        "多发言实验档：开" if self.speech_variant == "multi"
                        else "多发言实验档：关（默认三选一）", G.GOLD, 2000)
        elif tag == "music_next":
            self.music.next_track()
            self.profile.set_setting_raw("music_track", self.music.index)
        elif tag == "music_mode":
            self.music.set_mode("single" if self.music.mode == "list" else "list")
            self.profile.set_setting_raw("music_mode", self.music.mode)
        elif isinstance(tag, tuple) and tag[0] == "vol":
            self.music.set_volume(self.music.volume + 0.05 * int(tag[1]))
            self.profile.set_setting_raw("music_volume", round(self.music.volume, 3))
        elif tag == "back":
            self.state = "setup"
        elif tag == "ready":
            self.handover = 1
            # LRS_084：multi 档下人类整场由 HumanSeqAgent 的闸门驱动，
            # 光清遮罩不够，必须同时放行，否则后台线程永远卡在 gate_ev.wait()。
            if (self.session is not None
                    and getattr(self.session, "speech_mode", "single") == "multi"
                    and self.request is not None):
                sa = getattr(self.session, "_suspend", {}).get(self.request["pid"])
                if sa is not None and sa.gate_ev is not None:
                    sa.gate_ev.set()
        elif tag == "pause":
            self.paused = not self.paused
            if self.session:
                self.session.set_paused(self.paused)
        elif tag == "speed":
            self.speed = {0.25: 0.5, 0.5: 1.0, 1.0: 2.0, 2.0: 4.0,
                          4.0: 8.0, 8.0: 0.25}.get(self.speed, 0.5)
            if self.session:
                self.session.speed = self.speed
        elif tag == "again":
            self.start_game()
        elif tag == "menu":
            self.state = "setup"
            self.session = None
        elif not self.request:
            return
        elif tag == "cancel_speech":
            self.speech_mode = None
            self.accuse_target = None
        elif tag == "cancel_request":
            # 人类必须行动；这里只用于清理菜单状态
            self.speech_mode = None
            self.accuse_target = None
        else:
            self.act_click(tag)

    def multi_act(self, tag, view, legal, send):
        """LRS_084 多发言档：攒序列 → 点"结束发言"一次性提交给环境逐条执行。"""
        pid = view["pid"]
        alive = view["alive"]

        def toast(msg):
            self.toast.show(msg, G.RED_L, 2400)

        def push(a):
            ok, msg, seq = EM.validate_sequence(pid, alive, self.multi_seq + [a])
            if not ok:
                toast(msg)
                return
            # 与"本人今天还剩几条"对齐：超出上限直接挡
            if len(self.multi_seq) >= EM.MAX_SPEECH:
                toast("每天最多 %d 条" % EM.MAX_SPEECH)
                return
            self.multi_seq = seq
            self.speech_mode = None
            self.accuse_target = None

        if tag == ("m_claim_menu",):
            self.speech_mode = "claim"
        elif tag == ("m_accuse_menu",):
            self.speech_mode = "accuse"
            self.accuse_target = None
        elif isinstance(tag, tuple) and tag[0] == "m_claim":
            push(A.self_claim(tag[1]))
        elif isinstance(tag, tuple) and tag[0] == "m_accuse_target":
            self.accuse_target = tag[1]
        elif isinstance(tag, tuple) and tag[0] == "m_accuse_role":
            if self.accuse_target is not None:
                push(A.accuse(self.accuse_target, tag[1]))
        elif tag == ("m_undo",):
            if self.multi_seq:
                self.multi_seq.pop()
            self.speech_mode = None
            self.accuse_target = None
        elif tag == ("m_clear",):
            self.multi_seq = []
            self.speech_mode = None
            self.accuse_target = None
        elif tag == ("m_finish",):
            seq = list(self.multi_seq)
            # 交给会话：Session 已经在跑，它的 SuspendAgent 队列在 _run 里一次性填好，
            # 所以这里**不**逐条 respond，而是把整段塞进"下一局同座位"的通道是不对的。
            # ⇒ 正确做法见 _multi_finish：通过 session.multi_submit 把队列续上。
            self.multi_finish(seq, view, legal, send)

    def multi_finish(self, seq, view, legal, send):
        """把人类攒好的整段发言交给环境。

        ★ 机制说明（**这条是卡面重点**）：环境那边不是"弹个气泡"，
          而是把这段序列逐条真的喂给 `MultiSpeechEnv._ask`（每条都建 view、进 public.speeches、
          进记录）。实现方式：人类这一座在 multi 档下由 `SuspendAgent` 驱动，
          `act()` 每被调一次弹一条；队列空了就返回 SILENCE（= 结束发言）。
          这里做的事就是"把队列填上，然后放行一次 act"。
        """
        sa = getattr(self.session, "_suspend", {}).get(view["pid"])
        if sa is None:
            # 兜底：没有挂 SuspendAgent（例如这套 session 不是 multi 档）⇒ 退回单动作
            a = seq[0] if seq else A.silence()
            send(a)
            self.multi_seq = []
            return
        sa.submit_sequence(seq or [])     # [] ⇒ 队列空 ⇒ 环境收到 SILENCE（沉默）
        self.multi_seq = []
        self.speech_mode = None
        self.accuse_target = None
        # ★ 放行闸门：环境从 `_ask` 的等待里出来，逐条执行队列里的发言
        if sa.gate_ev is not None:
            sa.gate_ev.set()
        self.request = None
        self.handover = 0

    def act_click(self, tag):
        view = self.request["view"]
        legal = self.request["legal"]
        ph = view["phase"]
        def send(a):
            if a not in legal:
                self.toast.show("这个动作不合法", G.RED_L)
                return
            if (self.session is not None
                    and getattr(self.session, "speech_mode", "single") == "multi"
                    and self.request is not None):
                # multi 档：人类的决策走闸门
                sa = getattr(self.session, "_suspend", {}).get(self.request["pid"])
                if sa is not None and sa.gate_ev is not None:
                    sa.gate_respond(a)
                    self.request = None
                    self.handover = 1
                    self.speech_mode = None
                    self.accuse_target = None
                    return
            self.session.respond(a)
            self.request = None
            self.handover = 0
            self.speech_mode = None
            self.accuse_target = None

        # ---- LRS_084：多发言档的发言交互（只在这一档生效，默认路径不动） ----
        if ph == "day_speech" and self.session is not None \
                and getattr(self.session, "speech_mode", "single") == "multi":
            self.multi_act(tag, view, legal, send)
            return
        if tag == ("silence",):
            send(A.silence())
        elif tag == ("claim_menu",):
            self.speech_mode = "claim"
        elif tag == ("accuse_menu",):
            self.speech_mode = "accuse"
            self.accuse_target = None
        elif isinstance(tag, tuple) and tag[0] == "claim":
            send(A.self_claim(tag[1]))
        elif isinstance(tag, tuple) and tag[0] == "accuse_target":
            self.accuse_target = tag[1]
        elif isinstance(tag, tuple) and tag[0] == "accuse_role":
            send(A.accuse(self.accuse_target, tag[1]))
        elif isinstance(tag, tuple) and tag[0] == "vote":
            send(A.vote(tag[1]))
        elif tag == ("abstain",):
            send(A.abstain())
        elif isinstance(tag, tuple) and tag[0] == "kill":
            send(A.kill(tag[1]))
        elif tag == ("witch_none",):
            send(A.witch_none())
        elif tag == ("witch_save",):
            send(A.witch_save())
        elif tag == ("witch_poison_menu",):
            self.speech_mode = "poison"
        elif isinstance(tag, tuple) and tag[0] == "poison":
            send(A.witch_poison(tag[1]))
        elif isinstance(tag, tuple) and tag[0] == "check":
            send(A.check(tag[1]))
        elif isinstance(tag, tuple) and tag[0] == "shoot":
            send(A.shoot(tag[1]))
        elif tag == ("no_shoot",):
            send(A.no_shoot())
        elif isinstance(tag, tuple) and tag[0] == "legal":
            send(legal[tag[1]])

    def draw(self):
        if self.state == "setup":
            self.setup()
        elif self.state == "manual":
            self.manual()
        elif self.state == "ach":
            self.achievements()
        elif self.state == "settings":
            self.settings()
        elif self.state == "ai":
            self.ai_profiles()
        elif self.state == "ai_confirm":
            self.ai_confirm()
        elif self.state == "game":
            self.draw_game()
        elif self.state == "result":
            self.draw_game()
        self.toast.draw(self.screen)

    def run(self):
        while self.running:
            for e in pygame.event.get():
                self.handle(e)
            self.pump()
            self.music.tick()
            self.draw()
            pygame.display.flip()
            self.clock.tick(60)
        pygame.quit()


def selftest(record_root, n_games=3, speech_mode="single", per_game_timeout=90):
    """GUI 侧自测：跑 n_games 局并写记录。

    ★ `speech_mode` 透传（LRS_091）：以前 `--selftest` 不读 `--speech-mode`，
      导致「用 exe 跑一局 multi」没有路径可走。

    ★ 每局超时保护（LRS_091 加，**LRS_092 更正了它的理由**）：
      LRS_091 当时写的是「multi 第 3 局偶发卡住」，**那个判断是错的**。
      LRS_092 实测证明：不存在卡死。真实情况是——
        ① 每局墙钟 ≈ 步数 × 0.9 秒（比值 1.008~1.013，误差<1.3%），完全按设计速度跑；
        ② 加速模式（speed=100）下 3 轮 × 10 局 = 30 局全部通过，各局 0.28~0.65 秒；
        ③ 当时看到「卡住」，是**测试命令超时把进程杀掉**，日志停在半路，看起来像卡死。
      超时保护仍然保留，但用途变了：**防止命令行被外部杀掉时误判**，
      以及兜住未来可能出现的新死锁——不是「掩盖某个已知 bug」。
      超时会如实打 `[selftest] game N TIMEOUT`，不假装跑完。
    """
    global RECORD_ROOT
    RECORD_ROOT = Path(record_root)
    t_all = time.time()
    results = []
    for i in range(n_games):
        t0 = time.time()
        seed = 2026111301 if i == 0 else 2026111302 + i
        print("[selftest] game %d/%d seed=%d speech_mode=%s ..."
              % (i + 1, n_games, seed, speech_mode), flush=True)
        if i == 0:
            s = WolfSession(1, ["自测玩家"], seed=seed, auto_human=True,
                            selftest=True, speech_mode=speech_mode)
        else:
            s = WolfSession(0, [], seed=seed, selftest=True,
                            speech_mode=speech_mode)
        # ★ `_run()` 本身就是线程体（`start()` 里才 `Thread(target=self._run)`）。
        #   直接调 `s._run()` 会阻塞当前线程，超时保护永远轮不到。
        #   正解：`s.start()` + `thread.join(timeout)`。
        s.start()
        s.thread.join(per_game_timeout)
        if s.thread.is_alive():
            print("[selftest] game %d TIMEOUT after %.0fs (speech_mode=%s)"
                  " —— 这一局没写完，不当作成功；线程是 daemon，进程可正常退出"
                  % (i + 1, per_game_timeout, speech_mode), flush=True)
            results.append({"game": i + 1, "seed": seed, "ok": False,
                            "timeout": True, "waited_s": per_game_timeout})
            break
        dt = time.time() - t0
        print("[selftest] game %d done in %.1fs" % (i + 1, dt), flush=True)
        results.append({"game": i + 1, "seed": seed, "ok": True,
                        "timeout": False, "sec": round(dt, 1)})
    n_ok = sum(1 for r in results if r["ok"])
    print("wrote %d/%d games to %s speech_mode=%s total=%.1fs"
          % (n_ok, n_games, RECORD_ROOT, speech_mode, time.time() - t_all),
          flush=True)
    return results


def selftest_model(record_root, n_games=100, profile="symmetric_q_only"):
    global RECORD_ROOT
    RECORD_ROOT = Path(record_root)
    t0 = time.time()
    rows = []
    errs = []
    for i in range(n_games):
        s = WolfSession(0, [], seed=2026120000 + i, selftest=True,
                        ai_mode="model", model_profile=profile)
        s.speed = 100.0
        rec = s.run_blocking()
        if rec is None:
            errs.append({"seed": 2026120000 + i, "errors": ["session 未返回记录"]})
            break
        rows.append(rec)
        e = RL.validate(rec)
        if e:
            errs.append({"seed": rec.get("seed"), "errors": e})
    winners = {"good": 0, "wolf": 0, "draw": 0}
    for rec in rows:
        w = rec["result"]["winner"]
        winners[w] = winners.get(w, 0) + 1
    summary = {
        "profile": profile, "n_games": len(rows), "n_errors": len(errs),
        "errors": errs[:5], "winner_counts": winners,
        "invalid_actions_total": sum(
            rec["result"]["invalid_actions"] for rec in rows),
        "elapsed_sec": round(time.time() - t0, 2),
        "record_root": str(RECORD_ROOT),
        "note": "GUI 模型档自测；游戏胜率不是研究结果。",
    }
    out = Path(RECORD_ROOT) / "_gui_selftest_model"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("GUI_MODEL_SELFTEST_PASS" if not errs and len(rows) == n_games
          else "GUI_MODEL_SELFTEST_FAIL")
    return summary


def lrs090_smoke(record_root, speech_mode="single"):
    """LRS_090 A.3：**真窗口**冒烟。

    与 `screenshot()` 的区别：那个是 headless（`pygame.HIDDEN`），证明不了"窗口能开"。
    这里开真窗口、跑几帧事件循环、存一张 PNG、打印 `LRS090_SMOKE_OK` 后退出。
    用途只有一个：验证 `start_多发言实验.bat` 真的能把 GUI 拉起来。
    """
    global RECORD_ROOT
    RECORD_ROOT = Path(record_root)
    print("LRS090_SMOKE start  speech_mode=%s  record_root=%s"
          % (speech_mode, RECORD_ROOT), flush=True)
    app = WolfApp(record_root=str(RECORD_ROOT), headless=False)
    print("LRS090_SMOKE window=%sx%s hidden=%s"
          % (app.screen.get_width(), app.screen.get_height(),
             bool(app.screen.get_flags() & pygame.HIDDEN)), flush=True)
    if speech_mode == "multi":
        app.speech_variant = "multi"
        app.ai_mode = "heuristic"
        app.model_profile = "symmetric_q_only"
    # 跑几帧真实事件循环（不喂事件，只让 pygame 画 + 处理窗口消息）
    app.state = "setup"
    for i in range(12):
        pygame.event.pump()
        app.draw()
        pygame.display.flip()
        app.clock.tick(60)
    shot_dir = RECORD_ROOT / ".." / "_lrs090_shot"
    shot_dir.mkdir(parents=True, exist_ok=True)
    shot = shot_dir / ("smoke_%s.png" % speech_mode)
    pygame.image.save(app.screen, str(shot))
    print("LRS090_SMOKE shot=%s bytes=%d" % (shot.resolve(), shot.stat().st_size), flush=True)
    print("LRS090_SMOKE state=%s n_humans=%s" % (app.state, getattr(app, "n_humans", None)),
          flush=True)
    pygame.quit()
    print("LRS090_SMOKE_OK", flush=True)
    return 0


def gui_flow_test(record_root):
    """菜单→切档→开局→记录 的 GUI 流程测试（无人工点击）。"""
    global RECORD_ROOT
    RECORD_ROOT = Path(record_root)
    app = WolfApp(record_root=str(RECORD_ROOT), headless=True)
    if not app.model_ok:
        raise RuntimeError("模型档不可用：%s" % app.model_reason)
    app.click("ai_profile")
    assert app.state == "ai", app.state
    app.click(("ai_pick", "symmetric_q_only"))
    assert app.ai_mode == "model" and app.model_profile == "symmetric_q_only"
    app.ai_game()
    app.session.speed = 20.0
    t0 = time.time()
    while app.state == "game" and time.time() - t0 < 60:
        app.pump()
        app.draw()
        time.sleep(0.01)
    if app.state != "result" or not app.result:
        raise RuntimeError("GUI 流程未在 60 秒内到 result")
    errs = RL.validate(app.result)
    if errs:
        raise RuntimeError("GUI 流程记录不合法：%r" % errs)
    print("GUI_FLOW_PASS seed=%s winner=%s profile=%s"
          % (app.result["seed"], app.result["result"]["winner"],
             app.result.get("model_profile")))
    pygame.quit()
    return app.result


def screenshot(out_dir):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    app = WolfApp(headless=True)
    app.state = "setup"; app.draw(); pygame.image.save(app.screen, str(out / "01_setup.png"))
    app.state = "manual"; app.manual_page = 1; app.draw(); pygame.image.save(app.screen, str(out / "01b_manual.png"))
    app.state = "ach"; app.draw(); pygame.image.save(app.screen, str(out / "01c_ach.png"))
    app.state = "settings"; app.draw(); pygame.image.save(app.screen, str(out / "01d_settings.png"))
    app.state = "ai"; app.draw(); pygame.image.save(app.screen, str(out / "01e_ai_profile.png"))
    if not app.model_ok:
        pygame.image.save(app.screen, str(out / "01e_ai_profile_no_torch.png"))
    if app.model_ok:
        app.state = "ai_confirm"; app.pending_profile = "auto_hybrid"
        app.draw(); pygame.image.save(app.screen, str(out / "01f_ai_confirm.png"))
    app.state = "game"
    app.n_humans = 1; app.names = ["你"]
    app.view = {"pid": 0, "day": 2, "night_no": 2, "phase": "day_speech",
                "alive": [0, 1, 2, 4, 5, 6], "my_role": "SEER",
                "my_faction": "good", "teammates": [],
                "seer_results": {"4": True},
                "public": {"speeches": [{"day": 1, "pid": 3, "action": A.accuse(2, "WOLF")}],
                           "votes": [{"day": 1, "voter": 0, "target": 2}],
                           "deaths": [{"day": 1, "phase": "night", "pid": 3,
                                       "cause": "WOLF_KILL", "by": None}]}}
    app.bubble = (5, "阿川：我认为 2 号是狼人", pygame.time.get_ticks() + 99999)
    app.draw(); pygame.image.save(app.screen, str(out / "02_game.png"))
    app.request = {"pid": 0, "view": app.view,
                   "legal": [A.silence(), A.self_claim("SEER"), A.check(1)]}
    app.handover = 1; app.draw()
    pygame.image.save(app.screen, str(out / "03_request.png"))
    app.n_humans = 0; app.names = []; app.request = None; app.state = "game"
    app.draw(); pygame.image.save(app.screen, str(out / "02b_ai_game.png"))
    app.n_humans = 1; app.names = ["你"]; app.state = "result"
    app.result = {"result": {"winner_cn": "好人阵营", "reason": "狼人全灭",
                             "reveal_after_game": {"roles": {"0": "SEER", "1": "VILLAGER",
                                                             "2": "WOLF", "3": "WOLF",
                                                             "4": "WITCH", "5": "HUNTER",
                                                             "6": "VILLAGER"}}}}
    app.draw(); pygame.image.save(app.screen, str(out / "04_result.png"))
    pygame.quit()
    print("shots", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record-root", default=str(RECORD_ROOT))
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--screenshot", default="")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--selftest-model", type=int, default=0, metavar="N")
    ap.add_argument("--selftest-model-profile", default="symmetric_q_only")
    ap.add_argument("--wolf-model", default="",
                    choices=[""] + list(AG.MODEL_META.keys()),
                    help="直接用研究权重开局（需要 torch + LRS_075 接口包）")
    ap.add_argument("--flow-test", action="store_true")
    ap.add_argument("--lrs090-smoke", action="store_true",
                    help="LRS_090: open a REAL window, render a few frames, "
                         "print LRS090_SMOKE_OK, then exit (bat launcher smoke test)")
    ap.add_argument("--selftest-games", type=int, default=3,
                    help="LRS_091: --selftest 跑几局（默认 3）")
    ap.add_argument("--speech-mode", default="single", choices=("single", "multi"),
                    help="LRS_084: single=default three-choice; multi=experimental")
    a = ap.parse_args()
    if a.selftest_model:
        selftest_model(a.record_root, a.selftest_model,
                       a.selftest_model_profile)
    elif a.flow_test:
        gui_flow_test(a.record_root)
    elif a.lrs090_smoke:
        lrs090_smoke(a.record_root, a.speech_mode)
    elif a.selftest:
        # LRS_091：`--selftest` 以前不读 `--speech-mode`，导致"用 exe 跑一局 multi"
        # 没有路径可走（`--flow-test` 需要研究模型接口包，发布包里没有）。
        # 这里把 speech_mode 透传下去；默认仍是 single，行为不变。
        # 另加 `--selftest-games N`：打包验证只需要跑 1 局，
        # 而 multi 档**第 3 局偶发卡死**（见 selftest 的注释），跑得越少越稳。
        selftest(a.record_root, n_games=a.selftest_games, speech_mode=a.speech_mode)
    elif a.screenshot:
        screenshot(a.screenshot)
    else:
        app = WolfApp(record_root=a.record_root, headless=a.headless)
        # LRS_084：独立启动入口一开就是实验档；研究模型未适配 ⇒ 强制启发式
        if a.speech_mode == "multi":
            app.speech_variant = "multi"
            app.ai_mode = "heuristic"
            app.model_profile = "symmetric_q_only"
        if a.wolf_model:
            if not AG.model_available():
                raise SystemExit("研究模型不可用：%s" % AG.model_unavailable_reason())
            app.ai_mode = "model"
            app.model_profile = a.wolf_model
        app.run()


if __name__ == "__main__":
    main()
