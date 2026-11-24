# -*- coding: utf-8 -*-
"""极简本地档案：成就、设置、战绩。两个游戏共用，文件在各游戏目录 save_data.json。"""
import json
from pathlib import Path

ACH = {
    "liarsdice": [
        ("first_game", "第一局", "完成一局吹牛骰子"),
        ("first_win", "初出茅庐", "赢下第一局"),
        ("wins5", "赌王", "累计赢 5 局"),
        ("wins10", "赌神", "累计赢 10 局"),
        ("streak3", "手气正旺", "连胜 3 局"),
        ("challenge_win", "开得漂亮", "开盅成功抓出假叫"),
        ("five_same", "豹子", "自己五颗骰子同点"),
        ("beat_all", "通关", "击败四个角色各至少一次"),
        ("easter", "无聊透顶", "连续点击标题 10 次"),
    ],
    "werewolf": [
        ("first_game", "入夜", "完成一局狼人杀"),
        ("first_win", "活到最后", "作为真人阵营赢下一局"),
        ("win3", "老手", "作为真人阵营赢 3 局"),
        ("seer_wolf", "验狼", "作为预言家验到狼人"),
        ("witch_save", "妙手回春", "作为女巫用解药救人"),
        ("hunter_wolf", "枪法如神", "作为猎人开枪带走狼人"),
        ("ai10", "旁观者", "纯 AI 旁观累计 10 局"),
        ("easter", "月圆之夜", "连续点击月亮 10 次"),
    ],
}
ACH_NAME = {g: {i: n for i, n, d in v} for g, v in ACH.items()}
ACH_DESC = {g: {i: d for i, n, d in v} for g, v in ACH.items()}


class Profile:
    def __init__(self, game, folder):
        self.game = game
        self.path = Path(folder) / "save_data.json"
        self.data = {
            "games": 0, "wins": 0, "streak": 0, "best_streak": 0,
            "beaten": [], "achievements": {}, "title_clicks": 0,
            "settings": {"music": True, "sfx": True,
                         "special": True, "fast": False},
        }
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        self.data[k] = v
                    if not isinstance(self.data.get("settings"), dict):
                        self.data["settings"] = {}
                    for k, v in (("music", True), ("sfx", True),
                                 ("special", True), ("fast", False)):
                        self.data["settings"].setdefault(k, v)
        except Exception:
            pass

    def save(self):
        try:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False,
                                            indent=1), encoding="utf-8")
        except Exception:
            pass

    def setting(self, key, default=True):
        return bool(self.data["settings"].get(key, default))

    def set_setting(self, key, value):
        self.data["settings"][key] = bool(value)
        self.save()

    def setting_raw(self, key, default=None):
        return self.data["settings"].get(key, default)

    def set_setting_raw(self, key, value):
        self.data["settings"][key] = value
        self.save()

    def unlock(self, aid):
        if self.data["achievements"].get(aid):
            return None
        self.data["achievements"][aid] = True
        self.save()
        return ACH_NAME[self.game].get(aid, aid)

    def click_title(self):
        self.data["title_clicks"] = int(self.data.get("title_clicks", 0)) + 1
        if self.data["title_clicks"] >= 10:
            self.data["title_clicks"] = 0
            return self.unlock("easter")
        self.save()
        return None

    def record_game(self, won=False, opponent=None, challenge_win=False,
                    five_same=False, ai_only=False):
        self.data["games"] = int(self.data.get("games", 0)) + 1
        out = []
        if self.data["games"] == 1:
            out.append(self.unlock("first_game"))
        if won:
            self.data["wins"] = int(self.data.get("wins", 0)) + 1
            self.data["streak"] = int(self.data.get("streak", 0)) + 1
            self.data["best_streak"] = max(int(self.data.get("best_streak", 0)),
                                           self.data["streak"])
            if self.data["wins"] >= 1:
                out.append(self.unlock("first_win"))
            if self.data["wins"] >= 5:
                out.append(self.unlock("wins5"))
            if self.data["wins"] >= 10:
                out.append(self.unlock("wins10"))
            if self.data["streak"] >= 3:
                out.append(self.unlock("streak3"))
            if opponent:
                beaten = set(self.data.get("beaten") or [])
                beaten.add(opponent)
                self.data["beaten"] = sorted(beaten)
                if len(beaten) >= 4:
                    out.append(self.unlock("beat_all"))
        else:
            self.data["streak"] = 0
        if challenge_win:
            out.append(self.unlock("challenge_win"))
        if five_same:
            out.append(self.unlock("five_same"))
        if ai_only:
            ai = int(self.data.get("ai_games", 0)) + 1
            self.data["ai_games"] = ai
            if ai >= 10:
                out.append(self.unlock("ai10"))
        self.save()
        return [x for x in out if x]
