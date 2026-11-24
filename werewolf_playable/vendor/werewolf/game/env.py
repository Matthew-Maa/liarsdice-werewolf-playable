"""
env.py - 狼人杀环境（无界面，事件流驱动）
==========================================
一局 = 夜(狼→女巫→预言家) → 结算 → 白天(顺序发言 → 投票) → 结算，循环到屠边。

设计要点（给后续研究用）：
  1. **一切决策点都留痕**：每个 act() 调用都写一条 action 事件（含 int code），
     包括非主刀狼的"偏好出刀"和已死猎人的开枪。
  2. **信息隔离**：agent 只能通过 view 看世界；view 里的 public 只含
     公开发言 / 公开投票 / 公开死亡公告（含死因），私有信息按角色分别注入。
  3. **可复现**：角色分配用 Random(seed)；每个座位的 agent 用
     Random(seed*1009 + pid*7919)，与"哪个 baseline 坐哪个座"无关 ——
     这样同 seed 的不同阵容格可以直接配对比较。
"""
import random

from .. import config as C
from .roles import WOLF, SEER, WITCH, HUNTER, faction_of, GOD_ROLES, VILLAGER_ROLES
from . import actions as A
from . import rules as R


class Player(object):
    __slots__ = ("pid", "role", "alive", "death_cause", "death_day", "death_phase")

    def __init__(self, pid, role):
        self.pid = pid
        self.role = role
        self.alive = True
        self.death_cause = None
        self.death_day = None
        self.death_phase = None

    def to_dict(self):
        return {
            "pid": self.pid,
            "role": self.role,
            "alive": self.alive,
            "death_cause": self.death_cause,
            "death_day": self.death_day,
            "death_phase": self.death_phase,
        }


class WerewolfEnv(object):
    def __init__(self, agents, seed=0, record=True, game_id=None, cell=None, cfg=None,
                 roles=None):
        """
        agents: 长度 = 板子人数的 agent 列表，agents[pid] 坐 pid 号位
        seed:   一局的唯一随机源
        record: 是否保留完整事件流（10k 局 smoke 时关掉省内存）
        roles:  强制角色分配（单元测试用；None = 按 seed 随机洗牌）
        """
        self.cfg = cfg or C
        self.agents = list(agents)
        self.seed = int(seed)
        self.record = record
        self.game_id = game_id if game_id is not None else "g%06d" % self.seed
        self.cell = cell
        self.forced_roles = tuple(roles) if roles is not None else None

        self.n_players = C.board_size()
        assert len(self.agents) == self.n_players, \
            "agent 数 %d != 板子人数 %d" % (len(self.agents), self.n_players)

        self.rng = random.Random(self.seed)      # 只用于角色分配（跨阵容格一致）
        # 平票随机出局用的独立 RNG：与角色分配序列解耦，保证同 seed 跨格仍可配对
        self.tie_rng = random.Random(self.seed * 31 + 7)
        self.players = []
        self.events = []
        self.steps = 0
        self.day = 0
        self.night_no = 0
        self.last_eliminated = None
        self.winner = None
        self.winner_reason = None
        self.invalid_actions = 0
        self.tie_days = 0

        # 私有状态
        self.seer_results = {}       # {target_pid: is_wolf}
        self.witch_antidote_used = False
        self.witch_poison_used = False
        self.night_victim = None     # 今夜被刀者（女巫可见）

        # ---- task_LRS_030：警长状态（SHERIFF=False 时这两行只是占位，不参与任何流程）----
        self.sheriff = None          # 当前警长 pid；None = 无警长
        self.sheriff_elected = False  # 是否已选过（只选一次）
        self.badge_dropped = False   # 警徽是否已流失
        # 警长选举用独立 RNG（不碰 rng / tie_rng 的既有序列 ⇒ 等价性安全）
        self.sheriff_rng = random.Random(self.seed * 53 + 11)

        # 公开信息
        self.public = {"speeches": [], "votes": [], "deaths": [], "rounds": []}

    # ==================== 主流程 ====================

    def run(self):
        self._setup()
        while True:
            if self.day >= C.MAX_DAYS:
                self.winner = "draw"
                self.winner_reason = "超过 %d 天上界" % C.MAX_DAYS
                self._ev({"kind": "end", "winner": self.winner,
                          "reason": self.winner_reason})
                break
            self.day += 1
            self._night()
            if self._settle_winner("night"):
                break
            self._daytime()
            if self._settle_winner("day"):
                break
        return self._record()

    def _setup(self):
        deck = []
        for role, n in C.BOARD:
            deck.extend([role] * n)
        if self.forced_roles is not None:
            assert len(self.forced_roles) == self.n_players
            deck = list(self.forced_roles)
        else:
            self.rng.shuffle(deck)
        self.players = [Player(i, deck[i]) for i in range(self.n_players)]
        # 每个座位一个独立 RNG，与阵容格无关
        for p in self.players:
            ag = self.agents[p.pid]
            if hasattr(ag, "reset"):
                ag.reset((self.seed * 1009 + p.pid * 7919) % (2 ** 31 - 1))
        if self.record:
            self._ev({"kind": "setup",
                      "roles": {str(p.pid): p.role for p in self.players}})

    # ==================== 夜晚 ====================

    def _night(self):
        cfg = self.cfg
        self.night_no += 1
        alive = self.alive_pids()
        roles_alive = {p: self.players[p].role for p in alive}
        wolves = [p for p in alive if roles_alive[p] == WOLF]

        kill_target = None
        saved = False
        poison_target = None
        self.night_victim = None

        # ---- 1. 狼人（卡定顺序第一）----
        if wolves:
            if cfg.WOLF_ROTATE_LEADER:
                leader = wolves[self._leader_index(len(wolves))]
            else:
                leader = wolves[0]
            legal = A.legal_kills(alive, wolves, cfg.WOLF_SELF_KILL)
            pref = {}
            for w in wolves:
                note = "leader" if w == leader else "pref_only"
                a = self._ask(w, "night_wolf", legal, note=note)
                pref[w] = a["target"]
            kill_target = pref[leader]
            self.night_victim = kill_target

        # ---- 2. 女巫（卡定顺序第二）----
        witch = self._find_alive(WITCH)
        if witch is not None:
            legal = A.legal_witch(
                victim=kill_target, witch_pid=witch, alive=alive,
                has_antidote=not self.witch_antidote_used,
                has_poison=not self.witch_poison_used,
                can_self_save=cfg.WITCH_CAN_SELF_SAVE,
            )
            a = self._ask(witch, "night_witch", legal)
            if a["potion"] == A.POTION_SAVE:
                saved = True
                self.witch_antidote_used = True
            elif a["potion"] == A.POTION_POISON:
                poison_target = a["target"]
                self.witch_poison_used = True

        # ---- 3. 预言家（卡定顺序第三）----
        seer = self._find_alive(SEER)
        if seer is not None:
            legal = A.legal_checks(
                pid=seer, alive=alive, already_checked=set(self.seer_results),
                allow_self=cfg.SEER_CAN_CHECK_SELF,
                allow_recheck=cfg.SEER_ALLOW_RECHECK,
            )
            a = self._ask(seer, "night_seer", legal)
            tgt = a["target"]
            self.seer_results[tgt] = (self.players[tgt].role == WOLF)
            self._ev({"kind": "seer_result", "day": self.day,
                      "actor": seer, "target": tgt,
                      "is_wolf": self.seer_results[tgt], "private": True})

        # ---- 4. 结算 ----
        deaths = R.resolve_night(kill_target, saved, poison_target)
        for pid, cause in deaths:
            self._kill(pid, cause, by=None, phase="night")

        self.public["rounds"].append({
            "day": self.day, "phase": "night",
            "pids": [p for p, _ in deaths],
            "causes": {str(p): c for p, c in deaths},
        })
        self._ev({"kind": "night_result", "day": self.day,
                  "kill_target": kill_target, "saved": bool(saved),
                  "poison_target": poison_target,
                  "deaths": [{"pid": p, "cause": c} for p, c in deaths]})

        # ---- 5. 猎人开枪（夜死，被毒不开枪）----
        for pid, cause in deaths:
            if R.hunter_can_shoot(self.players[pid].role, cause):
                self._hunter_shot(pid, cause)

    def _leader_index(self, n_wolves):
        """第 n 夜主刀在"存活狼按 pid 升序"列表里的下标（轮流主刀）。"""
        return (self.night_no - 1) % n_wolves

    # ==================== 白天 ====================

    def _daytime(self):
        cfg = self.cfg
        alive = self.alive_pids()
        order = self._speech_order(alive)

        for pid in order:
            legal = A.legal_speeches(pid, alive)
            a = self._ask(pid, "day_speech", legal)
            self.public["speeches"].append(
                {"day": self.day, "pid": pid, "action": a})

        # ---- task_LRS_030：警长选举（只在选举日、且还没选过时）----
        #   ★ 必须放在发言之后、投票之前：这样"竞选发言"就是当天的正常发言，
        #     不新增竞选动作（设计书 §1.2），游戏时长不膨胀。
        if cfg.SHERIFF and not self.sheriff_elected and self.day == cfg.SHERIFF_ELECT_DAY:
            self._sheriff_election(alive)
            alive = self.alive_pids()      # 选举可能带来死亡？不会，但保持一致性

        # 投票：先全部收齐再公开（同日投票是同时的，不能边投边看）
        collected = []
        for pid in alive:
            legal = A.legal_votes(pid, alive,
                                  allow_self=cfg.VOTE_ALLOW_SELF,
                                  allow_abstain=cfg.VOTE_ALLOW_ABSTAIN)
            a = self._ask(pid, "day_vote", legal)
            collected.append((pid, None if a["type"] == A.ABSTAIN else a["target"]))

        for pid, tgt in collected:
            self.public["votes"].append(
                {"day": self.day, "voter": pid, "target": tgt})

        # ---- task_LRS_030：警长票权加权 ----
        #   ★ 只有 cfg.SHERIFF 且确有警长时才传 sheriff ⇒ 否则与改动前逐位一致。
        if cfg.SHERIFF and self.sheriff is not None:
            tally = R.tally_votes(collected, alive, sheriff=self.sheriff,
                                  sheriff_weight=cfg.SHERIFF_VOTE_WEIGHT)
        else:
            tally = R.tally_votes(collected, alive)
        if tally["tie"]:
            self.tie_days += 1
        out_pid = tally["eliminated"]
        # 平票处理（TIE_RULE）：默认无人出局；random_elimination 时在并列者里随机出一人
        if tally["tie"] and cfg.TIE_RULE == "random_elimination" and tally["top"]:
            out_pid = self.tie_rng.choice(sorted(tally["top"]))
        self._ev({"kind": "vote_tally", "day": self.day,
                  "counts": {str(k): v for k, v in tally["counts"].items()},
                  "top": tally["top"], "tie": tally["tie"],
                  "abstain": tally["abstain"],
                  "eliminated": out_pid})

        if out_pid is not None:
            out = out_pid
            self._kill(out, R.CAUSE_VOTE, by=None, phase="day")
            self.last_eliminated = out
            self.public["rounds"].append({
                "day": self.day, "phase": "day",
                "pids": [out], "causes": {str(out): R.CAUSE_VOTE},
            })
            if R.hunter_can_shoot(self.players[out].role, R.CAUSE_VOTE):
                self._hunter_shot(out, R.CAUSE_VOTE)
        else:
            self.public["rounds"].append({
                "day": self.day, "phase": "day", "pids": [], "causes": {},
            })

    def _sheriff_election(self, alive):
        """警长选举（task_LRS_030）。只在选举日跑一次。

        ★ 平票 / 全弃票 ⇒ 本局无警长（SHERIFF_TIE_NO_SHERIFF=True），不递归重选。
        ★ 用 self.sheriff_rng 之外的随机性？不 —— 选举本身不掷骰，
          只有 agent 内部用它自己的 rng；所以等价性不受影响。
        """
        cfg = self.cfg
        self.sheriff_elected = True
        collected = []
        for pid in alive:
            legal = A.legal_sheriff_votes(pid, alive,
                                          allow_abstain=cfg.VOTE_ALLOW_ABSTAIN)
            a = self._ask(pid, "day_sheriff_vote", legal)
            collected.append((pid, None if a["target"] < 0 else a["target"]))

        tally = R.tally_sheriff_votes(collected, alive)
        elected = None
        if not tally["tie"] and tally["eliminated"] is not None:
            elected = tally["eliminated"]
        elif tally["tie"] and not cfg.SHERIFF_TIE_NO_SHERIFF and tally["top"]:
            # 备选口径：并列者里随机出一人（用 sheriff_rng，独立序列）
            elected = self.sheriff_rng.choice(sorted(tally["top"]))
        self.sheriff = elected
        self._ev({"kind": "sheriff_elect", "day": self.day,
                  "counts": {str(k): v for k, v in tally["counts"].items()},
                  "top": tally["top"], "tie": tally["tie"],
                  "abstain": tally["abstain"],
                  "sheriff": elected})

    def _speech_order(self, alive):
        """从上一出局者的下一位开始顺时针；首日从 DAY_FIRST_SPEAKER_PID 开始。

        ★ task_LRS_030：警长存活且 SHERIFF_SPEAK_FIRST=True 时，把警长提到队首。
          **只重排、不改候选集** ⇒ 宽度线四档（1/6/12/36）口径完全不受影响。
          ★★ cfg.SHERIFF=False 时走原路径，逐位等价。
        """
        n = self.n_players
        if self.last_eliminated is None:
            start = C.DAY_FIRST_SPEAKER_PID
        else:
            start = (self.last_eliminated + 1) % n
        aset = set(alive)
        order = [(start + k) % n for k in range(n) if (start + k) % n in aset]
        cfg = self.cfg
        if cfg.SHERIFF and cfg.SHERIFF_SPEAK_FIRST and self.sheriff is not None:
            if self.sheriff in aset:
                order = [self.sheriff] + [p for p in order if p != self.sheriff]
        return order

    def _hunter_shot(self, hunter_pid, cause):
        """猎人死亡结算后立即开枪。"""
        alive = self.alive_pids()
        legal = A.legal_shots(hunter_pid, alive)
        a = self._ask(hunter_pid, "hunter_shot", legal, note="dead_hunter")
        if a["type"] == A.SHOOT:
            tgt = a["target"]
            self._kill(tgt, R.CAUSE_SHOT, by=hunter_pid, phase="shot")
            self.public["rounds"].append({
                "day": self.day, "phase": "shot",
                "pids": [tgt], "causes": {str(tgt): R.CAUSE_SHOT},
            })

    # ==================== 基础操作 ====================

    def alive_pids(self):
        return [p.pid for p in self.players if p.alive]

    def _find_alive(self, role):
        for p in self.players:
            if p.alive and p.role == role:
                return p.pid
        return None

    def _kill(self, pid, cause, by=None, phase=None):
        pl = self.players[pid]
        if not pl.alive:
            return
        pl.alive = False
        pl.death_cause = cause
        pl.death_day = self.day
        pl.death_phase = phase
        d = {"kind": "death", "day": self.day, "phase": phase,
             "pid": pid, "cause": cause, "by": by}
        self._ev(d)
        self.public["deaths"].append(
            {"day": self.day, "phase": phase, "pid": pid, "cause": cause, "by": by})
        # ---- task_LRS_030：警长死亡 ⇒ 警徽流转 ----
        #   ★ 必须放在"标记死亡之后"，因为继承人必须看**死亡后**的存活名单。
        #   ★ 递归保护：_badge_transfer 里不再调 _kill，只改 self.sheriff。
        if self.cfg.SHERIFF and self.sheriff == pid and not self.badge_dropped:
            self._badge_transfer(pid)

    def _badge_transfer(self, dead_sheriff):
        """警长死亡时流转警徽（task_LRS_030）。

        口径（config.BADGE_MODE）：
          designate（默认）：由临死警长指定一个存活继承人；无可指定 ⇒ 流失
          drop             ：直接流失
          reelect          ：在存活者里重选一次
        ★ 警徽**不会**留在死人身上：一旦死人，self.sheriff 先置 None 再按口径决定。
        """
        cfg = self.cfg
        self.sheriff = None
        mode = cfg.BADGE_MODE

        if mode == "drop":
            self.badge_dropped = True
            self._ev({"kind": "badge_drop", "day": self.day,
                      "from": dead_sheriff, "reason": "BADGE_MODE=drop"})
            return

        alive = self.alive_pids()

        if mode == "reelect":
            if not alive:
                self.badge_dropped = True
                self._ev({"kind": "badge_drop", "day": self.day,
                          "from": dead_sheriff, "reason": "无人可重选"})
                return
            collected = []
            for pid in alive:
                legal = A.legal_sheriff_votes(pid, alive,
                                              allow_abstain=cfg.VOTE_ALLOW_ABSTAIN)
                a = self._ask(pid, "day_sheriff_vote", legal)
                collected.append((pid, None if a["target"] < 0 else a["target"]))
            tally = R.tally_sheriff_votes(collected, alive)
            new_sheriff = None
            if not tally["tie"] and tally["eliminated"] is not None:
                new_sheriff = tally["eliminated"]
            elif tally["tie"] and not cfg.SHERIFF_TIE_NO_SHERIFF and tally["top"]:
                new_sheriff = self.sheriff_rng.choice(sorted(tally["top"]))
            self.sheriff = new_sheriff
            self._ev({"kind": "badge_reelect", "day": self.day,
                      "from": dead_sheriff, "sheriff": new_sheriff,
                      "tie": tally["tie"]})
            if new_sheriff is None:
                self.badge_dropped = True
            return

        # ---- designate（默认）----
        legal = A.legal_badge(dead_sheriff, alive)
        a = self._ask(dead_sheriff, "sheriff_death_badge", legal, note="dead_sheriff")
        if a["type"] == A.BADGE:
            self.sheriff = a["target"]
            self._ev({"kind": "badge_transfer", "day": self.day,
                      "from": dead_sheriff, "to": a["target"]})
        else:
            self.badge_dropped = True
            self._ev({"kind": "badge_drop", "day": self.day,
                      "from": dead_sheriff, "reason": "警长选择流失"})

    def _settle_winner(self, when):
        roles_alive = {p.pid: p.role for p in self.players if p.alive}
        w = R.check_winner(roles_alive, self.cfg.WIN_MODE)
        if w is None:
            return False
        self.winner = w
        self.winner_reason = R.winner_reason(roles_alive, self.cfg.WIN_MODE)
        self._ev({"kind": "end", "winner": w, "reason": self.winner_reason,
                  "when": when, "day": self.day})
        return True

    # ==================== 决策与视图 ====================

    def _ask(self, pid, phase, legal, note=None):
        self.steps += 1
        view = self._view(pid, phase)
        action = self.agents[pid].act(view, legal)
        if action not in legal:
            # 兜底：不合法动作退化为合法集第一个，并计数
            # —— 同时写一条事件，避免"静默兜底"把数据悄悄污染（下游能查到）
            self.invalid_actions += 1
            self._ev({"kind": "invalid_action", "day": self.day, "phase": phase,
                      "actor": pid, "action": action, "fallback": legal[0]})
            action = legal[0]
        self._ev({"kind": "action", "day": self.day, "phase": phase,
                  "actor": pid, "action": action, "code": A.encode(action),
                  "note": note})
        return action

    def _view(self, pid, phase):
        pl = self.players[pid]
        alive = self.alive_pids()
        v = {
            "pid": pid,
            "day": self.day,
            "night_no": self.night_no,
            "phase": phase,
            "n_players": self.n_players,
            "alive": alive,
            "my_role": pl.role,
            "my_faction": faction_of(pl.role),
            "teammates": [],
            "public": self.public,
        }
        if pl.role == WOLF:
            v["teammates"] = [p for p in alive
                              if p != pid and self.players[p].role == WOLF]
        elif pl.role == SEER:
            v["seer_results"] = dict(self.seer_results)
        elif pl.role == WITCH:
            v["witch"] = {
                "has_antidote": not self.witch_antidote_used,
                "has_poison": not self.witch_poison_used,
                "night_victim": self.night_victim,
            }
        # ---- task_LRS_030：警长是**公开身份**，放顶层（不放 public，避免改 public 结构）----
        #   ★★ 只在 cfg.SHERIFF 时加这个键 ⇒ SHERIFF=False 时 view 与改动前**键集合相同**，
        #      这是逐位等价的一部分（features/honest 都不会看到多余字段）。
        if self.cfg.SHERIFF:
            v["sheriff"] = self.sheriff
            v["sheriff_elected"] = self.sheriff_elected
        return v

    # ==================== 记录 ====================

    def _ev(self, d):
        if self.record:
            if "t" not in d:
                d["t"] = self.steps
            self.events.append(d)

    def _record(self):
        rec = {
            "game_id": self.game_id,
            "cell": self.cell,
            "seed": self.seed,
            "win_mode": self.cfg.WIN_MODE,
            "board": {r: n for r, n in self.cfg.BOARD},
            "n_players": self.n_players,
            "agents": [getattr(a, "name", a.__class__.__name__) for a in self.agents],
            "roles": {str(p.pid): p.role for p in self.players},
            "winner": self.winner,
            "winner_reason": self.winner_reason,
            "days": self.day,
            "steps": self.steps,
            "tie_days": self.tie_days,
            "invalid_actions": self.invalid_actions,
            "players": [p.to_dict() for p in self.players],
            "events": self.events if self.record else [],
        }
        # ---- task_LRS_030：只在开启时写警长字段 ⇒ SHERIFF=False 的记录与改动前**逐字节相同** ----
        if self.cfg.SHERIFF:
            rec["sheriff"] = self.sheriff
            rec["sheriff_elected"] = self.sheriff_elected
            rec["badge_dropped"] = self.badge_dropped
        return rec
