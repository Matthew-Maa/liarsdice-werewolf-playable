# -*- coding: utf-8 -*-
"""狼人杀 · 可玩版（RA-027）
板子：狼2 / 民2 / 女巫1 / 预言家1 / 猎人1（7 人）
夜序：狼人 → 女巫 → 预言家；白天：发言（沉默/我是X/我认为X是Y）→ 投票（可弃票）

运行：python play.py
一键：双击 start.bat
"""
import argparse
import copy
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from vendor.werewolf import config as C                      # noqa: E402
from vendor.werewolf.game.env import WerewolfEnv             # noqa: E402
from vendor.werewolf.game import actions as A                # noqa: E402
from vendor.werewolf.game.roles import role_cn, faction_cn  # noqa: E402
import agents as AG                                          # noqa: E402
import record_lib as RL                                      # noqa: E402
import env_multi as EM                                       # noqa: E402  LRS_084

GAME = "werewolf"

# LRS_076：当前 AI 档。默认启发式；研究模型默认 symmetric_q_only。
AI_CONFIG = {"mode": "heuristic", "profile": "symmetric_q_only"}
MODEL_PROFILE_ORDER = ("symmetric_q_only", "auto_hybrid", "good_three_piece",
                       "q_seer_fair", "wolf_kit_experimental")

RULE_SUMMARY = {
    "board": {r: n for r, n in C.BOARD},
    "n_players": C.board_size(),
    "win_mode": C.WIN_MODE,
    "win_mode_cn": "狼人全灭→好人胜；狼人存活数≥好人存活数→狼人胜（神职算好人）",
    "night_order": list(C.NIGHT_ORDER),
    "speech_actions": "SILENCE / SELF_CLAIM(role) / ACCUSE(target, role)",
    "vote": "VOTE(target) 或 ABSTAIN（平票无人出局）",
    "hunter": "被刀/被票出可开枪；被毒不开枪",
    "seer": "验人结果只给预言家本人",
}


def clear():
    AG.clear()


def hr(ch="─", n=62):
    print(ch * n)


def banner():
    print()
    hr("═")
    print("        狼 人 杀 · 研 究 A I 版")
    print("        7 人板：狼2 民2 女巫1 预言家1 猎人1")
    hr("═")
    print()


def show_versions():
    if AI_CONFIG["mode"] == "model":
        prof = AI_CONFIG["profile"]
        meta = AG.MODEL_META[prof]
        print("这桌用的 AI：研究模型（LRS_075 接口包）")
        print("  profile：%s" % prof)
        print("  模型：%s" % meta["cards"])
        print("  版本：%s" % meta["version"])
        print("  说明：%s" % meta["level"])
        if meta.get("warning"):
            print("  ⚠ " + meta["warning"])
        print("  ★ 游戏胜率不是研究结果；研究数字看 LRS_069/070。")
    else:
        print("这桌用的 AI（全部来自狼人杀线 v0，没有训练 ckpt，是启发式）：")
        print("  好人 AI：HeuristicAgent（baseline 3）")
        print("          发言加权：投『被指认是狼次数最多』和『最爱沉默』的人。")
        print("  狼人 AI：1 只说谎狼（LyingWolfAgent，baseline 4）")
        print("          冒充预言家、诬陷好人，发言和投票保持一致。")
        print("          另 1 只按沉默狼打（启发式在狼人身份下不发言）。")
        print("          说谎预算 lie_prob=0.55（原版 1.0 会让好人几乎必败；")
        print("          0.55 时 120 局实测好人约 49%，是为了可玩性调参）。")
        print("  角色由 seed 随机分配；AI 看到自己是狼就走狼 AI，否则走好人 AI。")
    print()
    print("规则：")
    print("  · 夜序：狼人 → 女巫 → 预言家；女巫解/毒各一瓶，同夜只能用一个，不能自救。")
    print("  · 白天发言只有三种：沉默 / 我是X / 我认为X是Y（与训练环境同一套动作编码）。")
    print("  · 投票可以弃票；平票无人出局。")
    print("  · 猎人被刀/被票出可以开枪；被毒死不能开枪。")
    print("  · 胜负：狼全灭好人胜；狼人存活数 ≥ 好人存活数 狼人胜。")
    print()

def seat_names(n_humans, human_names):
    names = []
    for pid in range(C.board_size()):
        if pid < n_humans:
            names.append(human_names[pid])
        else:
            names.append("AI助手%d" % pid)
    return names


def make_players(n_humans, names, n_liars=1, lie_prob=0.55,
                 ai_mode="heuristic", model_profile="symmetric_q_only"):
    out = []
    for pid, name in enumerate(names):
        if pid < n_humans:
            out.append(RL.player_entry(pid, "human", name))
        elif ai_mode == "model":
            meta = AG.MODEL_META[model_profile]
            e = RL.player_entry(pid, "ai", name,
                                agent_id=meta["agent_id"],
                                agent_version=meta["version"],
                                card=meta["cards"],
                                level=meta["level"])
            e["model_profile"] = model_profile
            e["model_cards"] = meta["cards"]
            out.append(e)
        else:
            ver = ("good=heuristic v0; wolf=lying_wolf v0 "
                   "(lie_prob=%.2f, %d只) + heuristic v0 沉默狼 (%d只)"
                   % (lie_prob, n_liars, max(0, 2 - n_liars)))
            e = RL.player_entry(pid, "ai", name,
                                agent_id=AG.AI_META["agent_id"],
                                agent_version=ver,
                                card=AG.AI_META["card"],
                                level=AG.AI_META["level"])
            e["wolf_agent"] = "lying_wolf v0（冒充预言家 + 诬陷）"
            e["good_agent"] = "heuristic v0（发言加权）"
            out.append(e)
    return out

def play_game(n_humans, names, seed, record_root, mode, selftest=False,
              delay=0.0, verbose=True, n_liars=1, lie_prob=0.55,
              ai_mode="heuristic", model_profile="symmetric_q_only",
              speech_mode="single"):
    records = []
    table = AG.Table(names, n_humans)
    table.delay = delay
    table.quiet = not verbose
    roles = AG.roles_for_seed(seed) if ai_mode == "model" else None
    reals = AG.make_agents(n_humans, names, scripted=selftest and n_humans > 0,
                           seed=seed, n_liars=n_liars, lie_prob=lie_prob,
                           ai_mode=ai_mode, model_profile=model_profile,
                           roles=roles)
    wrappers = [AG.RecordedAgent(a, table, records) for a in reals]
    if speech_mode == "multi":
        # LRS_084：实验档只给「纯 AI」用；CLI 没有攒序列的界面，真人局请用 GUI。
        if n_humans > 0:
            raise ValueError("multi 档在 CLI 下只支持纯 AI；真人多发言请用 GUI 实验档")
        # ★ SpeakingAgent 必须在**外层**：MultiSpeechEnv 要读它的 said/end；
        #   RecordedAgent 在内层，保证每条发言都单独进记录。
        wrappers = [EM.SpeakingAgent(w) for w in wrappers]
        env = EM.MultiSpeechEnv(wrappers, seed=seed, record=True,
                                cell="ai_all", roles=list(roles) if roles else None,
                                speech_mode="multi")
    else:
        env = WerewolfEnv(wrappers, seed=seed, record=True,
                          cell=("mixed_%dh" % n_humans) if n_humans else "ai_all",
                          roles=list(roles) if roles else None)
    if verbose:
        print("开始：seed=%d，%d 名人类 + %d 个 AI" % (
            seed, n_humans, C.board_size() - n_humans))
        if ai_mode == "model":
            meta = AG.MODEL_META[model_profile]
            print("AI 档：研究模型 / %s（%s）" % (model_profile, meta["cards"]))
            if meta.get("warning"):
                print("⚠ " + meta["warning"])
        hr()
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
        "reveal_after_game": {
            "roles": raw["roles"],
            "players": raw["players"],
        },
        "deaths": [e for e in raw["events"] if e.get("kind") == "death"],
    }
    if speech_mode == "multi":
        result["speech_mode"] = "multi"
        result["n_speech"] = len(env.speech_log)
    if ai_mode == "model":
        m_meta = AG.MODEL_META[model_profile]
        model_info = {
            "model_profile": model_profile,
            "model_version": m_meta["version"],
            "model_cards": m_meta["cards"],
            "model_note": "游戏胜率不是研究结果；研究口径见 LRS_069/070。",
        }
        meta_ai_engine = "research_model"
    else:
        model_info = {
            "model_profile": "heuristic",
            "model_version": "baseline_v0",
            "model_cards": "baseline 3/4（heuristic + lying_wolf）",
            "model_note": "启发式 AI，非研究模型。",
        }
        meta_ai_engine = "heuristic"
    players = make_players(n_humans, names, n_liars, lie_prob,
                           ai_mode=ai_mode, model_profile=model_profile)
    meta = {
        "rules": RULE_SUMMARY,
        "ai_engine": meta_ai_engine,
        "agents": {str(p["seat"]): p["name"] for p in players},
        "wolf_policy": {"n_liars": n_liars, "lie_prob": lie_prob},
        "note": "observation 只含该座位可见信息；roles 只在 result.reveal_after_game 里公开",
        "seed_note": "seed 决定角色分配与各座位 AI 随机流；人类输入不参与复现",
        "model_profile": model_info["model_profile"],
        "model_version": model_info["model_version"],
        "model_cards": model_info["model_cards"],
    }
    if speech_mode == "multi":
        meta["speech_mode"] = "multi"
        meta["speech_rule"] = ("每天每座最多 1 条自报 + 2 条指认；"
                               "不重复目标、不指自己/死者；空序列=沉默")
        meta["speech_warning"] = ("多发言实验档：研究模型未适配，仅启发式/纯 AI；"
                                  "不得据此说多发言有效")
        seq = {}
        for day, pid, order, code in env.speech_log:
            seq.setdefault(str(day), {}).setdefault(str(pid), []).append(code)
        meta["speech_seq"] = seq
    if ai_mode == "model":
        meta["model_warning"] = AG.MODEL_META[model_profile].get("warning")
    rec = RL.make_record(GAME, seed, mode, players, records, result,
                         meta=meta, selftest=selftest, model_info=model_info)
    errs = RL.validate(rec)
    if errs:
        raise RuntimeError("记录结构不合法：" + "; ".join(errs))
    path = RL.write_record(rec, record_root, GAME)
    if verbose:
        print()
        hr("═")
        print("结果：%s 胜（%s），共 %d 天" % (
            result["winner_cn"], result["reason"], result["days"]))
        print("身份公开：" + "；".join(
            "%d号=%s" % (int(pid), role_cn(r)) for pid, r in sorted(
                raw["roles"].items(), key=lambda x: int(x[0]))))
        hr("═")
        print("本局已记录：%s" % path)
    return rec, path

def ai_vs_ai(n_games, seed0, record_root, delay=0.5, n_liars=1, lie_prob=0.55,
             ai_mode="heuristic", model_profile="symmetric_q_only",
             speech_mode="single"):
    for k in range(n_games):
        clear()
        banner()
        print("纯 AI 旁观：第 %d/%d 局" % (k + 1, n_games))
        if ai_mode == "model":
            meta = AG.MODEL_META[model_profile]
            print("研究模型档：%s（%s）" % (model_profile, meta["cards"]))
            if meta.get("warning"):
                print("⚠ " + meta["warning"])
        print("（放着跑，每局自动写记录；Ctrl+C 可停）")
        names = seat_names(0, [])
        rec, path = play_game(0, names, seed0 + k, record_root, "ai_vs_ai",
                              delay=delay, n_liars=n_liars, lie_prob=lie_prob,
                              ai_mode=ai_mode, model_profile=model_profile,
                              speech_mode=speech_mode)
        if k + 1 < n_games:
            time.sleep(0.6)


def model_selftest(n_games, seed0, record_root, model_profile="symmetric_q_only"):
    """纯 AI 跑 n 局研究模型；最后 3 行写样本并过校验器。"""
    root = os.path.join(record_root, "_model_selftest")
    t0 = time.time()
    rows = []
    paths = []
    for k in range(n_games):
        rec, path = play_game(0, seat_names(0, []), seed0 + k, root,
                              "ai_vs_ai", selftest=True, delay=0.0,
                              verbose=False, ai_mode="model",
                              model_profile=model_profile)
        rows.append(rec)
        paths.append(path)
    errs = []
    for i, rec in enumerate(rows[-3:]):
        e = RL.validate(rec)
        if e:
            errs.append({"sample": i, "errors": e})
    sample_path = os.path.join(root, "sample_3.jsonl")
    with open(sample_path, "w", encoding="utf-8", newline="\n") as f:
        for rec in rows[-3:]:
            f.write(json.dumps(rec, ensure_ascii=False,
                               separators=(",", ":")) + "\n")
    winners = {"good": 0, "wolf": 0, "draw": 0}
    for rec in rows:
        w = rec["result"]["winner"]
        winners[w] = winners.get(w, 0) + 1
    summary = {
        "profile": model_profile,
        "n_games": len(rows),
        "n_errors": len(errs),
        "winner_counts": winners,
        "invalid_actions_total": sum(
            rec["result"]["invalid_actions"] for rec in rows),
        "elapsed_sec": round(time.time() - t0, 2),
        "record_paths": sorted(set(paths)),
        "sample_3": sample_path,
        "sample_validation": "PASS" if not errs else "FAIL",
        "sample_errors": errs,
        "note": "游戏胜率不是研究结果；自测只证明接口与记录不报错。",
    }
    out = os.path.join(root, "model_selftest_summary.json")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("MODEL_SELFTEST_PASS" if not errs else "MODEL_SELFTEST_FAIL")
    return summary

def selftest(record_root, n_liars=1, lie_prob=0.55):
    print("=== 自测 1/3：人机混编（1 名脚本代打人类 + 6 AI）===")
    names = seat_names(1, ["自测玩家"])
    r1, p1 = play_game(1, names, 2026111301, record_root, "human_vs_ai",
                       selftest=True, delay=0.0, n_liars=n_liars,
                       lie_prob=lie_prob)
    print("  写入：%s" % p1)

    print("\n=== 自测 2/3：纯 AI 旁观 ===")
    names = seat_names(0, [])
    r2, p2 = play_game(0, names, 2026111302, record_root, "ai_vs_ai",
                       selftest=True, delay=0.0, n_liars=n_liars,
                       lie_prob=lie_prob)

    print("\n=== 自测 3/3：纯 AI 旁观（第二局，凑 3 行样本）===")
    r3, p3 = play_game(0, names, 2026111303, record_root, "ai_vs_ai",
                       selftest=True, delay=0.0, n_liars=n_liars,
                       lie_prob=lie_prob)
    print("\n自测完成。记录根目录：%s" % record_root)
    return p1


def select_ai_profile():
    while True:
        clear()
        banner()
        cur = AI_CONFIG
        print("当前 AI：%s" % (
            "启发式" if cur["mode"] == "heuristic"
            else "研究模型 / %s" % cur["profile"]))
        print()
        print("  1. 启发式（baseline，不加载模型）")
        print("  2. 研究模型 symmetric_q_only（默认研究档，推荐）")
        print("  3. 研究模型 auto_hybrid（未联合验证，仅供娱乐）")
        print("  4. 研究模型 good_three_piece（好人侧部署档）")
        print("  5. 研究模型 q_seer_fair（公平对局，关 Head-A）")
        print("  6. 研究模型 wolf_kit_experimental（不部署，仅实验）")
        print("  0. 返回")
        c = input("选一个（0-6）：").strip()
        if c == "0":
            return
        if c == "1":
            AI_CONFIG["mode"] = "heuristic"
            AI_CONFIG["profile"] = "symmetric_q_only"
            return
        idx = {"2": "symmetric_q_only", "3": "auto_hybrid",
               "4": "good_three_piece", "5": "q_seer_fair",
               "6": "wolf_kit_experimental"}.get(c)
        if idx is None:
            print("请输入 0-6。")
            time.sleep(0.5)
            continue
        meta = AG.MODEL_META[idx]
        if meta.get("warning"):
            print()
            print("⚠ " + meta["warning"])
            input("按回车确认继续（这是提示，不是错误）……")
        AI_CONFIG["mode"] = "model"
        AI_CONFIG["profile"] = idx
        return


def main():
    ap = argparse.ArgumentParser(description="狼人杀·可玩版")
    ap.add_argument("--record-root", default=str(HERE.parent / "对局记录"))
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--ai-vs-ai", type=int, default=0, metavar="N")
    ap.add_argument("--seed", type=int, default=20261113)
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--wolf-liars", type=int, default=1, choices=(1, 2))
    ap.add_argument("--lie-prob", type=float, default=0.55)
    ap.add_argument("--ai-model", default="heuristic",
                    choices=("heuristic",) + MODEL_PROFILE_ORDER,
                    help="heuristic 或研究模型 profile")
    ap.add_argument("--model-profile", default="symmetric_q_only",
                    choices=MODEL_PROFILE_ORDER)
    ap.add_argument("--model-selftest", type=int, default=0, metavar="N")
    ap.add_argument("--speech-mode", default="single", choices=("single", "multi"),
                    help="发言档：single=三选一（默认）；multi=每天最多 1 自报+2 指认（实验）")
    args = ap.parse_args()

    if args.speech_mode == "multi" and args.ai_model != "heuristic":
        raise SystemExit("multi 实验档不支持研究模型（156 输入分布不匹配）；"
                         "请去掉 --ai-model，或改用 GUI 实验档的启发式档。")
    if args.speech_mode == "multi":
        print("[LRS_084] 多发言实验档：仅启发式 / 纯 AI；默认 start.bat 与三选一未改动。")
    if args.ai_model != "heuristic":
        AI_CONFIG["mode"] = "model"
        AI_CONFIG["profile"] = args.ai_model
        meta = AG.MODEL_META[args.ai_model]
        if meta.get("warning"):
            print("⚠ " + meta["warning"])
    if args.model_selftest:
        prof = (args.ai_model if args.ai_model != "heuristic"
                else args.model_profile)
        model_selftest(args.model_selftest, 2026113000, args.record_root, prof)
        return
    if args.selftest:
        selftest(args.record_root, args.wolf_liars, args.lie_prob)
        return
    if args.ai_vs_ai:
        ai_vs_ai(args.ai_vs_ai, args.seed, args.record_root, delay=args.delay,
                 n_liars=args.wolf_liars, lie_prob=args.lie_prob,
                 ai_mode=AI_CONFIG["mode"],
                 model_profile=AI_CONFIG["profile"],
                 speech_mode=args.speech_mode)
        return

    while True:
        clear()
        banner()
        cur = AI_CONFIG
        cur_cn = ("启发式" if cur["mode"] == "heuristic"
                  else "研究模型：" + cur["profile"])
        print("  1. 人机混编（1-6 个真人 + 其余 AI，同机轮换、身份隐藏）")
        print("  2. 纯 AI 旁观（自动打、自动攒记录）")
        print("  3. 看规则与 AI 版本")
        print("  4. 切换 AI 对手（当前：%s）" % cur_cn)
        print("  5. 退出")
        print()
        c = input("选一个（1-5）：").strip()
        if c == "5" or c.lower() == "q":
            print("再见。")
            return
        if c == "4":
            select_ai_profile()
            continue
        if c == "3":
            clear()
            show_versions()
            input("按回车返回……")
            continue
        if c == "2":
            clear()
            try:
                n = int(input("跑几局？（回车=3）：").strip() or "3")
            except ValueError:
                n = 3
            ai_vs_ai(max(1, n), args.seed, args.record_root, delay=args.delay,
                     n_liars=args.wolf_liars, lie_prob=args.lie_prob,
                     ai_mode=AI_CONFIG["mode"],
                     model_profile=AI_CONFIG["profile"],
                     speech_mode=args.speech_mode)
            input("\n按回车返回主菜单……")
            continue
        if c == "1":
            clear()
            print("人机混编：真人坐 0..N-1 号位，其余座位是 AI。")
            print("身份由 seed 随机分配，开局谁都不知道自己是好是坏。\n")
            try:
                n = int(input("几个真人？（1-6，回车=1）：").strip() or "1")
            except ValueError:
                n = 1
            n = max(1, min(6, n))
            names = []
            for i in range(n):
                nm = input("第 %d 位昵称（回车=玩家%d）：" % (i + 1, i + 1)).strip()
                names.append(nm or "玩家%d" % (i + 1))
            cfg_names = seat_names(n, names)
            print("\n座位：")
            for pid, nm in enumerate(cfg_names):
                print("  %d 号  %s  %s" % (pid, nm, "（真人）" if pid < n else "（AI）"))
            print("\n提醒：每到一个真人行动，屏幕会清空并要求交接；")
            print("      确认上一位玩家已经离开屏幕再按回车。")
            input("\n按回车开局……")
            play_game(n, cfg_names, args.seed, args.record_root, "mixed",
                      delay=0.0, n_liars=args.wolf_liars,
                      lie_prob=args.lie_prob,
                      ai_mode=AI_CONFIG["mode"],
                      model_profile=AI_CONFIG["profile"],
                      speech_mode=args.speech_mode)
            input("\n按回车返回主菜单……")
            continue
        print("请输入 1-5。")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n已退出。")
