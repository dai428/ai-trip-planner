"""候选池接口。

【为什么需要把候选池暴露出来】
    候选池是"看不见的中间层"：行程生成得好不好，可能只是因为候选池是空的
    （高德没配 Key、城市没覆盖、配额用尽），而用户完全无从判断。
    把它开成只读接口，排查时一眼就能看出"是候选不够，还是模型没选好"。

    refresh 单独做成 POST：它会真实外呼高德、消耗配额，
    不该被一个可以随手刷新的 GET 触发。
"""

from fastapi import APIRouter, HTTPException, Query

from app.services import amap_service, poi_pool_service

router = APIRouter(prefix="/api/poi", tags=["候选池"])


@router.get("/{city}", summary="查看某城市的候选池")
def get_pool(
    city: str,
    kind: str | None = Query(None, description="按类型过滤：景点 / 美食 / 住宿"),
    limit: int = Query(50, ge=1, le=500),
) -> dict:
    """返回候选（必要时按需补齐）。

    这里走的是 candidates()，即"缺了才去高德补"，不会每次都外呼。
    """
    if kind and kind not in poi_pool_service.KINDS:
        raise HTTPException(status_code=400, detail=f"kind 只能是 {poi_pool_service.KINDS}")

    raw = poi_pool_service.candidates(city, kind=kind, limit=limit)

    # `_source` 是内部字段名（下划线前缀表示"业务层自己加的展示字段"）。
    # 对外接口不该暴露带下划线的键 —— 那是内部约定，不是契约。
    # 在边界上翻译一次，前端就能用干净的 `source`。
    items = []
    by_kind: dict[str, int] = {}
    for it in raw:
        by_kind[it["kind"]] = by_kind.get(it["kind"], 0) + 1
        items.append({
            "name": it["name"],
            "kind": it["kind"],
            "area": it.get("area", ""),
            "address": it.get("address", ""),
            "category": it.get("category", ""),
            "tags": it.get("tags", []),
            "origin": it.get("origin", "amap"),
            "source": it.get("_source", ""),
        })

    return {
        "city": city,
        "count": len(items),
        "by_kind": by_kind,
        "amap_enabled": amap_service.enabled(),
        "items": items,
    }


@router.post("/{city}/refresh", summary="强制重新召回某城市")
def refresh_pool(city: str) -> dict:
    """清掉旧数据并重新宽召回。会真实消耗高德配额。"""
    if not amap_service.enabled():
        raise HTTPException(status_code=409, detail="未配置高德 Key，候选池无法召回")

    removed = poi_pool_service.clear(city)
    written = poi_pool_service.refresh(city)
    return {
        "city": city,
        "removed": removed,
        "written": written,
        "total": sum(written.values()),
    }
