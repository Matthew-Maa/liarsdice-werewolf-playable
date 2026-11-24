"""
env.py - 无界面游戏环境
一局游戏 = 两个 AI 轮流决策，直到有人挑战为止。
"""
import random
import sys
from .logic import is_valid_bid, roll_dice


class LiarDiceEnv:
    # ★ task_DZ_086（2026-11-02）：越界动作的拒收日志。
    #   原实现只用 `is_valid_bid` 校验，而它不校验 qty 上限 ⇒ qty=11 会被**静默放行**。
    #   现在：非法叫牌一律改判为 challenge，并且**记一条日志**（不再静默）。
    #   留档：scripts/_bugfix_137/before/env.py
    reject_log = None          # None = 不记；设为 list 时把每次拒收追加进去
    verbose_reject = False     # True = 同时打一行到 stderr

    # ★★ task_DZ_087（2026-11-03，监理批准路线 B）：开盅时把**双方手牌**回传给两个 AI。
    #
    # **动机**：真实吹牛骰子开盅时，双方骰子本来就是全公开的 —— 回传手牌不是作弊，
    #   是规则的一部分。而且这里**只在游戏结束时**调用，agent 拿到的是"这局已经结束"的
    #   信息，能影响的只有**下一局**的权重更新，比 task_DZ_014 的 oracle（局内就知道对手
    #   完整参数）弱得多。
    #
    # **接口纪律**（监理裁定 §1，必须遵守）：
    #   · 新钩子是**追加**接口，`on_challenge_result` 的签名**一字不改**；
    #   · `on_dice_revealed` **只在游戏结束时**调用一次，局内任何阶段都不得回传手牌；
    #   · 对方 AI 没实现这个方法时**静默跳过**（用 hasattr 判，不抛异常）——
    #     scripts/ 下有 43 个文件、97 处实现 `on_challenge_result`，不能要求它们都加。
    #
    # 留档：scripts/_bugfix_140/before/env.py
    reveal_hands = True        # False = 完全不调用新钩子（用于"修复前行为"对照）

    def _reveal_dice(self, dice, winner, last_qty, last_face):
        """游戏结束时回传双方手牌（只在挑战分出胜负的那一刻调用一次）。"""
        if not self.reveal_hands:
            return
        payload = {
            'dice': [list(dice[0]), list(dice[1])],
            'winner': int(winner),
            'last_qty': int(last_qty),
            'last_face': int(last_face),
            'special_rule': int(self.special_rule),
            'dice_num': int(self.dice_num),
        }
        for seat, ai in enumerate(self.ai):
            f = getattr(ai, 'on_dice_revealed', None)
            if f is None:
                continue                      # 没实现 ⇒ 跳过，不报错
            # ★ 每个 AI 拿到的是**同一份** payload（双方手牌都公开），
            #   额外给 `seat` 让它知道自己是哪一方。
            f(payload, seat)

    def _reject_call(self, turn, action, last_qty, last_face, first_turn, special):
        rec = {'turn': int(turn), 'action': list(action),
               'last_qty': int(last_qty), 'last_face': int(last_face),
               'first_turn': bool(first_turn), 'special': int(special),
               'reason': 'invalid_call'}
        if self.reject_log is not None:
            self.reject_log.append(rec)
        if self.verbose_reject:
            sys.stderr.write('[env] reject invalid call %s (last_qty=%d, last_face=%d)\n'
                             % (list(action), last_qty, last_face))
        return rec

    def __init__(self, ai_p0, ai_p1, dice_num=5, special_rule=0):
        """
        ai_p0: 玩家 0（先手候选）
        ai_p1: 玩家 1
        dice_num: 每人几个骰子
        special_rule: 特殊规则（1 是开启"1是百搭"）
        """
        self.ai = [ai_p0, ai_p1]
        self.dice_num = dice_num
        self.special_rule = special_rule

    def play_one_game(self, on_decision=None):
        """
        跑一整局。
        返回: 0 或 1（赢家的编号）
        """
        # 一局的大致流程：掷骰 → 随机先手 → 轮流叫牌，直到有人挑战 → 开盅数个数定胜负
        # ---- 掷骰 ----
        dice = [
            [roll_dice() for _ in range(self.dice_num)],
            [roll_dice() for _ in range(self.dice_num)],
        ]

        # ---- 随机决定先手 ----
        turn = random.randint(0, 1)
        first_turn = True
        special = self.special_rule
        last_qty = 0
        last_face = 0

        # ---- v5 新增：本局叫牌历史 ----
        # call_log 是原始记录：[(qty, face, player), ...]，player 是叫牌方编号 0/1。
        # 组装 state 时，会把 player 转成相对"当前决策方 turn"的 by_self 标记，
        # 即 history 元素 = (qty, face, by_self)，by_self=True 表示该叫牌是决策方自己叫的。
        # 向后兼容：所有现有 AI 只读自己认识的字段，新代码须用 state.get('history', [])。
        call_log = []

        # ---- 轮流叫牌 ----
        while True:
            state = {
                'own_dice': dice[turn],
                'dice_num': self.dice_num,
                'special_rule': special,
                'first_turn': first_turn,
                'last_qty': last_qty,
                'last_face': last_face,
                # v5 新增：由原始 call_log 推导出"相对当前决策方"的 by_self 序列
                'history': [(q, f, (p == turn)) for (q, f, p) in call_log],
            }

            action = self.ai[turn].decide(state)

            # ---- 合法性校验 ----
            # ★ task_DZ_086：非法叫牌改判 challenge **并记日志**（原实现静默改判）
            if action[0] == 'call':
                _, _qty, _face = action
                if not is_valid_bid(_qty, _face, last_qty, last_face, first_turn, special):
                    self._reject_call(turn, action, last_qty, last_face,
                                      first_turn, special)
                    action = ('challenge', 0, 0)

            if on_decision is not None:
                on_decision(turn, state, action)
            if action[0] == 'challenge':
            # ---- 有人挑战：开盅，统计 ----
                target = last_face
                total = 0
                for d in dice[0]:
                    if d == target or (special and d == 1):
                        total += 1
                for d in dice[1]:
                    if d == target or (special and d == 1):
                        total += 1

                challenger = turn
                caller = 1 - turn

                if total >= last_qty:
                    # 叫牌者赢（挑战失败）
                    winner = caller
                else:
                    # 挑战者赢
                    winner = challenger

                # 通知两个 AI 更新记忆
                for ai in self.ai:
                    ai.on_challenge_result(last_qty, total)

                # ★ task_DZ_087：开盅瞬间公开双方手牌（**唯一**调用点，只在游戏结束时）
                self._reveal_dice(dice, winner, last_qty, last_face)

                return winner

            # ---- 叫牌动作 ----
            _, qty, face = action
            last_qty = qty
            last_face = face
            if face == 1:
                special = 0   # 叫 1 后，百搭失效
            # v5 新增：记录这条叫牌（原始视图记录叫牌方编号，推导 by_self 在组装 state 时做）
            call_log.append((qty, face, turn))
            first_turn = False
            turn = 1 - turn
