"""
liarsdice - 吹牛骰子不完全信息博弈项目核心包
==============================================
这个包只放"被别人 import 的库代码"，直接运行的脚本在上一级的 scripts/ 目录。

子包：
    game      游戏规则（掷骰、叫牌合法性判定、一局游戏的环境）
    ai        各种 AI（基线、蒙特卡洛、行为克隆神经网络、DQN）
    analysis  回放分析（诈唬行为统计）
config.py     全局开关：改 SPECIAL_RULE 一个数切换整条实验线
"""

# ---- 版本号（task_DZ_086 首次建立）----
# 本包此前**没有版本号**，这里从 1.0.0 起。
#   修复：logic.is_valid_bid 补 qty 上限校验（qty 1..QTY_MAX=10）；
#         mcts.MonteCarloAI 候选剔除 qty>10；
#         game.env 非法叫牌改判 challenge 时不再静默（记 reject_log）。
#   性质：**防御性修复**。该缺陷在当前参数下不可触发
#         （last_qty=10 时 prob_real 上界 (2/6)^5≈0.0041 < 挑战阈值 0.4 ⇒ 必然挑战），
#         既有对局与胜率数字不受影响。详见 result_20261102_137.md。
__version__ = '1.0.0'
# 1.0.0 -> 1.1.0（task_DZ_087，2026-11-03，监理批准路线 B）
#   新增：`LiarDiceEnv` 在**游戏结束**时调用 `ai.on_dice_revealed(payload, seat)`，
#         把双方手牌公开给两个 AI（真实吹牛骰子开盅时本就公开）。
#         · `on_challenge_result` 签名**未改**；新钩子是追加接口；
#         · 对方没实现时静默跳过（hasattr 判），不影响 scripts/ 下 97 处既有实现；
#         · 只在挑战分出胜负的那一刻调用一次，局内任何阶段都不回传手牌。
#   回归：非挑战路径逐位不变（task_DZ_087 R1，≥120 局面）；旧产物 SHA256 不变。
#   详见 shared/reports/liarsdice/result_20261103_140.md
__version__ = '1.1.0'

