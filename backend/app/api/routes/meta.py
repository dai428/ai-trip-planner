"""元信息接口：城市清单、城市搜索、偏好标签、依赖自检。

【为什么要 /api/meta/rag 和 /api/meta/amap】
    这两块能力都是"看不见"的：跑得好不好，光看接口返回的行程判断不出来。
    把语料条数、启用的召回通道、高德是否真的接上暴露成只读接口，
    排查时一眼就能看出"是索引没建起来/Key 没读到，还是真的没命中"。
"""

from fastapi import APIRouter, Query

from app.services import amap_service, guide_service
from app.services.city_service import list_cities_meta, search_cities

router = APIRouter(prefix="/api/meta", tags=["元信息"])

# 前端偏好选择器用的标签（与攻略库里的 tags 对齐）
PREFERENCES = ["历史", "人文", "自然", "美食", "海滨", "古镇", "亲子", "摄影", "休闲", "夜景"]


@router.get("/cities", summary="支持的目的地")
def get_cities() -> dict:
    return {"cities": list_cities_meta()}


@router.get("/cities/search", summary="搜索城市（全国）")
def search_city(keyword: str = Query("", alias="q", description="关键词，留空返回本地全部城市"),
                limit: int = Query(12, ge=1, le=30)) -> dict:
    """搜索框用：本地库命中优先，再用高德补全国城市。

    返回项带 `has_guide` —— 前端据此区分"有攻略支撑"与"仅能定位"。
    """
    return {"query": keyword, "results": [s.model_dump() for s in search_cities(keyword, limit)]}


@router.get("/amap", summary="高德接入自检")
def get_amap_status() -> dict:
    """暴露当前是"高德实测"还是"本地估算"，以及缓存条目数。"""
    return amap_service.status()


@router.get("/preferences", summary="可选偏好标签")
def get_preferences() -> dict:
    return {"preferences": PREFERENCES}


@router.get("/rag", summary="检索链路自检")
def get_rag_stats() -> dict:
    """返回语料规模与实际启用的召回通道。"""
    return guide_service.corpus_stats()
