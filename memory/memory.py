# memory.py
# 轻量 MemoryStore：session（带 TTL）+ profile（长期），key-value 存取

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

# 默认存储路径（项目目录下）
_DEFAULT_DB = Path(__file__).resolve().parent / "memory.db"
# Session TTL：24 小时（秒）
SESSION_TTL_SEC = 24 * 3600


def _get_conn() -> sqlite3.Connection:
    """获取 DB 连接。"""
    conn = sqlite3.connect(str(_DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    """初始化表结构。"""
    conn = _get_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                data TEXT NOT NULL DEFAULT '{}',
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS profiles (
                user_id TEXT PRIMARY KEY,
                data TEXT NOT NULL DEFAULT '{}',
                updated_at REAL NOT NULL
            );
        """)
        conn.commit()
    finally:
        conn.close()


def get_session(session_id: str) -> dict:
    """
    获取 session 数据。若过期（>24h 未更新）则返回空 dict。
    数据结构示例（由 orchestrator 在每次成功回答后写入）：
        {
            "last_dt": "2017-12-03",        # 本轮 dt
            "last_days": 9,                  # 本轮 days
            "last_intent": "overview_daily", # 本轮 intent 或首个 tool_key
            "last_tool_keys": ["overview_daily"],
            "last_metric_focus": "uv_to_buyer",  # 仅 diagnose 时
            "last_answer_summary": "headline | ev1 | ev2",  # 不含 df
        }
    """
    _init_db()
    conn = _get_conn()
    try:
        cur = conn.execute(
            "SELECT data, updated_at FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cur.fetchone()
        if row is None:
            return {}
        data_str, updated_at = row["data"], row["updated_at"]
        if time.time() - updated_at > SESSION_TTL_SEC:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.commit()
            return {}
        return json.loads(data_str) if data_str else {}
    finally:
        conn.close()


def update_session(session_id: str, patch: dict) -> None:
    """
    merge 更新 session。patch 会深度合并到现有数据。
    示例：update_session("s1", {"last_question": "看看趋势"})
    """
    _init_db()
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT data FROM sessions WHERE session_id = ?", (session_id,))
        row = cur.fetchone()
        existing = json.loads(row["data"]) if row and row["data"] else {}
        merged = _deep_merge(existing, patch)
        now = time.time()
        conn.execute(
            """INSERT INTO sessions (session_id, data, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET data=?, updated_at=?""",
            (session_id, json.dumps(merged, ensure_ascii=False), now, json.dumps(merged, ensure_ascii=False), now),
        )
        conn.commit()
    finally:
        conn.close()


def get_profile(user_id: str) -> dict:
    """
    获取 profile 数据（长期保存）。
    数据结构示例：
        {
            "preferences": {"default_days": 9, "language": "zh"},
            "history_summary": [],
        }
    """
    _init_db()
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT data FROM profiles WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if row is None:
            return {}
        return json.loads(row["data"]) if row["data"] else {}
    finally:
        conn.close()


def update_profile(user_id: str, patch: dict) -> None:
    """
    merge 更新 profile。
    示例：update_profile("u1", {"preferences": {"default_days": 7}})
    """
    _init_db()
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT data FROM profiles WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        existing = json.loads(row["data"]) if row and row["data"] else {}
        merged = _deep_merge(existing, patch)
        now = time.time()
        conn.execute(
            """INSERT INTO profiles (user_id, data, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET data=?, updated_at=?""",
            (user_id, json.dumps(merged, ensure_ascii=False), now, json.dumps(merged, ensure_ascii=False), now),
        )
        conn.commit()
    finally:
        conn.close()


def _deep_merge(base: dict, patch: dict) -> dict:
    """将 patch 深度合并到 base（patch 覆盖 base 同键值）。"""
    out = dict(base)
    for k, v in patch.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def demo() -> None:
    """验收：能读写 session/profile。"""
    import os
    test_db = Path(__file__).resolve().parent / "memory_test.db"
    global _DEFAULT_DB
    old, _DEFAULT_DB = _DEFAULT_DB, test_db
    try:
        # Session
        update_session("s1", {"last_question": "看看趋势", "count": 1})
        s = get_session("s1")
        assert s.get("last_question") == "看看趋势"
        assert s.get("count") == 1
        update_session("s1", {"count": 2})
        s = get_session("s1")
        assert s.get("count") == 2
        assert s.get("last_question") == "看看趋势"
        print("session read/write OK")

        # Profile
        update_profile("u1", {"preferences": {"days": 9}})
        p = get_profile("u1")
        assert p.get("preferences", {}).get("days") == 9
        update_profile("u1", {"preferences": {"days": 7, "lang": "zh"}})
        p = get_profile("u1")
        assert p["preferences"]["days"] == 7
        assert p["preferences"]["lang"] == "zh"
        print("profile read/write OK")
    finally:
        _DEFAULT_DB = old
        if test_db.exists():
            test_db.unlink()


if __name__ == "__main__":
    demo()
