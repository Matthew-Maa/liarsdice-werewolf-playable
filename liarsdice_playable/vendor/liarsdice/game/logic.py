"""
logic.py - 吹牛骰子的纯规则函数
================================
不依赖任何 AI，只描述"游戏规则"本身：
掷骰、组合数/二项分布概率、叫牌合法性、叫牌生成策略。

注意：这里的 special_on 参数 = 本局"1 是百搭"是否仍然生效
（一旦有人叫了点数 1，百搭立即失效，由 env.py 负责维护）。
"""
import random
import time

BLUFF_THRESHOLD = 0.1

def roll_dice():
    return random.randint(1, 6)

def combination(n, k):
    if k < 0 or k > n: return 0
    if k > n - k: k = n - k
    result = 1
    for i in range(1, k + 1):
        result = result * (n - i + 1) // i
    return result

POW_1_6 = [1.0, 1.0/6.0, 1.0/36.0, 1.0/216.0, 1.0/1296.0,
           1.0/7776.0, 1.0/46656.0, 1.0/279936.0,
           1.0/1679616.0, 1.0/10077696.0, 1.0/60466176.0,
           1.0/362797056.0, 1.0/2176782336.0, 1.0/13060694016.0,
           1.0/78364164096.0]
POW_5_6 = [1.0, 5.0/6.0, 25.0/36.0, 125.0/216.0, 625.0/1296.0,
           3125/7776.0, 15625.0/46656.0, 78125.0/279936.0,
           390625.0/1679616.0, 1953125.0/10077696.0, 9765625.0/60466176.0,
           48828125.0/362797056.0, 244140625.0/2176782336.0,
           1220703125.0/13060694016.0, 6103515625.0/78364164096.0]
# 2026-10-06 Kimi 修复 p=1/6 bug：百搭下单骰命中概率应为 2/6（目标面或万能 1），
# 补 2/6 与 4/6 幂次表。p_type: 1→(1/6,5/6) 2→(2/6,4/6)
POW_2_6 = [1.0, 2.0/6.0, 4.0/36.0, 8.0/216.0, 16.0/1296.0,
           32.0/7776.0, 64.0/46656.0, 128.0/279936.0,
           256.0/1679616.0, 512.0/10077696.0, 1024.0/60466176.0,
           2048.0/362797056.0, 4096.0/2176782336.0, 8192.0/13060694016.0,
           16384.0/78364164096.0]
POW_4_6 = [1.0, 4.0/6.0, 16.0/36.0, 64.0/216.0, 256.0/1296.0,
           1024/7776.0, 4096.0/46656.0, 16384.0/279936.0,
           65536.0/1679616.0, 262144.0/10077696.0, 1048576.0/60466176.0,
           4194304.0/362797056.0, 16777216.0/2176782336.0,
           67108864.0/13060694016.0, 268435456.0/78364164096.0]

def binomial_probability_fast(n, k, p_type):
    # C(n,k)·p^k·(1-p)^(n-k)，幂次查表
    if k < 0 or k > n or n >= len(POW_1_6): return 0.0
    pk = {1: POW_1_6, 2: POW_2_6}.get(p_type, POW_1_6)[k]
    qk = {1: POW_5_6, 2: POW_4_6}.get(p_type, POW_5_6)[n - k]
    return combination(n, k) * pk * qk

# ★ task_DZ_086（2026-11-02）修缺陷：动作空间上限。
#   `coding.encode_action` 的约定是 (qty-1)*6+face <= 60 ⇒ qty 只能到 10。
#   原 `is_valid_bid` 只比大小、不校验上界，导致 qty=11 被判为合法，
#   而它 encode 出来是 61（越界）。这里补上上界校验。
#   留档：scripts/_bugfix_137/before/logic.py
QTY_MAX = 10

def is_valid_bid(qty, face, last_qty, last_face, is_first, special_on):
    # 首叫限最低个数；之后必须更大（点数 1 视为最大）
    # ★ task_DZ_086：先校验动作空间上界（qty 1..QTY_MAX），越界一律不合法
    if qty < 1 or qty > QTY_MAX:
        return False
    if is_first:
        min_qty = 2 if face == 1 else 3
        if qty < min_qty: return False
        return True
    new_face_val = 7 if face == 1 else face
    last_face_val = 7 if last_face == 1 else last_face
    return qty > last_qty or (qty == last_qty and new_face_val > last_face_val)

def advanced_first_bid(ai_stats, diceNum, special_on):
    # 首叫：围绕自己最多的点数叫，偶尔换点伪装
    effective = [0] * 7
    for i in range(2, 7):
        effective[i] = ai_stats[i] + (ai_stats[1] if special_on else 0)
    effective[1] = ai_stats[1]
    best_face = 2; best_count = 0
    for i in range(2, 7):
        if effective[i] > best_count:
            best_count = effective[i]; best_face = i
    if best_count <= 1:
        if random.random() < 0.7:
            min_face = 2; min_count = 99
            for i in range(2, 7):
                if effective[i] < min_count:
                    min_count = effective[i]; min_face = i
            return 2 + random.randint(0, 1), min_face
        else:
            return 2 if best_face == 1 else 3, best_face
    if random.random() < 0.55:
        second_best_face = 2; second_best_count = 0
        for i in range(2, 7):
            if i != best_face and effective[i] > second_best_count:
                second_best_count = effective[i]; second_best_face = i
        if second_best_count > 0 and random.random() < 0.9:
            return  second_best_count + 1, second_best_face
        else:
            fake_face = best_face
            while fake_face == best_face or fake_face == second_best_face:
                fake_face = random.randint(2, 6)
            return 3 + random.randint(0, 1), fake_face
    bid_qty = best_count + 1
    min_qty = 2 if best_face == 1 else 3
    if bid_qty < min_qty: bid_qty = min_qty
    return bid_qty, best_face

def advanced_raise_bid(ai_stats, last_qty, last_face, diceNum, special_on):
    # 跟叫：10% 概率纯诈唬（挑最少的点叫），否则按风险小幅加价
    effective = [0] * 7
    for i in range(2, 7):
        effective[i] = ai_stats[i] + (ai_stats[1] if special_on else 0)
    effective[1] = ai_stats[1]
    best_face = 2; best_count = 0
    for i in range(2, 7):
        if effective[i] > best_count:
            best_count = effective[i]; best_face = i
    bid_qty, bid_face = last_qty, last_face
    if best_count <= 1 and random.random() < BLUFF_THRESHOLD:
        fake_face = 2; fake_count = 99
        for i in range(2, 7):
            if effective[i] < fake_count:
                fake_count = effective[i]; fake_face = i
        return min(last_qty + 1, diceNum * 2), fake_face 
    if last_face < best_face and random.random() < 0.35:
        return max(3, last_qty + 1), best_face 
    test_qty = bid_qty + 1
    # 2026-10-06 Kimi 修复：百搭下单骰命中概率 2/6
    pt = 2 if special_on else 1
    risk_qty = 1.0 - binomial_probability_fast(diceNum * 2, test_qty, pt)
    risk_face = 1.0 - binomial_probability_fast(diceNum * 2, bid_qty, pt)
    if risk_qty < risk_face and random.random() < 0.5:
        bid_qty += 1
    else:
        faces = [2,3,4,5,6,1]
        for f in faces:
            if f > last_face:
                bid_face = f
                break
        else:
            bid_qty += 1; bid_face = 2
    return bid_qty, bid_face
