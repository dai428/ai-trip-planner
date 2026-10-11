"""攻略库服务。

【它在项目里的位置】
    夹在"数据文件"和"行程生成"之间：
        数据层  app/data/guides/*.json（结构化攻略）
        ↑↓
        本模块  读进来 → 摊平成检索语料 → 提供两条访问路径
        ↑↓
        业务层  trip_service 通过 retrieve() 拿到"有出处的素材"

【两条访问路径，各有用途】
    retrieve()  按语义找"最相关的几条"     —— 用于给行程条目挂出处（RAG）
    get_guide() 拿某城市的完整结构化攻略   —— 用于批量取景点/美食（直接生成）
"""

from __future__ import annotations

import json
from functools import lru_cache

from app import config
from app.rag.retriever import Doc, HybridRetriever, Retrieved


@lru_cache(maxsize=1)
def load_guides() -> dict[str, dict]:
    """把 10 个城市的攻略 JSON 全读进内存（只读一次）。"""
    guides: dict[str, dict] = {}
    for path in sorted(config.GUIDE_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        guides[data["city"]] = data
    return guides


def get_guide(city: str) -> dict | None:
    """取某城市的完整结构化攻略。"""
    return load_guides().get(city)


def supported_guide_cities() -> list[str]:
    """有攻略库覆盖的城市。"""
    return sorted(load_guides().keys())


def build_docs() -> list[Doc]:
    """把结构化攻略"摊平"成一条条自然语言语料。

    【为什么要摊平】
        结构化 JSON 适合"取值"，但不适合"检索"：
        用户问「第一次去北京看什么」，检索器要的是连续的自然语言。
        所以这里把每个景点/美食/住宿/贴士各写成一段话，
        同时把标签、区域、时长这些字段拼进文本 —— 让它们也能被关键词命中。
    """
    docs: list[Doc] = []

    for city, g in load_guides().items():
        # 概览
        docs.append(
            Doc(
                id=f"{city}#overview",
                city=city,
                kind="概览",
                title=f"{city}概览",
                text=f"{city}：{g.get('overview', '')}",
                meta={},
            )
        )

        # 景点
        for i, a in enumerate(g.get("attractions", [])):
            docs.append(
                Doc(
                    id=f"{city}#attr-{i}",
                    city=city,
                    kind="景点",
                    title=a["name"],
                    text=(
                        f"{city}景点：{a['name']}，位于{a.get('area', '')}。"
                        f"标签：{'/'.join(a.get('tags', []))}。"
                        f"建议游玩时长：{a.get('duration', '')}。"
                        f"{a.get('desc', '')}"
                    ),
                    meta=dict(a),
                )
            )

        # 美食
        for i, fd in enumerate(g.get("foods", [])):
            docs.append(
                Doc(
                    id=f"{city}#food-{i}",
                    city=city,
                    kind="美食",
                    title=fd["name"],
                    text=(
                        f"{city}美食：{fd['name']}，常见于{fd.get('area', '')}。"
                        f"{fd.get('desc', '')}"
                    ),
                    meta=dict(fd),
                )
            )

        # 住宿
        for i, lg in enumerate(g.get("lodging", [])):
            docs.append(
                Doc(
                    id=f"{city}#stay-{i}",
                    city=city,
                    kind="住宿",
                    title=lg["area"],
                    text=(
                        f"{city}住宿：推荐住在{lg['area']}。"
                        f"{lg.get('desc', '')}参考价 {lg.get('price', '')}。"
                    ),
                    meta=dict(lg),
                )
            )

        # 贴士
        for i, tip in enumerate(g.get("tips", [])):
            docs.append(
                Doc(
                    id=f"{city}#tip-{i}",
                    city=city,
                    kind="贴士",
                    title=f"{city}贴士 {i + 1}",
                    text=f"{city}实用贴士：{tip}",
                    meta={},
                )
            )

    return docs


@lru_cache(maxsize=1)
def get_retriever() -> HybridRetriever:
    """检索器单例（建索引有成本，全局只建一次）。"""
    return HybridRetriever(
        build_docs(),
        enable_vector=config.RAG_ENABLE_VECTOR,
        enable_rerank=config.RAG_ENABLE_RERANK,
        rrf_k=config.RRF_K,
        vector_model=config.EMBEDDING_MODEL_NAME,
        rerank_model=config.RERANK_MODEL_NAME,
    )


def retrieve(query: str, *, city: str | None = None, top_k: int = 4) -> list[Retrieved]:
    """检索攻略：返回带出处与命中通道的结果。"""
    return get_retriever().search(query, city=city, top_k=top_k)


def corpus_stats() -> dict:
    """语料统计，用于 /api/meta/rag 自检。"""
    docs = build_docs()
    by_kind: dict[str, int] = {}
    for d in docs:
        by_kind[d.kind] = by_kind.get(d.kind, 0) + 1
    return {
        "cities": len(load_guides()),
        "documents": len(docs),
        "by_kind": by_kind,
        "channels": get_retriever().channels,
    }
