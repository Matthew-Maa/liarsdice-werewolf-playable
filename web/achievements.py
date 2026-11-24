# -*- coding: utf-8 -*-
"""成就定义与判定（网页版）。"""

DEFS = {
    "first_game": ("初次上桌", "完成第一局对局"),
    "first_win": ("第一滴血", "第一次获胜"),
    "win3": ("三连胜", "连续赢下三局"),
    "ten_games": ("常客", "累计完成 10 局"),
    "dice_five": ("五子同堂", "骰子局拿到五颗同点数"),
    "dice_open": ("开盅成功", "骰子局开盅成功"),
    "dice_bayes": ("读心者克星", "骰子局击败读心者"),
    "dice_win10": ("骰子十胜", "骰子累计 10 胜"),
    "wolf_wolf_win": ("狼王", "以狼人身份获胜"),
    "wolf_good_win": ("好人阵营", "以好人身份获胜"),
    "wolf_seer": ("验出狼", "预言家验到狼人"),
    "wolf_hunter": ("猎人带走", "猎人开枪带走狼人"),
    "wolf_witch": ("女巫救人", "女巫用解药救人"),
    "wolf_survive": ("活到最后", "获胜且自己存活到终局"),
    "wolf_win3": ("狼人三胜", "狼人杀累计 3 胜"),
}


def public_defs():
    return [{"code": c, "name": v[0], "desc": v[1]} for c, v in DEFS.items()]


def evaluate_dice(result, mode, opponent):
    out = []
    if mode != "human" or not result:
        return out
    hands = (result.get("hands") or {})
    p0 = hands.get("p0") or []
    if len(p0) == 5 and len(set(p0)) == 1:
        out.append("dice_five")
    if (result.get("winner_seat") == 0 and result.get("challenger_seat") == 0
            and str(result.get("reason", "")).startswith("开盅成功")):
        out.append("dice_open")
    if opponent == "bayes" and result.get("winner_seat") == 0:
        out.append("dice_bayes")
    return out


def evaluate_wolf(result, actions, human_pids):
    out = []
    if not result or not human_pids:
        return out
    reveal = result.get("reveal_after_game") or {}
    roles = reveal.get("roles") or {}
    players = {int(p["pid"]): p for p in (reveal.get("players") or [])}
    winner = result.get("winner")
    pid = int(human_pids[0])
    role = roles.get(str(pid))
    if not role:
        return out
    if role == "WOLF" and winner == "wolf":
        out.append("wolf_wolf_win")
    if role != "WOLF" and winner == "good":
        out.append("wolf_good_win")
    if role == "SEER":
        for a in actions or []:
            if a.get("seat") == pid and a.get("phase") == "night_seer":
                tgt = (a.get("action") or {}).get("target")
                if tgt is not None and roles.get(str(tgt)) == "WOLF":
                    out.append("wolf_seer")
                    break
    if role == "HUNTER":
        for a in actions or []:
            if a.get("seat") == pid and a.get("phase") == "hunter_shot":
                tgt = (a.get("action") or {}).get("target")
                if tgt is not None and roles.get(str(tgt)) == "WOLF":
                    out.append("wolf_hunter")
                    break
    if role == "WITCH":
        for a in actions or []:
            if a.get("seat") == pid and a.get("phase") == "night_witch":
                if (a.get("action") or {}).get("potion") == "SAVE":
                    out.append("wolf_witch")
                    break
    if player_alive(players, pid) and (
            (winner == "wolf" and role == "WOLF") or
            (winner == "good" and role != "WOLF")):
        out.append("wolf_survive")
    return out


def player_alive(players, pid):
    p = players.get(int(pid))
    return bool(p and p.get("alive"))
