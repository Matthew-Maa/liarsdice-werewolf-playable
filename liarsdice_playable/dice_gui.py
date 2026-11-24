# -*- coding: utf-8 -*-
"""吹牛骰子 · pygame 画面版（RA-027）。

玩家看到的只有：角色名、风格、难度；研究卡/算法只在记录里留技术字段。
"""
import argparse
import copy
import json
import os
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

from vendor.liarsdice.game.env import LiarDiceEnv          # noqa: E402
from vendor.liarsdice.game.logic import is_valid_bid       # noqa: E402
import agents as AG                                         # noqa: E402
import record_lib as RL                                     # noqa: E402
import play as CLI                                          # noqa: E402
import gui_common as G                                      # noqa: E402
import profile as PF                                        # noqa: E402

GAME = "liarsdice"
DICE_N = 5
SPECIAL = 1
MAX_Q = 10
RECORD_ROOT = APP_DIR.parent / "对局记录"


class GameAborted(Exception):
    """玩家中途退出：结束这一局并写“未完成”记录。"""

# 玩家看到的角色（不含研究卡/算法）
OPPONENTS = [
    dict(key="baseline", name="老实人", portrait="portrait_1.jpg", diff="入门",
         style="稳健 · 不诈唬", stars=1,
         blurb="按自己的牌算概率，不记你，也不会故意骗你。第一局就找他。"),
    dict(key="peak", name="老江湖", portrait="portrait_3.jpg", diff="进阶",
         style="压迫 · 敢开敢加", stars=3,
         blurb="气场很足，喜欢把叫价往上顶；你一犹豫，他就加码。"),
    dict(key="memory", name="记忆师", portrait="portrait_5.jpg", diff="高手",
         style="记仇 · 盯你的习惯", stars=3,
         blurb="你上一局怎么开、怎么加，它都记着；同一个套路别用两次。"),
    dict(key="bayes", name="读心者", portrait="portrait_2.jpg", diff="专家",
         style="读牌 · 越玩越懂你", stars=4,
         blurb="它把你的手牌当成未知数，一局一局更新判断；玩得越久越难骗。"),
]
BY_KEY = {o["key"]: o for o in OPPONENTS}
PLAYER_PORTRAIT = "portrait_4.jpg"


def bid_cn(q, f):
    return "叫 %s 个 %s" % (CLI.CN_NUM.get(q, q), CLI.FACES.get(f, f))


def action_text(a):
    if a[0] == "challenge":
        return "开！"
    return bid_cn(a[1], a[2])


# ==================== 对局线程 ====================

class HumanProxy:
    def start_game(self):
        pass

    def on_challenge_result(self, q, total):
        pass

    def on_dice_revealed(self, payload, seat):
        pass


class GuiDiceAgent:
    def __init__(self, real, seat, session, human=False):
        self.real = real
        self.seat = seat
        self.session = session
        self.human = human

    def start_game(self):
        f = getattr(self.real, "start_game", None)
        if f:
            f()

    def _obs(self, state):
        hist = [{"seat": s, "qty": q, "face": f} for q, f, s in self.session.call_log]
        return {
            "seat": self.seat,
            "round": len(self.session.actions) + 1,
            "own_dice": list(state["own_dice"]),
            "dice_total": DICE_N * 2,
            "special_rule": int(state["special_rule"]),
            "history": hist,
            "last_bid": (None if not hist else
                         {"seat": hist[-1]["seat"], "qty": hist[-1]["qty"],
                          "face": hist[-1]["face"]}),
            "visible": "只有自己的骰子 + 公开叫牌历史（非上帝视角）",
        }

    def decide(self, state):
        obs = self._obs(state)
        if self.human and not self.session.auto_human:
            self.session.q.put(("human_turn", obs))
            self.session.ev.wait()
            self.session.ev.clear()
            if self.session.aborted:
                raise GameAborted()
            action = self.session.action
        else:
            if self.human:
                action = self.real.decide(state)
            else:
                self.session.q.put(("thinking", self.seat, obs))
                self.session.ai_sleep(1.0)
                if self.session.aborted:
                    raise GameAborted()
                action = self.real.decide(state)
        if self.session.aborted:
            raise GameAborted()
        self.session.record(obs, action, self.seat)
        self.session.q.put(("action", self.seat, action))
        return action

    def on_challenge_result(self, q, total):
        self.session.total = int(total)
        f = getattr(self.real, "on_challenge_result", None)
        if f:
            f(q, total)

    def on_dice_revealed(self, payload, seat):
        self.session.payload = payload
        f = getattr(self.real, "on_dice_revealed", None)
        if f:
            f(payload, seat)


class DiceSession:
    def __init__(self, human_key, human_name="你", seed=None, spectator=False,
                 ai2_key=None, auto_human=False, selftest=False, special=1):
        self.q = queue.Queue()
        self.ev = threading.Event()
        self.action = None
        self.actions = []
        self.call_log = []
        self.total = None
        self.payload = None
        self.auto_human = auto_human
        self.selftest = bool(selftest)
        self.special = int(special)
        self.paused = False
        self.speed = 1.0
        self.aborted = False
        self.resume_ev = threading.Event()
        self.resume_ev.set()
        self.fast = False
        self.seed = int(seed if seed is not None else random.randrange(1, 10 ** 9))
        self.spectator = spectator
        self.human_name = human_name or "你"
        self.human_key = human_key
        self.ai2_key = ai2_key
        self.rec = None
        self.thread = None
        self.lock = threading.Lock()

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
            self.thread.join(timeout=4)
        return self.rec

    def respond(self, action):
        self.action = action
        self.ev.set()

    def record(self, obs, action, seat):
        with self.lock:
            if action[0] == "call":
                act = {"type": "CALL", "qty": int(action[1]), "face": int(action[2])}
                code = "CALL:%d:%d" % (action[1], action[2])
                txt = bid_cn(action[1], action[2])
                self.call_log.append((int(action[1]), int(action[2]), int(seat)))
            else:
                act = {"type": "OPEN"}
                code, txt = "OPEN", "开！"
            self.actions.append({"t": len(self.actions), "seat": seat,
                                 "phase": "bidding" if action[0] == "call" else "challenge",
                                 "observation": obs, "action": act,
                                 "code": code, "text": txt})

    def _player_entries(self):
        p = [RL.player_entry(0, "human", self.human_name)]
        def ai_entry(seat, key):
            info = AG.AI_INFO[key]
            o = BY_KEY[key]
            return RL.player_entry(seat, "ai", o["name"],
                                   agent_id=info["agent_id"],
                                   agent_version=info["agent_version"] + (
                                       "-whole" if key == "memory" else ""),
                                   card=info["card"],
                                   level="%s · %s" % (o["diff"], o["style"]))
        if self.spectator:
            p = [ai_entry(0, self.human_key), ai_entry(1, self.ai2_key)]
        else:
            p.append(ai_entry(1, self.human_key))
        return p

    def _run(self):
        try:
            if self.spectator:
                a0 = GuiDiceAgent(AG.make_ai(self.human_key, "whole"), 0, self)
                a1 = GuiDiceAgent(AG.make_ai(self.ai2_key, "whole"), 1, self)
            else:
                real_h = CLI.ScriptedHuman(self.human_name) if self.auto_human else HumanProxy()
                a0 = GuiDiceAgent(real_h, 0, self, human=True)
                a1 = GuiDiceAgent(AG.make_ai(self.human_key, "whole"), 1, self)
            random.seed(self.seed)
            a0.start_game(); a1.start_game()
            env = LiarDiceEnv(a0, a1, dice_num=DICE_N,
                              special_rule=self.special)
            env.reject_log = []
            winner = env.play_one_game()
            challenger = None
            for x in reversed(self.actions):
                if x["action"]["type"] == "OPEN":
                    challenger = x["seat"]
                    break
            caller = 1 - challenger if challenger is not None else None
            last_call = self.actions[-1]["action"] if self.actions else {}
            if challenger is not None:
                for x in reversed(self.actions):
                    if x["action"]["type"] == "CALL":
                        last_call = x["action"]
                        break
            names = [p["name"] for p in self._player_entries()]
            payload = self.payload or {"dice": [[], []]}
            result = {
                "winner_seat": int(winner),
                "winner_name": names[winner],
                "winner_side": "p%d" % winner,
                "loser_seat": 1 - int(winner),
                "reason": ("开盅成功：叫牌不成立" if winner == challenger
                           else "开盅失败：叫牌成立"),
                "challenger_seat": challenger,
                "caller_seat": caller,
                "final_bid": {"qty": int(last_call.get("qty", 0)),
                              "face": int(last_call.get("face", 0)),
                              "text": bid_cn(last_call.get("qty", 0),
                                             last_call.get("face", 0))},
                "revealed_total": self.total,
                "special_rule": int(payload.get("special_rule", self.special)),
                "hands": {"p0": payload.get("dice", [[], []])[0],
                          "p1": payload.get("dice", [[], []])[1]},
                "rounds": len(self.actions),
            }
            meta = {"rules": {"dice_per_player": DICE_N,
                              "special_rule": self.special,
                              "special_rule_cn": ("1 是百搭（叫过 1 之后百搭失效）"
                                                  if self.special else "无百搭"),
                              "dice_total": DICE_N * 2},
                    "note": "seed 可复现发牌与先手；AI 跨局记忆会改变后续决策",
                    "rejected_calls": list(env.reject_log or [])}
            rec = RL.make_record(GAME, self.seed,
                                 "ai_vs_ai" if self.spectator else "human_vs_ai",
                                 self._player_entries(), self.actions, result,
                                 meta=meta,
                                 selftest=self.selftest or self.auto_human)
            RL.write_record(rec, str(RECORD_ROOT), GAME)
            self.rec = rec
            self.q.put(("game_over", rec))
        except GameAborted:
            result = self._aborted_result()
            meta = {"rules": {"dice_per_player": DICE_N,
                              "special_rule": self.special,
                              "special_rule_cn": ("1 是百搭（叫过 1 之后百搭失效）"
                                                  if self.special else "无百搭"),
                              "dice_total": DICE_N * 2},
                    "note": "玩家中途退出；hands 只含退出前已经看到过的座位，未开盅",
                    "aborted": True, "ended_by": "human_quit"}
            rec = RL.make_record(GAME, self.seed,
                                 "ai_vs_ai" if self.spectator else "human_vs_ai",
                                 self._player_entries(), self.actions, result,
                                 meta=meta,
                                 selftest=self.selftest or self.auto_human)
            RL.write_record(rec, str(RECORD_ROOT), GAME)
            self.rec = rec
        except Exception as e:
            self.q.put(("error", repr(e)))

    def _aborted_result(self):
        p0 = []
        for x in reversed(self.actions):
            if x["seat"] == 0:
                p0 = list(x["observation"].get("own_dice") or [])
                break
        last = {}
        for x in reversed(self.actions):
            if x["action"].get("type") == "CALL":
                last = x["action"]
                break
        q = int(last.get("qty", 0))
        f = int(last.get("face", 0))
        return {
            "aborted": True,
            "winner_seat": -1,
            "winner_name": "",
            "winner_side": "",
            "loser_seat": -1,
            "reason": "中途退出（未完成）",
            "challenger_seat": None,
            "caller_seat": None,
            "final_bid": {"qty": q, "face": f,
                          "text": bid_cn(q, f) if q else "未开盅"},
            "revealed_total": None,
            "hands": {"p0": p0, "p1": []},
            "rounds": len(self.actions),
        }

    def run_blocking(self):
        self._run()
        return self.rec


# ==================== 界面 ====================

class DiceApp:
    def __init__(self, record_root=None, headless=False):
        global RECORD_ROOT
        if record_root:
            RECORD_ROOT = Path(record_root)
        pygame.init()
        G.ensure_sfx()
        G.make_ambient(G.ASSETS / "bgm_werewolf.wav")
        flags = 0
        if headless:
            flags = pygame.HIDDEN
        self.screen = pygame.display.set_mode((G.W, G.H), flags)
        try:
            pygame.key.start_text_input()
        except Exception:
            pass
        pygame.display.set_caption("吹牛骰子 · 桌上见真章")
        self.clock = pygame.time.Clock()
        self.profile = PF.Profile("liarsdice", APP_DIR)
        _vol = self.profile.setting_raw("music_volume", 0.28)
        _mode = self.profile.setting_raw("music_mode", "list")
        _track = self.profile.setting_raw("music_track", 0)
        if isinstance(_vol, bool) or not isinstance(_vol, (int, float)):
            _vol = 0.28
        if _mode not in ("list", "single"):
            _mode = "list"
        if isinstance(_track, bool) or not isinstance(_track, int):
            _track = 0
        self.music = G.Music(volume=float(_vol), mode=_mode, start=_track,
                             enabled=self.profile.setting("music", True))
        self.sfx = G.Sfx()
        self.sfx.enabled = self.profile.setting("sfx", True)
        self.toast = G.Toast()
        self.special = self.profile.setting("special", True)
        self.state = "splash"
        self.splash_start = pygame.time.get_ticks()
        self.manual_page = 0
        self.title_rect = pygame.Rect(G.W // 2 - 230, 64, 460, 84)
        self.mode = "human"
        self.sel = []
        self.session = None
        self.human_obs = None
        self.thinking = False
        self.current_bid = ""
        self.log = []
        self.reveal = None
        self.result = None
        self.result_t0 = 0
        self.qty = 3
        self.face = 2
        self.paused = False
        self.speed = 0.5
        self.running = True
        self.hover_card = None
        self.buttons = []
        self.name_input = G.TextInput((G.W // 2 - 160, 262, 320, 46),
                                      value="你", max_len=10)
        self.die_anim = 0.0
        self.turn_seat = None
        self.face_rects = []

    # ---------- 背景与标题 ----------
    def draw_bg(self, dim=150):
        self.screen.blit(G.bank.image("bg_casino.jpg", (G.W, G.H)), (0, 0))
        ov = pygame.Surface((G.W, G.H), pygame.SRCALPHA)
        ov.fill((8, 6, 5, dim))
        self.screen.blit(ov, (0, 0))

    def draw_title(self, title="吹 牛 骰 子", sub="五 颗 骰 子 · 一 个 百 搭 · 谁 先 心 虚"):
        G.text(self.screen, title, 68, G.GOLD, (G.W // 2, 92), bold=True,
               align="center")
        G.text(self.screen, sub, 22, G.CREAM, (G.W // 2, 160), align="center")

    def draw_music(self):
        self.music.draw(self.screen)


    # ---------- 开场 ----------
    def splash(self):
        self.draw_bg(175)
        self.draw_title()
        G.text(self.screen, "2026 · 桌上见真章", 20, G.MUTED,
               (G.W // 2, 220), align="center")
        G.text(self.screen, "点击开始", 26, G.GOLD, (G.W // 2, 430),
               bold=True, align="center")
        self.buttons = []
        self.draw_music()

    # ---------- 菜单 ----------
    def menu(self):
        self.draw_bg(165)
        self.draw_title()
        bs = [
            G.Button((G.W // 2 - 170, 226, 340, 58), "开 始 游 戏", "primary", 24, tag="play"),
            G.Button((G.W // 2 - 170, 298, 340, 52), "玩 家 手 册", "ghost", 21, tag="manual"),
            G.Button((G.W // 2 - 170, 364, 340, 52), "成 就", "ghost", 21, tag="ach"),
            G.Button((G.W // 2 - 170, 430, 340, 52), "设 置", "ghost", 21, tag="settings"),
            G.Button((G.W // 2 - 170, 496, 340, 46), "退 出", "ghost", 19, tag="quit"),
        ]
        self.buttons = bs
        for b in bs:
            b.draw(self.screen)
        wins = int(self.profile.data.get("wins", 0))
        games = int(self.profile.data.get("games", 0))
        got = len(self.profile.data.get("achievements") or {})
        G.text(self.screen, "战绩：%d 胜 / %d 局 · 成就 %d/%d" % (
            wins, games, got, len(PF.ACH["liarsdice"])), 18, G.CREAM,
            (G.W // 2, 570), align="center")
        self.draw_music()

    def mode_select(self):
        self.draw_bg(150)
        G.text(self.screen, "怎 么 打", 50, G.GOLD, (G.W // 2, 76), bold=True,
               align="center")
        G.text(self.screen, "给对手起个名字，然后选一种打法。", 19, G.CREAM,
               (G.W // 2, 136), align="center")
        G.text(self.screen, "你的名字", 18, G.MUTED, (G.W // 2 - 160, 238),
               bold=True)
        self.name_input.rect.y = 262
        self.name_input.draw(self.screen)
        bs = [
            G.Button((G.W // 2 - 190, 340, 380, 62), "人 机 对 战", "primary", 24, tag="human"),
            G.Button((G.W // 2 - 190, 420, 380, 62), "A I  内 战 旁 观", "ghost", 22, tag="ai"),
            G.Button((G.W // 2 - 90, 508, 180, 46), "返回", "ghost", 19, tag="back_menu"),
        ]
        self.buttons = bs
        for b in bs:
            b.draw(self.screen)
        self.draw_music()

    def manual(self):
        self.draw_bg(170)
        pages = [
            ("规则", [
                "每人 5 颗骰子，只看自己的；1 是百搭，可当任何点数。",
                "轮流叫牌：数量要更大，或数量相同、点数更大（1 点算最大）。",
                "叫过 1 之后，本局百搭失效；先手至少叫 3 个（叫 1 至少 2 个）。",
                "不想跟就喊“开”：开盅数全场，够叫价则叫牌者赢，不够则开的人赢。",
                "开盅时双方手牌公开；每局结束自动保存一份本地记录。",
            ]),
            ("四个对手", [
                "老实人 · 入门：稳健，不诈唬，适合第一局。",
                "老江湖 · 进阶：压迫感强，喜欢把叫价往上顶。",
                "记忆师 · 高手：记住你上一局怎么开、怎么加。",
                "读心者 · 专家：把你的手牌当未知数，越玩越懂你。",
                "风格不同，不代表绝对强弱；换着打才有意思。",
            ]),
            ("记录与隐私", [
                "每一局会写一行到 对局记录/liarsdice/日期.jsonl。",
                "记录包含：谁、seed、每一步只属于当事人的信息、动作、结果。",
                "不联网、不上传；昵称只留在本机。",
                "AI 会跨局记忆，所以同一个 seed 能复现发牌，但不保证复现后续选择。",
            ]),
            ("小技巧", [
                "先看自己手里有几个目标点数，再判断对手要补几个。",
                "1 是百搭，但叫 1 会关掉百搭；什么时候叫 1 是门学问。",
                "对手突然加得很凶，未必是牌好，可能是在吓你。",
                "连续输给同一个角色时，换一种开局节奏。",
            ]),
        ]
        title, lines = pages[self.manual_page % len(pages)]
        G.text(self.screen, "玩 家 手 册", 46, G.GOLD, (G.W // 2, 54),
               bold=True, align="center")
        G.panel(self.screen, (180, 112, 920, 470), color=(20, 17, 15), alpha=235)
        G.text(self.screen, "%d / %d · %s" % (self.manual_page + 1, len(pages), title),
               24, G.GOLD_L, (220, 132), bold=True)
        for i, line in enumerate(lines):
            color = G.GOLD_L if ("1 是百搭" in line or "叫过 1" in line) else G.CREAM
            G.wrap_text(self.screen, line, 21, color, (220, 186 + i * 68),
                        840, line_h=30)
        prev = G.Button((250, 610, 140, 44), "上一页", "ghost", 18, tag="man_prev")
        nxt = G.Button((G.W - 390, 610, 140, 44), "下一页", "ghost", 18, tag="man_next")
        back = G.Button((G.W // 2 - 80, 610, 160, 44), "返回", "primary", 18, tag="back_menu")
        self.buttons = [prev, nxt, back]
        for b in self.buttons:
            b.draw(self.screen)

    def achievements(self):
        self.draw_bg(170)
        G.text(self.screen, "成 就", 48, G.GOLD, (G.W // 2, 46), bold=True,
               align="center")
        got = self.profile.data.get("achievements") or {}
        G.text(self.screen, "已解锁 %d / %d" % (sum(1 for a in PF.ACH["liarsdice"] if got.get(a[0])),
                                                len(PF.ACH["liarsdice"])), 19,
               G.CREAM, (G.W // 2, 100), align="center")
        G.panel(self.screen, (150, 132, 980, 500), color=(20, 17, 15), alpha=238)
        for i, (aid, name, desc) in enumerate(PF.ACH["liarsdice"]):
            col = i % 2
            row = i // 2
            x = 180 + col * 480
            y = 158 + row * 92
            on = bool(got.get(aid))
            G.text(self.screen, "✓" if on else "○", 22, G.GOLD if on else (90, 82, 70),
                   (x, y))
            G.text(self.screen, name if on else "???", 21, G.CREAM if on else G.MUTED,
                   (x + 34, y - 2), bold=on)
            G.text(self.screen, "已解锁" if on else "未解锁", 17,
                   G.GOLD_L if on else (90, 82, 70), (x + 34, y + 28))
        b = G.Button((G.W // 2 - 80, 652, 160, 44), "返回", "primary", 18, tag="back_menu")
        self.buttons = [b]
        b.draw(self.screen)

    def settings(self):
        self.draw_bg(170)
        G.text(self.screen, "设 置", 48, G.GOLD, (G.W // 2, 52), bold=True,
               align="center")
        G.panel(self.screen, (280, 100, 720, 540), color=(20, 17, 15), alpha=238)
        def onoff(v):
            return "开" if v else "关"
        self.buttons = []
        y = 148
        G.text(self.screen, "音乐", 21, G.CREAM, (320, y))
        b = G.Button((850, y - 8, 120, 42), onoff(self.music.enabled),
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
        b = G.Button((850, y - 8, 120, 42), onoff(self.sfx.enabled),
                     "primary" if self.sfx.enabled else "ghost", 19,
                     tag=("toggle", "sfx"))
        self.buttons.append(b); b.draw(self.screen)
        y += 64
        G.text(self.screen, "百搭模式（1 可当任何点数）", 21, G.CREAM, (320, y))
        b = G.Button((850, y - 8, 120, 42), onoff(self.special),
                     "primary" if self.special else "ghost", 19,
                     tag=("toggle", "special"))
        self.buttons.append(b); b.draw(self.screen)
        back = G.Button((G.W // 2 - 80, 596, 160, 46), "返回", "primary", 19,
                        tag="back_menu")
        self.buttons.append(back)
        back.draw(self.screen)

    # ---------- 选人 ----------
    def select(self):
        self.draw_bg(150)
        title = "选 一 个 对 手" if self.mode == "human" else "选 两 位 选 手"
        G.text(self.screen, title, 46, G.GOLD, (G.W // 2, 52), bold=True,
               align="center")
        n = len(OPPONENTS)
        card_w = 260
        gap = 24
        total = n * card_w + (n - 1) * gap
        x0 = (G.W - total) // 2
        self.buttons = []
        for i, o in enumerate(OPPONENTS):
            r = pygame.Rect(x0 + i * (card_w + gap), 126, card_w, 476)
            selected = o["key"] in self.sel
            order = self.sel.index(o["key"]) + 1 if selected else 0
            G.panel(self.screen, r, color=(24, 20, 18), alpha=232,
                    border=G.GOLD if selected else (92, 80, 62),
                    border_w=4 if selected else 2)
            if selected:
                G.pill(self.screen, (r.x + 12, r.y + 12, 36, 30),
                       str(order), G.GOLD, G.INK, 18)
            G.draw_avatar(self.screen, o["portrait"],
                          (r.centerx, r.y + 112), 74,
                          ring=G.GOLD if selected else (120, 104, 82))
            G.text(self.screen, o["name"], 30, G.CREAM,
                   (r.centerx, r.y + 200), bold=True, align="center")
            G.text(self.screen, o["style"], 18, G.GOLD_L,
                   (r.centerx, r.y + 238), align="center")
            G.text(self.screen, "难度 " + o["diff"], 18, G.MUTED,
                   (r.centerx, r.y + 266), align="center")
            # stars
            for k in range(4):
                col = G.GOLD if k < o["stars"] else (70, 62, 52)
                pygame.draw.circle(self.screen, col,
                                   (r.centerx - 30 + k * 20, r.y + 296), 6)
            G.wrap_text(self.screen, o["blurb"], 16, G.CREAM,
                        (r.x + 18, r.y + 326), r.w - 36, line_h=22)
            self.buttons.append(G.Button((r.x + 50, r.bottom - 52, r.w - 100, 40),
                                         "已 选" if selected else "选 他",
                                         "primary" if selected else "ghost",
                                         19, tag=("pick", o["key"])))
        for b in self.buttons:
            b.draw(self.screen)
        back = G.Button((40, G.H - 82, 120, 46), "返回", "ghost", 20, tag="back")
        start = G.Button((G.W - 240, G.H - 82, 200, 46), "开 始",
                         "primary", 22, enabled=bool(self.sel), tag="start")
        self.buttons += [back, start]
        back.draw(self.screen)
        start.draw(self.screen)
        self.draw_music()

    # ---------- 桌面 ----------
    def draw_table(self):
        self.draw_bg(120)
        # 顶栏
        pygame.draw.rect(self.screen, (12, 10, 9), (0, 0, G.W, 56))
        G.text(self.screen, "吹牛骰子", 24, G.GOLD, (28, 14), bold=True)
        if self.mode == "ai":
            label = "AI 内战 · 旁观"
        else:
            label = "人机对战"
        G.text(self.screen, label, 18, G.MUTED, (170, 20))
        self.draw_music()
        # 对手（AI 内战时上下各一位）
        ai = BY_KEY[self.sel[0] if self.mode == "ai" else self.sel[0]]
        G.draw_avatar(self.screen, ai["portrait"], (G.W // 2, 132), 62,
                      ring=G.GOLD)
        G.text(self.screen, ai["name"], 24, G.CREAM, (G.W // 2, 202),
               bold=True, align="center")
        G.text(self.screen, ai["style"], 18, G.GOLD_L, (G.W // 2, 232),
               align="center")
        # 对手骰子（背面）
        for i in range(5):
            G.draw_die(self.screen, (G.W // 2 - 170 + i * 70, 262, 56, 56),
                       0, hidden=True)
        # 中间叫价
        G.panel(self.screen, (G.W // 2 - 230, 340, 460, 92),
                color=(18, 16, 14), alpha=225)
        bid = self.current_bid or "还没有人叫牌"
        G.text(self.screen, bid, 30, G.GOLD_L, (G.W // 2, 362),
               bold=True, align="center")
        if self.thinking:
            G.text(self.screen, "对手正在想……", 20, G.MUTED,
                   (G.W // 2, 408), align="center")
        # 下方：人机=玩家；AI 内战=第二位
        if self.mode == "ai":
            low = BY_KEY[self.sel[1]]
            G.draw_avatar(self.screen, low["portrait"], (G.W // 2, 500), 48,
                          ring=G.GOLD)
            G.text(self.screen, low["name"], 20, G.CREAM, (G.W // 2, 552),
                   bold=True, align="center")
            own = None
        else:
            G.draw_avatar(self.screen, PLAYER_PORTRAIT, (G.W // 2, 500), 48,
                          ring=G.CREAM)
            pname = self.name_input.value.strip() or "你"
            G.text(self.screen, pname, 20, G.CREAM, (G.W // 2, 552),
                   bold=True, align="center")
            own = self.human_obs["own_dice"] if self.human_obs else None
        if self.result:
            own = self.result["result"]["hands"]["p0"]
        for i in range(5):
            val = own[i] if own else 0
            G.draw_die(self.screen, (G.W // 2 - 180 + i * 72, 582, 58, 58),
                       val, hidden=not own, highlight=bool(own))
        # 左侧记录
        G.panel(self.screen, (24, 90, 250, 420), color=(14, 12, 11), alpha=205)
        G.text(self.screen, "叫牌记录", 18, G.GOLD, (40, 104), bold=True)
        for i, line in enumerate(self.log[-12:]):
            G.text(self.screen, line, 18, G.CREAM, (40, 136 + i * 28))
        # 右下操作
        if self.mode == "human":
            self.draw_controls()
        else:
            pb = G.Button((G.W - 292, 54, 120, 34),
                          "继续" if self.paused else "暂停", "ghost", 15, tag="pause")
            sp = G.Button((G.W - 158, 54, 120, 34),
                          "速度 %gx" % self.speed, "ghost", 15, tag="speed")
            self.buttons = [pb, sp]
            for b in self.buttons:
                b.draw(self.screen)
        qb = G.Button((G.W - 330, 14, 130, 32), "退出本局", "ghost", 15,
                      tag="quit_game")
        self.buttons.append(qb)
        qb.draw(self.screen)

    def draw_controls(self):
        my_turn = bool(self.human_obs) and not self.thinking and not self.result
        G.panel(self.screen, (18, G.H - 76, G.W - 36, 66),
                color=(10, 8, 7), alpha=200, border=None, radius=12)
        y = G.H - 58
        qbtn = G.Button((G.W - 560, y, 40, 38), "−", "ghost", 22, enabled=my_turn,
                        tag="q-")
        qbtn2 = G.Button((G.W - 472, y, 40, 38), "+", "ghost", 22, enabled=my_turn,
                         tag="q+")
        G.text(self.screen, "%d 个" % self.qty, 20, G.CREAM, (G.W - 426, y + 8))
        faces = []
        for i, f in enumerate(range(1, 7)):
            r = pygame.Rect(G.W - 370 + i * 42, y, 36, 36)
            faces.append((f, r))
        add = G.Button((G.W - 120, y, 100, 38), "加叫", "primary", 18,
                       enabled=my_turn, tag="call")
        openb = G.Button((40, y, 140, 38), "开 盅", "danger", 18,
                         enabled=my_turn and bool(self.current_bid), tag="open")
        self.buttons = [qbtn, qbtn2, add, openb]
        qbtn.draw(self.screen); qbtn2.draw(self.screen); add.draw(self.screen)
        openb.draw(self.screen)
        self.face_rects = faces
        for f, r in faces:
            sel = f == self.face
            pygame.draw.rect(self.screen, G.GOLD if sel else (44, 38, 32), r,
                             border_radius=8)
            G.draw_die(self.screen, (r.x + 3, r.y + 3, 30, 30), f)

    # ---------- 结果 ----------
    def draw_result(self):
        self.draw_table()
        self.buttons = []
        r = self.result["result"]
        ov = pygame.Surface((G.W, G.H), pygame.SRCALPHA)
        ov.fill((8, 6, 5, 170))
        self.screen.blit(ov, (0, 0))
        G.panel(self.screen, (G.W // 2 - 300, 150, 600, 420),
                color=(22, 19, 17), alpha=245, border=G.GOLD, border_w=3)
        elapsed = max(0, pygame.time.get_ticks() - (self.result_t0 or 0))
        reveal_n = 0 if elapsed < 380 else min(10, 1 + (elapsed - 380) // 320)
        done = elapsed >= 380 + 10 * 320 + 420
        win = r["winner_seat"] == 0
        if not done:
            title = "开 盅"
        else:
            title = "你 赢 了" if win and self.mode == "human" else (
                "对 手 赢 了" if self.mode == "human" else "%s 胜" % r["winner_name"])
        G.text(self.screen, title, 44, G.GOLD if (done and win) else G.RED_L,
               (G.W // 2, 180), bold=True, align="center")
        if done:
            G.text(self.screen, r["reason"], 20, G.CREAM, (G.W // 2, 238),
                   align="center")
        else:
            G.text(self.screen, "开盅中… %d / 10" % reveal_n, 20, G.GOLD_L,
                   (G.W // 2, 238), align="center")
        G.text(self.screen, "最后一手：%s" % r["final_bid"]["text"], 22,
               G.CREAM, (G.W // 2, 274), align="center")
        if done:
            G.text(self.screen, "开盅实际：%s 个" % r["revealed_total"], 22,
                   G.CREAM, (G.W // 2, 306), align="center")
        special = int(r.get("special_rule", 0) or 0)
        target = int(r["final_bid"].get("face", 0) or 0)
        wild = bool(special and target != 1)
        lab0 = "1 号骰子" if self.mode == "ai" else "你的骰子"
        lab1 = "2 号骰子" if self.mode == "ai" else "对手骰子"
        G.text(self.screen, lab0, 18, G.MUTED, (G.W // 2 - 180, 348),
               align="center")
        G.text(self.screen, lab1, 18, G.MUTED, (G.W // 2 + 180, 348),
               align="center")
        for i in range(5):
            a = r["hands"]["p0"][i] if i < len(r["hands"]["p0"]) else 0
            b = r["hands"]["p1"][i] if i < len(r["hands"]["p1"]) else 0
            show_a = reveal_n > 2 * i
            show_b = reveal_n > 2 * i + 1
            G.draw_die(self.screen, (G.W // 2 - 250 + i * 30, 376, 28, 28), a,
                       hidden=not show_a,
                       highlight=bool(show_a and wild and a == 1))
            G.draw_die(self.screen, (G.W // 2 + 110 + i * 30, 376, 28, 28), b,
                       hidden=not show_b,
                       highlight=bool(show_b and wild and b == 1))
        if wild:
            G.text(self.screen, "1 = 百搭（本局有效）", 16, G.GOLD_L,
                   (G.W // 2, 414), align="center")
        if done:
            again = G.Button((G.W // 2 - 190, 460, 170, 48), "再 来 一 局",
                             "primary", 20, tag="again")
            back = G.Button((G.W // 2 + 20, 460, 170, 48), "换 对 手",
                            "ghost", 20, tag="select")
            self.buttons = [again, back]
            again.draw(self.screen); back.draw(self.screen)

    # ---------- 事件 ----------
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
            if kind == "human_turn":
                self.human_obs = a
                self.thinking = False
                self.turn_seat = 0
                last = self.human_obs.get("last_bid")
                if last:
                    self.qty = min(MAX_Q, int(last["qty"]) + 1)
                    self.face = int(last["face"])
                else:
                    self.qty, self.face = 3, 2
            elif kind == "thinking":
                self.thinking = True
                self.human_obs = None if not self.mode == "human" else self.human_obs
            elif kind == "action":
                seat, act = a, b
                self.thinking = False
                name = "你" if seat == 0 and self.mode == "human" else BY_KEY[self.sel[0] if self.mode == "human" else self.sel[seat]]["name"]
                self.log.append("%s：%s" % (name, action_text(act)))
                if act[0] == "call":
                    self.current_bid = "%s：%s" % (name, bid_cn(act[1], act[2]))
                    self.qty = max(3, min(MAX_Q, act[1] + 1))
                    self.face = act[2]
                else:
                    self.current_bid = "%s：开！" % name
                self.sfx.play("call" if act[0] == "call" else "open")
            elif kind == "game_over":
                self.on_game_over(a)
                self.result = a
                self.result_t0 = pygame.time.get_ticks()
                self.state = "result"
                self.sfx.play("win")
                self.session = None
            elif kind == "error":
                self.toast.show("出错：" + a, G.RED_L, 5000)
                self.session = None

    def start_game(self):
        self.paused = False
        self.speed = 0.5
        name = self.name_input.value.strip() or "你"
        if self.mode == "human":
            self.session = DiceSession(self.sel[0], name, special=int(self.special))
        else:
            self.session = DiceSession(self.sel[0], spectator=True,
                                       ai2_key=self.sel[1], special=int(self.special))
        self.session.speed = self.speed
        self.human_obs = None
        self.thinking = False
        self.current_bid = ""
        self.log = []
        self.result = None
        self.qty = 3
        self.face = 2
        self.state = "game"
        self.session.start()

    def on_game_over(self, rec):
        r = rec["result"]
        won = self.mode == "human" and r["winner_seat"] == 0
        challenge_win = bool(won and r.get("challenger_seat") == 0)
        five = len(r["hands"]["p0"]) == 5 and len(set(r["hands"]["p0"])) == 1
        opp = self.sel[0] if self.mode == "human" else None
        names = self.profile.record_game(
            won=won, opponent=opp, challenge_win=challenge_win,
            five_same=five, ai_only=(self.mode == "ai"))
        for nm in names:
            self.toast.show("成就解锁：" + nm, G.GOLD, 3200)

    def handle(self, event):
        if event.type == pygame.QUIT:
            if self.state == "game" and self.session:
                self.session.abort()
                self.session = None
            self.running = False
            return
        if self.state == "splash":
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self.state = "menu"
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if self.state == "game" and self.session:
                self.quit_game()
            elif self.state in ("game", "result", "select", "mode", "manual", "ach", "settings"):
                self.state = "menu"
                self.session = None
            else:
                self.running = False
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.state == "menu" and self.title_rect.collidepoint(event.pos):
                nm = self.profile.click_title()
                self.sfx.play("click")
                if nm:
                    self.toast.show("成就解锁：" + nm, G.GOLD, 3200)
                return
        for b in self.buttons:
            if b.handle(event):
                self.click(b.tag)
                return
        if self.state == "mode":
            self.name_input.handle(event)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for f, r in self.face_rects:
                if r.collidepoint(event.pos):
                    self.face = f
                    self.sfx.play("click")
                    return
        # 点击音乐区域
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if pygame.Rect(G.W - 170, 8, 160, 40).collidepoint(event.pos):
                self.music.toggle()
                self.profile.set_setting("music", self.music.enabled)

    def quit_game(self):
        """中途退出：先让会话把已发生的步骤写成未完成记录，再回菜单。"""
        if self.session:
            self.session.abort()
            self.session = None
        self.state = "menu"
        self.thinking = False
        self.toast.show("本局已保存为未完成", G.GOLD, 2800)

    def click(self, tag):
        self.sfx.play("click")
        if tag == "quit":
            self.running = False
        elif tag == "quit_game":
            self.quit_game()
        elif tag == "play":
            self.state = "mode"
        elif tag == "manual":
            self.state = "manual"; self.manual_page = 0
        elif tag == "ach":
            self.state = "ach"
        elif tag == "settings":
            self.state = "settings"
        elif tag == "back_menu":
            self.state = "menu"
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
            elif key == "special":
                self.special = not self.special
                self.profile.set_setting("special", self.special)
                self.toast.show("百搭模式：" + ("开" if self.special else "关") + "（下一局生效）")
        elif tag == "music_next":
            self.music.next_track()
            self.profile.set_setting_raw("music_track", self.music.index)
        elif tag == "music_mode":
            self.music.set_mode("single" if self.music.mode == "list" else "list")
            self.profile.set_setting_raw("music_mode", self.music.mode)
        elif isinstance(tag, tuple) and tag[0] == "vol":
            self.music.set_volume(self.music.volume + 0.05 * int(tag[1]))
            self.profile.set_setting_raw("music_volume", round(self.music.volume, 3))
        elif tag == "human":
            self.mode = "human"; self.sel = []; self.state = "select"
        elif tag == "ai":
            self.mode = "ai"; self.sel = []; self.state = "select"
        elif isinstance(tag, tuple) and tag[0] == "pick":
            k = tag[1]
            if self.mode == "human":
                self.sel = [k]
            else:
                if k in self.sel:
                    self.sel.remove(k)
                elif len(self.sel) < 2:
                    self.sel.append(k)
        elif tag == "back":
            self.state = "mode"
        elif tag == "start":
            if self.sel:
                self.start_game()
        elif tag == "call":
            if not self.human_obs:
                return
            st = self.human_obs
            if not is_valid_bid(self.qty, self.face, st["last_bid"]["qty"] if st["last_bid"] else 0,
                                st["last_bid"]["face"] if st["last_bid"] else 0,
                                not st["last_bid"], st["special_rule"]):
                self.toast.show("这手不合法：数量要更大，或同数量点数更大（1 算最大）", G.RED_L)
                return
            self.session.respond(("call", self.qty, self.face))
            self.human_obs = None
        elif tag == "open":
            if self.human_obs:
                self.session.respond(("challenge", 0, 0))
                self.human_obs = None
        elif tag == "q-":
            self.qty = max(1, self.qty - 1)
        elif tag == "q+":
            self.qty = min(MAX_Q, self.qty + 1)
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
            self.sel = self.sel if self.mode == "ai" else self.sel
            self.start_game()
        elif tag == "select":
            self.state = "select"

    def draw(self):
        if self.state == "splash":
            self.splash()
        elif self.state == "menu":
            self.menu()
        elif self.state == "mode":
            self.mode_select()
        elif self.state == "manual":
            self.manual()
        elif self.state == "ach":
            self.achievements()
        elif self.state == "settings":
            self.settings()
        elif self.state == "select":
            self.select()
        elif self.state == "game":
            self.draw_table()
        elif self.state == "result":
            self.draw_result()
        self.toast.draw(self.screen)

    def run(self):
        while self.running:
            if self.state == "splash" and pygame.time.get_ticks() - self.splash_start > 2200:
                self.state = "menu"
            for e in pygame.event.get():
                self.handle(e)
            self.pump()
            self.music.tick()
            self.draw()
            pygame.display.flip()
            self.clock.tick(60)
        pygame.quit()


def selftest(record_root, n_games=3):
    global RECORD_ROOT
    RECORD_ROOT = Path(record_root)
    root = Path(record_root)
    human_games = 1
    recs = []
    for i in range(n_games):
        if i < human_games:
            s = DiceSession("peak", "自测玩家", seed=2026111301 + i,
                            auto_human=True, selftest=True)
        elif i == 1:
            s = DiceSession("peak", spectator=True, ai2_key="bayes",
                            seed=2026111302 + i)
        else:
            s = DiceSession("baseline", spectator=True, ai2_key="memory",
                            seed=2026111303 + i, selftest=True)
        s.auto_human = (i < human_games)
        s.spectator = (i != 0)
        if i == 0:
            s.human_key, s.ai2_key = "peak", None
        elif i == 1:
            s.human_key, s.ai2_key = "peak", "bayes"
        else:
            s.human_key, s.ai2_key = "baseline", "memory"
        s._run()
        recs.append(s.rec)
    print("wrote", len(recs), "games to", root)
    return recs


def screenshot(out_dir):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    app = DiceApp(headless=True)
    app.state = "splash"; app.draw(); pygame.image.save(app.screen, str(out / "00_splash.png"))
    app.state = "menu"; app.draw(); pygame.image.save(app.screen, str(out / "01_menu.png"))
    app.state = "mode"; app.draw(); pygame.image.save(app.screen, str(out / "01b_mode.png"))
    app.state = "manual"; app.manual_page = 1; app.draw(); pygame.image.save(app.screen, str(out / "01c_manual.png"))
    app.state = "ach"; app.draw(); pygame.image.save(app.screen, str(out / "01d_ach.png"))
    app.state = "settings"; app.draw(); pygame.image.save(app.screen, str(out / "01e_settings.png"))
    app.state = "select"; app.mode = "human"; app.sel = ["peak"]; app.draw()
    pygame.image.save(app.screen, str(out / "02_select.png"))
    app.state = "game"; app.sel = ["peak"]; app.mode = "human"
    app.human_obs = {"own_dice": [2, 4, 6, 1, 5], "last_bid": {"qty": 3, "face": 4},
                     "history": [{"seat": 1, "qty": 3, "face": 4}], "special_rule": 1,
                     "seat": 0, "round": 2, "dice_total": 10}
    app.current_bid = "老江湖：叫 三 个 ④"
    app.log = ["老江湖：叫 三 个 ④"]
    app.draw(); pygame.image.save(app.screen, str(out / "03_game.png"))
    app.mode = "ai"; app.sel = ["peak", "bayes"]; app.state = "game"; app.human_obs = None
    app.draw(); pygame.image.save(app.screen, str(out / "03b_ai_game.png"))
    app.mode = "human"; app.sel = ["peak"]
    app.state = "result"
    app.result = {"result": {"winner_seat": 0, "winner_name": "你",
                             "reason": "开盅成功：叫牌不成立",
                             "final_bid": {"text": "叫 三 个 ④", "qty": 3, "face": 4},
                             "revealed_total": 2,
                             "hands": {"p0": [2, 4, 6, 1, 5], "p1": [3, 3, 2, 6, 1]}}}
    app.draw(); pygame.image.save(app.screen, str(out / "04_result.png"))
    pygame.quit()
    print("shots", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record-root", default=str(RECORD_ROOT))
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--screenshot", default="")
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest(a.record_root)
    elif a.screenshot:
        screenshot(a.screenshot)
    else:
        DiceApp(record_root=a.record_root, headless=a.headless).run()


if __name__ == "__main__":
    main()
