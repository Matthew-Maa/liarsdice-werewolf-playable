# -*- coding: utf-8 -*-
"""校验 RA-027 对局记录（两个游戏共用）。

用法：
    py -3 校验记录.py 文件1.jsonl 文件2.jsonl ...
    py -3 校验记录.py 对局记录/liarsdice/2026-11-13.jsonl
退出码 0 = 全部通过。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "liarsdice_playable"))
import record_lib as RL  # noqa: E402


def check_common(r, errs):
    for e in RL.validate(r):
        errs.append(e)
    if r.get("game") not in ("liarsdice", "werewolf"):
        errs.append("未知 game=%r" % r.get("game"))
    if not isinstance(r.get("ts"), str) or "T" not in r["ts"]:
        errs.append("ts 不是 ISO 时间")
    for p in r.get("players") or []:
        if p.get("kind") == "human":
            if not p.get("name"):
                errs.append("human 缺昵称")
        elif p.get("kind") == "ai":
            for k in ("agent_id", "agent_version", "card", "level"):
                if not p.get(k):
                    errs.append("AI 座位 %s 缺 %s" % (p.get("seat"), k))
        else:
            errs.append("未知 kind=%r" % p.get("kind"))
    for i, a in enumerate(r.get("actions") or []):
        for k in ("t", "seat", "phase", "observation", "action", "code", "text"):
            if k not in a:
                errs.append("actions[%d] 缺 %s" % (i, k))
        if a.get("t") != i:
            errs.append("actions[%d].t != 下标" % i)
        if not a.get("text"):
            errs.append("actions[%d] text 为空" % i)


def check_dice(r, errs):
    for i, a in enumerate(r.get("actions") or []):
        obs = a.get("observation") or {}
        if "own_dice" not in obs or len(obs["own_dice"]) != 5:
            errs.append("actions[%d] 骰子观测不是 5 颗自己的骰子" % i)
        if "opp_dice" in obs or "hands" in obs:
            errs.append("actions[%d] 骰子观测疑似上帝视角" % i)
        act = a.get("action") or {}
        if act.get("type") not in ("CALL", "OPEN"):
            errs.append("actions[%d] 未知骰子动作" % i)
        if act.get("type") == "CALL" and not ("qty" in act and "face" in act):
            errs.append("actions[%d] CALL 缺 qty/face" % i)
    res = r.get("result") or {}
    for k in ("winner_seat", "reason", "final_bid", "hands"):
        if k not in res:
            errs.append("result 缺 %s" % k)


def check_werewolf(r, errs):
    for i, a in enumerate(r.get("actions") or []):
        obs = a.get("observation") or {}
        if "roles" in obs:
            errs.append("actions[%d] 观测里有全场 roles（上帝视角）" % i)
        role = obs.get("my_role")
        if role != "WOLF" and obs.get("teammates"):
            errs.append("actions[%d] 非狼却看到队友" % i)
        if role != "SEER" and "seer_results" in obs:
            errs.append("actions[%d] 非预言家却看到验人结果" % i)
        if role != "WITCH" and "witch" in obs:
            errs.append("actions[%d] 非女巫却看到药" % i)
        if a.get("phase") == "day_speech":
            act = a.get("action") or {}
            if act.get("type") not in ("SILENCE", "SELF_CLAIM", "ACCUSE"):
                errs.append("actions[%d] 发言动作不合法" % i)
            if not isinstance(a.get("code"), int):
                errs.append("actions[%d] 发言缺整数编码" % i)
            if not a.get("text"):
                errs.append("actions[%d] 发言缺文本" % i)
    # 夜序：同一天的 night_wolf 全部在 night_witch 之前，witch 在 seer 之前
    by_day = {}
    for i, a in enumerate(r.get("actions") or []):
        if a.get("phase") in ("night_wolf", "night_witch", "night_seer"):
            by_day.setdefault(a.get("day"), {}).setdefault(a["phase"], []).append(i)
    for day, d in by_day.items():
        if "night_wolf" in d and "night_witch" in d and max(d["night_wolf"]) > min(d["night_witch"]):
            errs.append("第%s天夜序错：狼人不在女巫前" % day)
        if "night_witch" in d and "night_seer" in d and max(d["night_witch"]) > min(d["night_seer"]):
            errs.append("第%s天夜序错：女巫不在预言家前" % day)
    res = r.get("result") or {}
    if "reveal_after_game" not in res or "roles" not in res["reveal_after_game"]:
        errs.append("result 缺赛后身份公开")


def main(paths):
    bad = 0
    for path in paths:
        n = 0
        for line_no, line in enumerate(open(path, encoding="utf-8"), 1):
            line = line.strip()
            if not line:
                continue
            n += 1
            r = json.loads(line)
            errs = []
            check_common(r, errs)
            if r.get("game") == "liarsdice":
                check_dice(r, errs)
            elif r.get("game") == "werewolf":
                check_werewolf(r, errs)
            if errs:
                bad += 1
                print("[FAIL] %s 第 %d 行：%s" % (path, line_no, "；".join(errs[:8])))
            else:
                print("[PASS] %s 第 %d 行：game=%s mode=%s actions=%d seed=%s"
                      % (path, line_no, r["game"], r["mode"], len(r["actions"]), r["seed"]))
        print("---- %s：%d 局" % (path, n))
    if bad:
        print("总计 %d 行失败" % bad)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
