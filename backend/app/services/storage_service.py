"""存储服务：把行程与分享落盘（SQLite）。

【为什么用 SQLite 而不是 Redis / PostgreSQL】
    这个项目要"单机一键跑起来"，SQLite 是唯一零部署的选项：
    没有服务进程、没有连接串、没有账号，一个文件就是整个数据库。
    真到了高并发场景，只要把本模块换成 SQLAlchemy + PG，
    上层业务代码一行都不用动 —— 这正是把存储隔离成一层的原因。

【为什么每次请求都重开连接】
    sqlite3 的连接默认不能跨线程用，而 FastAPI 的同步路由跑在线程池里。
    重开连接开销极小（本地文件），换来的是"绝对不会有跨线程问题"。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from app import config
from app.models.trip import TripPlan, TripSummary

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
    id         TEXT PRIMARY KEY,
    cities     TEXT NOT NULL,
    days       INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    payload    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shares (
    token      TEXT PRIMARY KEY,
    trip_id    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trips_created ON trips(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_shares_trip   ON shares(trip_id);
"""


def _connect() -> sqlite3.Connection:
    config.DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """建表（幂等，服务启动时调一次）。"""
    with _connect() as conn:
        conn.executescript(_SCHEMA)


# ==================== 行程 ====================

def save_trip(plan: TripPlan) -> None:
    """保存（或覆盖）一份行程。"""
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO trips (id, cities, days, created_at, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                plan.id,
                json.dumps(plan.cities, ensure_ascii=False),
                plan.days,
                plan.created_at.isoformat(),
                plan.model_dump_json(),
            ),
        )


def get_trip(trip_id: str) -> TripPlan | None:
    """按 ID 取行程，不存在返回 None。"""
    with _connect() as conn:
        row = conn.execute("SELECT payload FROM trips WHERE id = ?", (trip_id,)).fetchone()
    return TripPlan.model_validate_json(row["payload"]) if row else None


def list_trips(limit: int = 50) -> list[TripSummary]:
    """历史列表（按生成时间倒序，只返回摘要）。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, cities, days, created_at FROM trips ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        TripSummary(
            id=r["id"],
            cities=json.loads(r["cities"]),
            days=r["days"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )
        for r in rows
    ]


def delete_trip(trip_id: str) -> bool:
    """删除行程及其所有分享链接。"""
    with _connect() as conn:
        conn.execute("DELETE FROM shares WHERE trip_id = ?", (trip_id,))
        cur = conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
    return cur.rowcount > 0


# ==================== 分享 ====================

def create_share(token: str, trip_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO shares (token, trip_id, created_at) VALUES (?, ?, ?)",
            (token, trip_id, datetime.now().isoformat()),
        )


def get_share_trip_id(token: str) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT trip_id FROM shares WHERE token = ?", (token,)).fetchone()
    return row["trip_id"] if row else None


def list_shares(trip_id: str) -> list[str]:
    with _connect() as conn:
        rows = conn.execute("SELECT token FROM shares WHERE trip_id = ?", (trip_id,)).fetchall()
    return [r["token"] for r in rows]
