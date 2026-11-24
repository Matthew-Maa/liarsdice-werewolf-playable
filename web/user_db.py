# -*- coding: utf-8 -*-
"""本地账号/记录/成就库（标准库 sqlite3）。

- 只存本机：对外游戏/用户数据/users.db
- 密码 PBKDF2-HMAC-SHA256 + 随机盐；cookie 只存随机会话 token
- 记录正文仍在 JSONL；这里只存索引（游戏/时间/胜负/摘要）
"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "用户数据"
DB_PATH = Path(os.environ.get("RA027_USER_DB") or (DATA_DIR / "users.db"))
DATA_DIR = DB_PATH.parent
_LOCK = threading.RLock()
_USER_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{2,20}$")
_ITER = 200_000


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init():
    with _LOCK, _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          nick TEXT NOT NULL,
          salt TEXT NOT NULL,
          pass_hash TEXT NOT NULL,
          created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions(
          token TEXT PRIMARY KEY,
          user_id INTEGER NOT NULL,
          created TEXT NOT NULL,
          expires TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS games(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          game TEXT NOT NULL,
          ts TEXT NOT NULL,
          result TEXT NOT NULL,
          role TEXT,
          summary TEXT,
          record_path TEXT
        );
        CREATE TABLE IF NOT EXISTS achievements(
          user_id INTEGER NOT NULL,
          code TEXT NOT NULL,
          ts TEXT NOT NULL,
          UNIQUE(user_id, code)
        );
        CREATE INDEX IF NOT EXISTS idx_games_user ON games(user_id, id DESC);
        """)
    return True


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               bytes.fromhex(salt), _ITER).hex()


def _public(row):
    return {"id": int(row["id"]), "username": row["username"],
            "nick": row["nick"], "created": row["created"]}


def register(username, password, nick=""):
    username = (username or "").strip()
    nick = (nick or username).strip()[:20]
    if not _USER_RE.match(username):
        raise ValueError("用户名 2~20 位，只能是中文/字母/数字/下划线")
    if len(password or "") < 6:
        raise ValueError("密码至少 6 位")
    salt = secrets.token_hex(16)
    h = _hash(password, salt)
    with _LOCK, _conn() as c:
        try:
            c.execute("INSERT INTO users(username,nick,salt,pass_hash,created) VALUES(?,?,?,?,?)",
                      (username, nick, salt, h, _now()))
        except sqlite3.IntegrityError:
            raise ValueError("这个用户名已经注册了")
        row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return _public(row)


def login(username, password):
    with _LOCK, _conn() as c:
        row = c.execute("SELECT * FROM users WHERE username=?",
                        ((username or "").strip(),)).fetchone()
        if not row:
            return None
        if not hmac.compare_digest(row["pass_hash"], _hash(password or "", row["salt"])):
            return None
        return _public(row)


def create_session(user_id, days=30):
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=days)
    with _LOCK, _conn() as c:
        c.execute("INSERT INTO sessions(token,user_id,created,expires) VALUES(?,?,?,?)",
                  (token, int(user_id), now.isoformat(timespec="seconds"),
                   exp.isoformat(timespec="seconds")))
    return token


def user_by_token(token):
    if not token:
        return None
    with _LOCK, _conn() as c:
        row = c.execute("""SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
                           WHERE s.token=? AND s.expires>?""",
                        (token, datetime.now(timezone.utc).isoformat(timespec="seconds"))).fetchone()
        return _public(row) if row else None


def delete_session(token):
    if not token:
        return
    with _LOCK, _conn() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))


def record_game(user_id, game, result, role="", summary="", record_path=""):
    with _LOCK, _conn() as c:
        c.execute("""INSERT INTO games(user_id,game,ts,result,role,summary,record_path)
                     VALUES(?,?,?,?,?,?,?)""",
                  (int(user_id), game, _now(), result, role, summary[:200],
                   str(record_path or "")))


def award(user_id, code, name=""):
    with _LOCK, _conn() as c:
        try:
            c.execute("INSERT INTO achievements(user_id,code,ts) VALUES(?,?,?)",
                      (int(user_id), code, _now()))
            return True
        except sqlite3.IntegrityError:
            return False


def list_games(user_id, limit=60):
    with _LOCK, _conn() as c:
        rows = c.execute("""SELECT game,ts,result,role,summary FROM games
                            WHERE user_id=? ORDER BY id DESC LIMIT ?""",
                         (int(user_id), int(limit))).fetchall()
        return [dict(r) for r in rows]


def list_achievements(user_id):
    with _LOCK, _conn() as c:
        rows = c.execute("SELECT code,ts FROM achievements WHERE user_id=? ORDER BY ts",
                         (int(user_id),)).fetchall()
        return [dict(r) for r in rows]


def stats(user_id, game=None):
    with _LOCK, _conn() as c:
        if game:
            rows = c.execute("""SELECT result,COUNT(*) n FROM games
                                WHERE user_id=? AND game=? GROUP BY result""",
                             (int(user_id), game)).fetchall()
        else:
            rows = c.execute("""SELECT result,COUNT(*) n FROM games
                                WHERE user_id=? GROUP BY result""",
                             (int(user_id),)).fetchall()
        d = {r["result"]: r["n"] for r in rows}
        wins = d.get("win", 0)
        losses = d.get("loss", 0)
        return {"games": sum(d.values()), "wins": wins, "losses": losses,
                "aborted": d.get("aborted", 0),
                "win_rate": (wins / (wins + losses)) if (wins + losses) else None}


def backup_game_data():
    with _LOCK, _conn() as c:
        games = [dict(r) for r in c.execute(
            "SELECT user_id,game,ts,result,role,summary FROM games ORDER BY id").fetchall()]
        ach = [dict(r) for r in c.execute(
            "SELECT user_id,code,ts FROM achievements ORDER BY ts").fetchall()]
        return {"games": games, "achievements": ach}


def clear_game_data():
    with _LOCK, _conn() as c:
        g = c.execute("SELECT COUNT(*) n FROM games").fetchone()["n"]
        c.execute("DELETE FROM games")
        c.execute("DELETE FROM achievements")
        return int(g)
