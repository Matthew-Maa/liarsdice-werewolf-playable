# -*- coding: utf-8 -*-
"""RA-027 统一对局记录写入器（骰子版/狼人杀版内容相同）。

格式见 ../记录格式说明.md。一行一局，UTF-8，追加写，写完立即 flush+fsync。
"""
import json
import os
from datetime import datetime

SCHEMA = "ra027.game_record.v1"


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def record_path(record_root, game, when=None):
    day = (when or datetime.now()).strftime("%Y-%m-%d")
    d = os.path.join(record_root, game)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, day + ".jsonl")


def write_record(rec, record_root, game, when=None):
    """追加写一局。返回写入的文件路径。"""
    path = record_path(record_root, game, when)
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())
    return path


def make_record(game, seed, mode, players, actions, result, meta=None,
                selftest=False, model_info=None):
    rec = {
        "schema": SCHEMA,
        "ts": now_iso(),
        "game": game,
        "seed": int(seed),
        "mode": mode,                 # human_vs_ai / ai_vs_ai / mixed
        "players": players,
        "actions": actions,           # 每步 = 观测 + 动作
        "result": result,
        "meta": meta or {},
    }
    if model_info:
        # LRS_076：狼人杀记录要写清用的哪档 AI；骰子不使用这几个键
        for k in ("model_profile", "model_version", "model_cards"):
            if k in model_info:
                rec[k] = model_info[k]
        if "model_note" in model_info:
            rec["meta"]["model_note"] = model_info["model_note"]
    if selftest:
        rec["meta"]["selftest"] = True
    return rec


def player_entry(seat, kind, name, agent_id=None, agent_version=None,
                 card=None, level=None):
    """players[] 的统一小工厂。人类只写昵称；AI 写模型标识+版本+卡+水平。"""
    e = {"seat": int(seat), "kind": kind, "name": name}
    if kind == "ai":
        e["agent_id"] = agent_id
        e["agent_version"] = agent_version
        e["card"] = card
        e["level"] = level
    return e


def validate(rec):
    """最小结构校验，返回问题列表（空 = 通过）。"""
    errs = []
    for k in ("schema", "ts", "game", "seed", "mode", "players", "actions",
              "result"):
        if k not in rec:
            errs.append("缺少顶层字段 " + k)
    if rec.get("schema") != SCHEMA:
        errs.append("schema 不是 " + SCHEMA)
    if not isinstance(rec.get("players"), list) or not rec["players"]:
        errs.append("players 必须是非空列表")
    acts = rec.get("actions")
    aborted = bool((rec.get("result") or {}).get("aborted"))
    if not isinstance(acts, list) or (not acts and not aborted):
        errs.append("actions 必须是非空列表（中途退出的记录可为空）")
    for i, a in enumerate(rec.get("actions") or []):
        if "observation" not in a or "action" not in a:
            errs.append("actions[%d] 缺 observation/action" % i)
    return errs
