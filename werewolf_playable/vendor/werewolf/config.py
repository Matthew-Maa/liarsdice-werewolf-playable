"""
config.py - 全局配置（规则参数 + 路径）
=========================================
**本文件是所有"任务卡里 Ⓚ 默认值"的唯一落点。**
想改任何一条默认规则，只改这里的一个常量即可，全流程（环境/测试/对战表）自动跟随。

约定：
  - 任务卡第一节里项目作者"他定"的条目，这里只做常量固化，不加开关；
  - 标了 Ⓚ 的条目，这里都写成显式常量 + 一行注释，方便一句话改掉。
"""
import os

# ==================== 一、板子与胜负 ====================
# Ⓚ 板子构成（2026-10-23 22:xx 铨鸿拍板：加平民到 7 人局）。
#   默认 7 人：狼人×2、平民×2、女巫×1、预言家×1、猎人×1。
#   理由（探针 probe_balance）：6 人局随机格好人胜率只有 15.5%，且"狼≥好"占 83.6% 胜因，
#   太极端、信息不值钱；加一民到 7 人让板子没那么"一刀定胜负"。
BOARD = (
    ("WOLF", 2),
    ("VILLAGER", 2),
    ("WITCH", 1),
    ("SEER", 1),
    ("HUNTER", 1),
)

# ==================== 板子预设（task_LRS_003） ====================
# 可切换的正式板子预设。**BOARD 仍指向 v0_default**（v0 的 7 格表必须继续可比）。
#   - v1_main  = 狼1·民3·巫预猎，task_LRS_003 预实验（5.2/5.4/5.5）+ task_LRS_002 验收选定的"v1 主场板"
#                （狼会说谎 + lie_prob=1 → 好人 ~50%，唯一把好人调到五五开的配置）
#   - v0_6p    = 旧 6 人板，只留复现（落到独立目录，不覆盖旧存档）
BOARDS = {
    "v0_default": (("WOLF", 2), ("VILLAGER", 2), ("WITCH", 1), ("SEER", 1), ("HUNTER", 1)),
    "v1_main":    (("WOLF", 1), ("VILLAGER", 3), ("WITCH", 1), ("SEER", 1), ("HUNTER", 1)),
    "v0_6p":      (("WOLF", 2), ("VILLAGER", 1), ("WITCH", 1), ("SEER", 1), ("HUNTER", 1)),
}
# Ⓚ 默认是否把 BOARD 换成 v1_main：暂不换（换掉会让 v0 的 7 格表失去对照；等 v1 基准表出来再定）。
BOARD_DEFAULT = "v0_default"

# 当前激活的板子预设名（task_LRS_003）：决定结果目录路由（见六、路径）。
# set_board(name) 会同步更新；直接改 C.BOARD 而不改本名时，目录路由不跟随（探针脚本即如此）。
BOARD_NAME = BOARD_DEFAULT


def set_board(name):
    """按预设名切换板子：C.BOARD = C.BOARDS[name]，并同步 BOARD_NAME（结果目录路由）。
    影响 C.board_size() / actions.max_target()（decode 动态读取）/ mode_dir() / games_dir()。
    未知名或非 7 人板需调用方负责（v0_6p 只作复现）。"""
    if name not in BOARDS:
        raise KeyError("未知板子预设 %r（可选 %s）" % (name, list(BOARDS)))
    global BOARD, BOARD_NAME
    BOARD = BOARDS[name]
    BOARD_NAME = name
    return BOARD
# 胜负口径（2026-10-23 17:20 项目作者改定，取代原"屠边"）：
#   狼人全灭                     -> 好人胜
#   **狼人存活数 >= 好人存活数**  -> 狼人胜（神职算好人）
# 原"屠边"口径（狼全灭 / 神职全灭 / 平民全灭）保留为 WIN_MODE="annihilation"，
# 只为复现 17:07 那一版对战表，默认不再使用。
WIN_MODE = "parity"          # "parity"（人数均势，默认） | "annihilation"（屠边，旧口径）
GOD_ROLES = ("WITCH", "SEER", "HUNTER")
VILLAGER_ROLES = ("VILLAGER",)

# ==================== 二、夜晚 ====================
# 夜晚行动顺序（卡定）：狼人 -> 女巫 -> 预言家（猎人夜晚无行动）
NIGHT_ORDER = ("WOLF", "WITCH", "SEER")

# Ⓚ 两狼"轮流主刀"：第 n 夜由存活狼中按 pid 升序的第 ((n-1) % 存活狼数) 个主刀。
#    非主刀狼仍然出刀（记进日志，作偏好，不影响结果）—— 保留它的决策点，
#    后续做对手建模时需要"每只狼每夜都有动作"。
WOLF_ROTATE_LEADER = True

# Ⓚ 是否允许狼自刀（刀自己或刀同伴）。默认 False：合法刀目标 = 存活的非狼玩家。
WOLF_SELF_KILL = False

# Ⓚ 女巫：解药、毒药各一瓶，同一夜只能用一个，全程不可自救
WITCH_ANTIDOTE_ONCE = True
WITCH_POISON_ONCE = True
WITCH_ONE_POTION_PER_NIGHT = True
WITCH_CAN_SELF_SAVE = False

# Ⓚ 预言家：验存活的其他玩家；允许重复验同一个人（浪费但合法）
SEER_CAN_CHECK_SELF = False
SEER_ALLOW_RECHECK = True

# ==================== 三、白天 ====================
# Ⓚ 首日发言起点 = pid 0；此后从"上一出局者的下一位"开始顺时针。
#    平票日无人出局 -> 发言起点不变（沿用上一位出局者）。
DAY_FIRST_SPEAKER_PID = 0
TIE_KEEPS_SPEECH_START = True

# Ⓚ 平票：得票最高者并列 -> 本轮无人出局（卡面口径）
#    人数均势口径下这条的分量变重了：平票 = 好人白拿一个回合。
#    故加一个开关便于做对照实验，默认仍是卡面口径。
#      "no_elimination"      平票无人出局（默认，卡面）
#      "random_elimination"  平票者里随机出一人（用独立 RNG，不动角色分配序列）
TIE_RULE = "no_elimination"
TIE_NO_ELIMINATION = (TIE_RULE == "no_elimination")

# Ⓚ 投票目标：存活的其他玩家 或 弃票（不能投自己）
VOTE_ALLOW_SELF = False
VOTE_ALLOW_ABSTAIN = True

# Ⓚ 猎人：被刀 / 被投票出局可开枪带走 1 名存活玩家；被毒死不开枪。
#    同时被刀又被毒 -> 死因按"毒"处理 -> 不开枪。
HUNTER_SHOT_ON_WOLF_KILL = True
HUNTER_SHOT_ON_VOTE_OUT = True
HUNTER_SHOT_ON_POISON = False

# Ⓚ 死亡原因是否公开：刀/毒/投票出局/枪杀 的原因公开；预言家验人结果只有本人知道。
DEATH_CAUSE_PUBLIC = True

# ==================== 三·五、警长/警徽（task_LRS_030，设计书 task_LRS_029 §1）====================
# ★★ 总开关：默认 False。**False 时全流程必须与"无警长"版本逐位等价**
#    （task_LRS_030 判据 1：120 局 events SHA 全等）。所有 env 改动点都必须 `if cfg.SHERIFF:` gated。
SHERIFF = False

# Ⓚ 警长票权：警长的一票算 1.5 票（`rules.tally_votes(sheriff=...)` 才生效）
SHERIFF_VOTE_WEIGHT = 1.5

# Ⓚ 警长每日首先发言（仅调整发言顺序，不改候选集 ⇒ 不碰宽度线四档口径）
SHERIFF_SPEAK_FIRST = True

# Ⓚ 选举日：只在第 1 天选一次（不每天重选，避免局长膨胀）
SHERIFF_ELECT_DAY = 1

# Ⓚ 警徽流转方式：designate=死时指定继承人（默认） | drop=直接流失 | reelect=重选
BADGE_MODE = "designate"

# Ⓚ 选举平票处理：True=本局无警长（不重选，避免递归）
SHERIFF_TIE_NO_SHERIFF = True

# ==================== 四、局长度上界（防死锁）====================
# Ⓚ 最多 30 个白天；超过则判平局（正常板子 4~5 天必结束，只是兜底）
MAX_DAYS = 30

# ==================== 五、baseline 的行为参数 ====================
# 完全诚实 / 启发式 共用：好人发言是否只报"真实身份 + 真实验人结果"
HONEST_WOLF_SPEECH = "SILENCE"        # 卡定：狼人恒沉默
HONEST_WITCH_USE_POISON = False       # Ⓚ 诚实女巫无信息时不毒人（只救人）
# Ⓚ 猎人开枪口径："accuse_then_random" = 先打被公开指控为狼次数最多者，无指控则随机；
#    "random" = 纯随机。诚实与启发式共用（卡里说启发式只改投票）。
HUNTER_SHOT_MODE = "accuse_then_random"

# 发言加权启发式（baseline 3）的嫌疑分权重
#   score = W_ACCUSE * (被 ACCUSE(·, WOLF) 的次数) + W_SILENCE * (该玩家 SILENCE 的次数)
#   ⚠ 任务卡第 2、3 条在文字上把投票规则写成了同一件事（都是"被指控为狼次数最多者"），
#     若 W_SILENCE=0 则 baseline2 与 baseline3 完全等价、梯度消失。
#     故 Ⓚ 取 W_SILENCE=0.5，让"沉默者可疑"进入打分——这正是卡里 v0 想观察的现象。
#     要严格回到卡面口径：把 W_SILENCE 改成 0.0 即可。
W_ACCUSE = 1.0
W_SILENCE = 0.5

# ==================== 六、路径（由本文件位置自动推导）====================
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))    # werewolf/
PROJECT_ROOT = os.path.dirname(PACKAGE_DIR)                 # 狼人杀/
RESULTS_DIR = os.path.join(PROJECT_ROOT, "results")
V0_DIR = os.path.join(RESULTS_DIR, "werewolf_v0")
DOCS_DIR = os.path.join(PROJECT_ROOT, "docs")

# 板子预设 -> 结果目录（相对 RESULTS_DIR）。task_LRS_003：不同板子必须分目录，防混数据。
#   - v0_default -> werewolf_v0（现有，不动）
#   - v1_main    -> werewolf_v1（新建，task_LRS_003 基准表）
#   - v0_6p      -> werewolf_v0/legacy_6p_20261024_newcode（新 6 人复现目录，与 oldcode 同级不覆盖）
BOARD_DIRS = {
    "v0_default": "werewolf_v0",
    "v1_main": "werewolf_v1",
    "v0_6p": os.path.join("werewolf_v0", "legacy_6p_20261024_newcode"),
}


def results_root(board_name=None):
    """按当前激活板子返回结果根目录（results/<BOARD_DIRS[BOARD_NAME]>）。"""
    name = board_name or BOARD_NAME
    return os.path.join(RESULTS_DIR, BOARD_DIRS.get(name, "werewolf_v0"))


def mode_dir(win_mode=None):
    """
    按胜负口径分目录：results/<board_root>/<win_mode>/
    —— 换口径不能把新旧数据混进一个目录（混了就没法重算、也没法对比）。
    """
    return os.path.join(results_root(), win_mode or WIN_MODE)


def games_dir(win_mode=None):
    """对局记录目录：results/<board_root>/<win_mode>/games/"""
    return os.path.join(mode_dir(win_mode), "games")


def board_size(board=None):
    return sum(n for _, n in (board or BOARD))


def board_desc(board=None):
    return "、".join(f"{r}×{n}" for r, n in (board or BOARD))


WIN_MODE_CN = {
    "parity": "人数均势（狼>=好即狼胜，神职算好人）",
    "annihilation": "屠边（狼全灭/神职全灭/平民全灭）",
}


def describe():
    """一句话描述当前规则口径（打印日志用）。"""
    return (f"{board_size()} 人局（{board_desc()}）；胜负={WIN_MODE_CN[WIN_MODE]}；夜序 "
            f"{'→'.join(NIGHT_ORDER)}；平票规则={TIE_RULE}；"
            f"女巫可自救={WITCH_CAN_SELF_SAVE}")


# 供 Ⓚ 清单自动生成用：(常量名, 当前值, 含义)
KAPPA_ITEMS = [
    ("BOARD", "狼2/民2/女巫1/预1/猎1（7 人）", "板子构成"),
    ("WIN_MODE", WIN_MODE, "胜负口径：parity=狼>=好即狼胜；annihilation=旧屠边"),
    ("WOLF_ROTATE_LEADER", WOLF_ROTATE_LEADER, "两狼轮流主刀，非主刀也出刀（记偏好）"),
    ("WOLF_SELF_KILL", WOLF_SELF_KILL, "狼能否自刀（默认禁）"),
    ("WITCH_ONE_POTION_PER_NIGHT", WITCH_ONE_POTION_PER_NIGHT, "女巫同夜只能用一瓶药"),
    ("WITCH_CAN_SELF_SAVE", WITCH_CAN_SELF_SAVE, "女巫能否自救（默认禁）"),
    ("SEER_CAN_CHECK_SELF", SEER_CAN_CHECK_SELF, "预言家能否验自己（默认禁）"),
    ("SEER_ALLOW_RECHECK", SEER_ALLOW_RECHECK, "预言家能否重复验同一人（默认允许）"),
    ("DAY_FIRST_SPEAKER_PID", DAY_FIRST_SPEAKER_PID, "首日发言起点"),
    ("TIE_RULE", TIE_RULE, "平票处理：no_elimination=无人出局（卡面）/ random_elimination=并列者随机出一人"),
    ("VOTE_ALLOW_SELF", VOTE_ALLOW_SELF, "能否投自己（默认禁）"),
    ("VOTE_ALLOW_ABSTAIN", VOTE_ALLOW_ABSTAIN, "能否弃票（默认可以）"),
    ("HUNTER_SHOT_ON_POISON", HUNTER_SHOT_ON_POISON, "被毒死的猎人能否开枪（默认禁）"),
    ("DEATH_CAUSE_PUBLIC", DEATH_CAUSE_PUBLIC, "死亡原因是否公开"),
    ("MAX_DAYS", MAX_DAYS, "局长上界（超判平局）"),
    ("HONEST_WOLF_SPEECH", HONEST_WOLF_SPEECH, "诚实狼人发言（恒沉默）"),
    ("HONEST_WITCH_USE_POISON", HONEST_WITCH_USE_POISON, "诚实女巫无信息时是否毒人"),
    ("HUNTER_SHOT_MODE", HUNTER_SHOT_MODE, "猎人开枪目标口径"),
    ("W_SILENCE", W_SILENCE, "启发式里「沉默」的嫌疑权重（0 则退化成诚实）"),
    ("SHERIFF", SHERIFF, "警长/警徽机制总开关（默认关，关闭时与无警长版本逐位等价）"),
    ("SHERIFF_VOTE_WEIGHT", SHERIFF_VOTE_WEIGHT, "警长票权（默认 1.5 票）"),
    ("SHERIFF_SPEAK_FIRST", SHERIFF_SPEAK_FIRST, "警长是否每日首先发言"),
    ("SHERIFF_ELECT_DAY", SHERIFF_ELECT_DAY, "警长选举日（默认第 1 天，只选一次）"),    ("BADGE_MODE", BADGE_MODE, "警徽流转：designate=指定继承人 / drop=流失 / reelect=重选"),
]
