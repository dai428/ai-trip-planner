"""分享服务：给一份行程发一张"只读门票"。

【为什么要 token 而不是直接用行程 ID】
    行程 ID 是自增/顺序可猜的；分享链接一旦外泄，别人可以顺着 ID 遍历你的所有行程。
    分享 token 独立生成、随机、可撤销 —— 撤销只删 token，不动行程本身。
"""

from __future__ import annotations

import secrets
from datetime import datetime

from app.models.share import ShareInfo
from app.services import storage_service

_TOKEN_BYTES = 4          # 4 字节 → 8 位十六进制，约 43 亿种组合


def new_token() -> str:
    """生成一个随机分享短码。"""
    return secrets.token_hex(_TOKEN_BYTES)


def create_share(trip_id: str, base_url: str) -> ShareInfo:
    """为行程创建分享链接。"""
    token = new_token()
    storage_service.create_share(token, trip_id)
    return ShareInfo(
        token=token,
        trip_id=trip_id,
        url=f"{base_url.rstrip('/')}/s/{token}",
        created_at=datetime.now(),
    )


def resolve(token: str) -> str | None:
    """把 token 换回行程 ID。"""
    return storage_service.get_share_trip_id(token)


def share_path(token: str) -> str:
    """前端路由路径（前端按 /s/:token 渲染只读页）。"""
    return f"/s/{token}"
