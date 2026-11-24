# -*- coding: utf-8 -*-
"""吹牛骰子 · 可玩版（RA-027）
人机对战 / AI 内战旁观，自动写统一 JSONL 对局记录。

运行：python play.py
一键：双击 start.bat
"""
import argparse
import copy
import os
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from vendor.liarsdice.game.env import LiarDiceEnv          # noqa: E402
from vendor.liarsdice.game.logic import is_valid_bid       # noqa: E402
import agents as AG                                         # noqa: E402
import record_lib as RL                                     # noqa: E402

GAME = "liarsdice"
DICE_PER_PLAYER = 5
SPECIAL_RULE = 1          # 1 为百搭（与参考实现 special 线一致）
DICE_TOTAL = DICE_PER_PLAYER * 2
MAX_QTY = DICE_TOTAL

FACES = {1: "①", 2: "②", 3: "③", 4: "④", 5: "⑤", 6: "⑥"}
CN_NUM = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六",
          7: "七", 8: "八", 9: "九", 10: "十"}


def clear():
    if os.environ.get("RA_NO_CLEAR"):
        return
    os.system("cls" if os.name == "nt" else "clear")


def hr(ch="─", n=58):
    print(ch * n)


def dice_text(hand):
    return "  ".join(FACES[d] for d in hand)


def bid_text(qty, face):
    return "叫 %s 个 %s" % (CN_NUM.get(qty, qty), FACES.get(face, face))


def action_text(act):
    if act[0] == "challenge":
        return "开！"
    return bid_text(act[1], act[2])


def banner():
    print()
    hr("═")
    print("        吹 牛 骰 子 · 研 究 对 手 版")
    print("        每人 5 颗 · 1 是百搭 · 开盅见真章")
    hr("═")
    print()


def show_roster():
    print("可选对手：")
    for i, key in enumerate(("baseline", "peak", "memory", "bayes"), 1):
        info = AG.AI_INFO[key]
        print("  %d. %-14s  %s" % (i, info["name"], info["level"]))
        print("     卡：%s" % info["card"])
        print("     %s" % info["desc"])
        print()


def pick_ai(prompt="选对手"):
    keys = ("baseline", "peak", "memory", "bayes")
    show_roster()
    while True:
        raw = input("%s（1-4，回车=1）：" % prompt).strip()
        if raw == "":
            return "baseline", "whole"
        if raw in ("1", "2", "3", "4"):
            key = keys[int(raw) - 1]
            tier = "whole"
            if key == "memory":
                print()
                print("记忆版有三档（原卡叫四档，实际 mid 与 long 等价，只有三档）：")
                print("  1. 不看历史（off）")
                print("  2. 只看上一局（short）")
                print("  3. 看整场（whole；原卡 mid/long 合并）")
                t = input("选记忆档（1-3，回车=3）：").strip()
                tier = {"1": "off", "2": "short", "3": "whole"}.get(t, "whole")
            return key, tier
        print("请输入 1-4。")


# ==================== 人类玩家 ====================

class HumanAgent:
    """控制台人类玩家。只拿得到自己的骰子和公开叫牌历史（状态由环境给）。"""

    def __init__(self, name="玩家"):
        self.name = name
        self.human = True
        self.key = "human"

    def start_game(self):
        pass

    def on_challenge_result(self, bid_qty, actual_count):
        pass

    def on_dice_revealed(self, payload, seat):
        pass

    def decide(self, state):
        print()
        hr()
        print("你的骰子： %s" % dice_text(state["own_dice"]))
        hist = state.get("history") or []
        if hist:
            print("叫牌历史：" + " → ".join(
                ("你" if by else "对手") + " " + bid_text(q, f) for q, f, by in hist))
        print()
        if state["first_turn"]:
            print("你是先手。先手至少叫 3 个（叫 1 点则至少 2 个）。")
        else:
            print("上一手：%s" % bid_text(state["last_qty"], state["last_face"]))
            print("你可以：1) 加叫   2) 开盅")
        while True:
            if state["first_turn"]:
                cmd = input("按回车开始叫牌，或输入 q 退出：").strip().lower()
                if cmd == "q":
                    raise SystemExit("已退出。")
                choice = "1"
            else:
                choice = input("选择（1=加叫，2=开盅）：").strip()
            if choice == "2" and not state["first_turn"]:
                return ("challenge", 0, 0)
            if choice not in ("1", "2"):
                print("请输入 1 或 2。")
                continue
            try:
                qty = int(input("叫几个？（1-%d）：" % MAX_QTY).strip())
                face = int(input("叫几点？（1-6）：").strip())
            except ValueError:
                print("要输入数字。")
                continue
            if qty < 1 or qty > MAX_QTY or face < 1 or face > 6:
                print("范围不对：数量 1-%d，点数 1-6。" % MAX_QTY)
                continue
            if not is_valid_bid(qty, face, state["last_qty"], state["last_face"],
                                state["first_turn"], state["special_rule"]):
                print("这手不合法：要么数量更大，要么数量相同、点数更大（1 点算最大）。")
                continue
            return ("call", qty, face)


class ScriptedHuman(HumanAgent):
    """自测用：不读键盘，按简单启发式代打（记录里标 selftest=true）。"""

    def __init__(self, name="自测玩家"):
        super().__init__(name)
        self.rng = random.Random(20261113)

    def decide(self, state):
        own = state["own_dice"]
        st = [0] * 7
        for d in own:
            st[d] += 1
        best = max(range(2, 7), key=lambda f: st[f] + st[1])
        if state["first_turn"]:
            return ("call", max(3, st[best] + 1), best)
        need = state["last_qty"] - (st[state["last_face"]] + st[1])
        if need > 2 or self.rng.random() < 0.18:
            return ("challenge", 0, 0)
        q, f = state["last_qty"] + 1, best if best > state["last_face"] else state["last_face"]
        while not is_valid_bid(q, f, state["last_qty"], state["last_face"], 0,
                               state["special_rule"]):
            q += 1
            if q > MAX_QTY:
                return ("challenge", 0, 0)
        return ("call", q, f)


# ==================== 一局 ====================

class Proxy:
    """透明转发，额外捕获开盅结果（手牌/总数）。"""

    def __init__(self, real):
        self.real = real
        self.name = getattr(real, "name", real.__class__.__name__)
        self.human = getattr(real, "human", False)
        self.key = getattr(real, "key", "ai")
        self.revealed = None
        self.total = None

    def start_game(self):
        f = getattr(self.real, "start_game", None)
        if f:
            f()

    def decide(self, state):
        return self.real.decide(state)

    def on_challenge_result(self, bid_qty, actual_count):
        self.total = int(actual_count)
        self.real.on_challenge_result(bid_qty, actual_count)

    def on_dice_revealed(self, payload, seat):
        self.revealed = payload
        f = getattr(self.real, "on_dice_revealed", None)
        if f:
            f(payload, seat)


def play_one(ai_list, player_entries, seed, record_root, mode, selftest=False,
             verbose=True, delay=0.0):
    random.seed(int(seed))
    for ai in ai_list:
        ai.start_game()

    env = LiarDiceEnv(ai_list[0], ai_list[1], dice_num=DICE_PER_PLAYER,
                      special_rule=SPECIAL_RULE)
    env.reject_log = []
    names = [p["name"] for p in player_entries]
    actions = []
    history_abs = []
    state_holder = {"last_bid": None, "challenger": None}

    def on_decision(turn, state, action):
        obs = {
            "seat": int(turn),
            "round": len(actions) + 1,
            "own_dice": list(state["own_dice"]),
            "dice_total": DICE_TOTAL,
            "special_rule": int(SPECIAL_RULE),
            "history": [{"seat": s, "qty": q, "face": f} for q, f, s in history_abs],
            "last_bid": (None if not history_abs else
                         {"seat": history_abs[-1][2],
                          "qty": history_abs[-1][0],
                          "face": history_abs[-1][1]}),
            "visible": "只有自己的骰子 + 公开叫牌历史（非上帝视角）",
        }
        if action[0] == "call":
            act = {"type": "CALL", "qty": int(action[1]), "face": int(action[2])}
            code = "CALL:%d:%d" % (action[1], action[2])
            text = bid_text(action[1], action[2])
            history_abs.append((int(action[1]), int(action[2]), int(turn)))
            state_holder["last_bid"] = {"seat": int(turn), "qty": int(action[1]),
                                        "face": int(action[2])}
        else:
            act = {"type": "OPEN"}
            code = "OPEN"
            text = "开！"
            state_holder["challenger"] = int(turn)
        actions.append({"t": len(actions), "seat": int(turn),
                        "phase": ("bidding" if action[0] == "call" else "challenge"),
                        "observation": obs, "action": act,
                        "code": code, "text": text})
        if verbose:
            print("  %-12s %s" % (names[turn], action_text(action)))
        if delay:
            time.sleep(delay)

    winner = env.play_one_game(on_decision=on_decision)
    challenger = state_holder["challenger"]
    caller = 1 - challenger
    bid = state_holder["last_bid"] or {"qty": 0, "face": 0}
    total = ai_list[0].total if ai_list[0].total is not None else ai_list[1].total
    payload = ai_list[0].revealed or ai_list[1].revealed or {}
    reason = ("开盅成功：叫牌不成立" if winner == challenger
              else "开盅失败：叫牌成立")
    result = {
        "winner_seat": int(winner),
        "winner_name": names[winner],
        "winner_side": "p%d" % winner,
        "loser_seat": 1 - int(winner),
        "reason": reason,
        "challenger_seat": int(challenger),
        "caller_seat": int(caller),
        "final_bid": {"qty": int(bid["qty"]), "face": int(bid["face"]),
                      "text": bid_text(bid["qty"], bid["face"])},
        "revealed_total": total,
        "hands": {"p0": payload.get("dice", [[], []])[0],
                  "p1": payload.get("dice", [[], []])[1]},
        "rounds": len(actions),
    }
    meta = {
        "rules": {"dice_per_player": DICE_PER_PLAYER,
                  "special_rule": SPECIAL_RULE,
                  "special_rule_cn": "1 是百搭（叫过 1 之后百搭失效）",
                  "dice_total": DICE_TOTAL},
        "note": "seed 可复现发牌与先手；AI 跨局记忆会改变后续决策",
        "rejected_calls": list(env.reject_log or []),
    }
    rec = RL.make_record(GAME, seed, mode, player_entries, actions, result,
                         meta=meta, selftest=selftest)
    errs = RL.validate(rec)
    if errs:
        raise RuntimeError("记录结构不合法：" + "; ".join(errs))
    path = RL.write_record(rec, record_root, GAME)
    return rec, path


def make_players(human_name, ai_key, ai_tier):
    """人类固定坐 0 号位；返回 (ai列表, players元信息)。"""
    human = HumanAgent(human_name or "玩家")
    ai = AG.make_ai(ai_key, ai_tier)
    info = AG.AI_INFO[ai_key]
    name = info["name"] + ("（%s）" % {"off": "不看历史", "short": "只看上一局",
                                      "whole": "看整场"}[ai_tier]
                           if ai_key == "memory" else "")
    players = [
        RL.player_entry(0, "human", human.name),
        RL.player_entry(1, "ai", name, agent_id=info["agent_id"],
                        agent_version=info["agent_version"] + (
                            "-" + ai_tier if ai_key == "memory" else ""),
                        card=info["card"], level=info["level"]),
    ]
    return [Proxy(human), Proxy(ai)], players


def ai_vs_ai(key1, tier1, key2, tier2, n, seed0, record_root, delay=0.0,
             selftest=False):
    ai1 = AG.make_ai(key1, tier1)
    ai2 = AG.make_ai(key2, tier2)
    i1, i2 = AG.AI_INFO[key1], AG.AI_INFO[key2]
    v1 = i1["agent_version"] + ("-" + tier1 if key1 == "memory" else "")
    v2 = i2["agent_version"] + ("-" + tier2 if key2 == "memory" else "")
    players = [
        RL.player_entry(0, "ai", i1["name"], agent_id=i1["agent_id"],
                        agent_version=v1, card=i1["card"],
                        level=i1["level"]),
        RL.player_entry(1, "ai", i2["name"], agent_id=i2["agent_id"],
                        agent_version=v2, card=i2["card"],
                        level=i2["level"]),
    ]
    wins = [0, 0]
    paths = []
    for k in range(n):
        seed = seed0 + k
        print("\n── 第 %d/%d 局 · seed=%d ──" % (k + 1, n, seed))
        rec, path = play_one([Proxy(ai1), Proxy(ai2)], players, seed,
                             record_root, "ai_vs_ai", verbose=True,
                             delay=delay, selftest=selftest)
        wins[rec["result"]["winner_seat"]] += 1
        paths.append(path)
        print("  结果：%s 胜（%s）" % (rec["result"]["winner_name"],
                                     rec["result"]["reason"]))
    print("\nAI 内战完成：%s %d : %d %s" % (i1["name"], wins[0], wins[1], i2["name"]))
    print("记录已写入：%s" % paths[-1])
    return wins


def selftest(record_root):
    print("=== 自测 1/3：人机对战（键盘输入用脚本代打，记录标 selftest）===")
    human = ScriptedHuman("自测玩家")
    ai = AG.make_ai("peak")
    info = AG.AI_INFO["peak"]
    players = [
        RL.player_entry(0, "human", human.name),
        RL.player_entry(1, "ai", info["name"], agent_id=info["agent_id"],
                        agent_version=info["agent_version"], card=info["card"],
                        level=info["level"]),
    ]
    rec1, p1 = play_one([Proxy(human), Proxy(ai)], players, 2026111301,
                        record_root, "human_vs_ai", selftest=True, verbose=True)
    print("  写入：%s" % p1)

    print("\n=== 自测 2/3：AI 内战（峰顶 vs 贝叶斯）===")
    ai_vs_ai("peak", "whole", "bayes", "whole", 1, 2026111302,
             record_root, delay=0.0, selftest=True)

    print("\n=== 自测 3/3：AI 内战（基线 vs 记事本，凑 3 行样本）===")
    ai_vs_ai("baseline", "whole", "memory", "whole", 1, 2026111303,
             record_root, delay=0.0, selftest=True)
    print("\n自测完成。记录根目录：%s" % record_root)
    return p1


# ==================== 入口 ====================

def main():
    ap = argparse.ArgumentParser(description="吹牛骰子·可玩版")
    ap.add_argument("--record-root", default=str(HERE.parent / "对局记录"))
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--ai-vs-ai", type=int, default=0, metavar="N")
    ap.add_argument("--p1", default="peak")
    ap.add_argument("--p2", default="bayes")
    ap.add_argument("--tier1", default="whole")
    ap.add_argument("--tier2", default="whole")
    ap.add_argument("--seed", type=int, default=20261113)
    ap.add_argument("--delay", type=float, default=0.7)
    args = ap.parse_args()

    if args.selftest:
        selftest(args.record_root)
        return
    if args.ai_vs_ai:
        ai_vs_ai(args.p1, args.tier1, args.p2, args.tier2, args.ai_vs_ai,
                 args.seed, args.record_root, delay=args.delay)
        return

    while True:
        clear()
        banner()
        print("  1. 人机对战")
        print("  2. AI 内战旁观（自动打、自动攒记录）")
        print("  3. 看四个 AI 的来历")
        print("  4. 退出")
        print()
        choice = input("选一个（1-4）：").strip()
        if choice == "4" or choice.lower() == "q":
            print("再见。")
            return
        if choice == "3":
            clear()
            show_roster()
            input("按回车返回……")
            continue
        if choice == "2":
            clear()
            print("AI 内战：选两位选手。\n")
            print("—— 先选 1 号位 ——")
            k1, t1 = pick_ai("1 号位")
            print("\n—— 再选 2 号位 ——")
            k2, t2 = pick_ai("2 号位")
            try:
                n = int(input("\n打几局？（回车=5）：").strip() or "5")
            except ValueError:
                n = 5
            ai_vs_ai(k1, t1, k2, t2, max(1, n), args.seed, args.record_root,
                     delay=args.delay)
            input("\n按回车返回主菜单……")
            continue
        if choice == "1":
            clear()
            print("人机对战：你坐 0 号位，先手随机。\n")
            name = input("你的昵称（回车=玩家）：").strip() or "玩家"
            key, tier = pick_ai("选对手")
            ais, players = make_players(name, key, tier)
            print("\n对手：%s（%s）" % (players[1]["name"], players[1]["level"]))
            print("规则：每人 5 颗，1 是百搭；开盅后数全场该点数（1 也算）。")
            input("按回车发牌……")
            rec, path = play_one(ais, players, args.seed, args.record_root,
                                 "human_vs_ai", verbose=True)
            print()
            hr("═")
            r = rec["result"]
            print("结果：%s 获胜 —— %s" % (r["winner_name"], r["reason"]))
            print("最后一手：%s；全场实际 %s 个" % (
                r["final_bid"]["text"], r["revealed_total"]))
            print("你的骰子：%s    对手：%s" % (
                dice_text(r["hands"]["p0"]), dice_text(r["hands"]["p1"])))
            hr("═")
            print("本局已记录：%s" % path)
            input("按回车返回主菜单……")
            continue
        print("请输入 1-4。")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n已退出。")
