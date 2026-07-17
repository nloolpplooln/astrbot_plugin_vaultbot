"""快照读写：封装 player_snapshot / nickname_index 的常用操作。"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from .db import get_conn


def save_snapshot(
    nickname: str,
    raw: Dict[str, Any],
    parsed: Dict[str, Any],
    source_url: str,
) -> int:
    """写入一份快照并更新昵称索引，返回 snapshot id。"""
    now = int(time.time())
    rid = parsed.get("rockstar_id")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO player_snapshot(nickname, rockstar_id, raw_json, parsed_json, source_url, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (nickname, rid, json.dumps(raw, ensure_ascii=False),
             json.dumps(parsed, ensure_ascii=False), source_url, now),
        )
        snap_id = cur.lastrowid
        conn.execute(
            "INSERT INTO nickname_index(nickname, latest_snapshot, updated_at) VALUES (?,?,?)"
            " ON CONFLICT(nickname) DO UPDATE SET latest_snapshot=excluded.latest_snapshot,"
            " updated_at=excluded.updated_at",
            (nickname, snap_id, now),
        )
    return snap_id


def get_latest(nickname: str) -> Optional[Dict[str, Any]]:
    """取昵称最近一份快照（不判断有效期）。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT s.* FROM nickname_index n JOIN player_snapshot s ON s.id = n.latest_snapshot"
            " WHERE n.nickname = ?",
            (nickname,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_by_index(snapshot_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM player_snapshot WHERE id = ?", (snapshot_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_history(nickname: str, limit: int = 50) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, nickname, rockstar_id, source_url, created_at FROM player_snapshot"
            " WHERE nickname = ? ORDER BY created_at DESC LIMIT ?",
            (nickname, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _row_to_dict(row: Any) -> Dict[str, Any]:
    d = dict(row)
    for k in ("raw_json", "parsed_json"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


# ---- 凭证持久化（authorization / refresh_cookies）----

def save_credential(key: str, value: Any) -> None:
    """存一条凭证。value 为 dict 时序列化为 JSON。"""
    now = int(time.time())
    raw = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO credential(key, value, updated_at) VALUES (?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, raw, now),
        )


def load_credential(key: str) -> Optional[Any]:
    """读一条凭证；能解析为 JSON 则返回对象，否则返回原字符串。"""
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM credential WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    val = row["value"]
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val
