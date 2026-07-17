"""SQLite 存储：建表与连接。

三张表（对齐空桑的数据模型）：
  player_snapshot  每次采集的一份玩家数据快照（含原始 JSON 与解析后字段）
  nickname_index   昵称 -> 最近快照，加速有效期缓存判断
  query_log        查询/申请记录，用于限速与历史
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS player_snapshot (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    nickname     TEXT NOT NULL,
    rockstar_id  INTEGER,
    raw_json     TEXT,           -- 抽取到的嵌入 JSON（原样保存，便于用新规则重解析）
    parsed_json  TEXT,           -- map_player 后的结构化字段
    source_url   TEXT,
    created_at   INTEGER NOT NULL -- unix 秒
);
CREATE INDEX IF NOT EXISTS idx_snapshot_nick ON player_snapshot(nickname, created_at);

CREATE TABLE IF NOT EXISTS nickname_index (
    nickname          TEXT PRIMARY KEY,
    latest_snapshot   INTEGER,   -- -> player_snapshot.id
    updated_at        INTEGER NOT NULL,
    FOREIGN KEY(latest_snapshot) REFERENCES player_snapshot(id)
);

CREATE TABLE IF NOT EXISTS query_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    nickname   TEXT NOT NULL,
    action     TEXT NOT NULL,    -- 'query' | 'post'
    client_ip  TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qlog_nick ON query_log(nickname, action, created_at);

CREATE TABLE IF NOT EXISTS credential (
    key        TEXT PRIMARY KEY,   -- 'authorization' | 'refresh_cookies'
    value      TEXT NOT NULL,      -- token 字符串 或 cookies 的 JSON
    updated_at INTEGER NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(get_settings().db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {get_settings().db_path}")
