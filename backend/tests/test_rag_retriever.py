"""检索层测试：验证"混合检索"的核心契约。

重点不是"分数对不对"（BM25 分数没有绝对含义），
而是"该召回的能不能被召回""该隔离的有没有串城"。
"""

from app.services import guide_service


def test_corpus_loaded():
    stats = guide_service.corpus_stats()
    assert stats["cities"] == 10
    assert stats["documents"] > 100
    assert "bm25" in stats["channels"]


def test_retrieve_returns_relevant_attraction():
    hits = guide_service.retrieve("想看熊猫", city="成都", top_k=5)
    titles = [h.doc.title for h in hits if h.doc.kind == "景点"]
    assert "成都大熊猫繁育研究基地" in titles


def test_retrieve_respects_city_isolation():
    """跨城市污染是 RAG 最隐蔽的 bug：问成都却返回大理的景点。"""
    hits = guide_service.retrieve("古城 骑行", city="大理", top_k=10)
    assert hits, "应至少召回到一条"
    assert all(h.doc.city == "大理" for h in hits)


def test_retrieve_empty_city_returns_nothing():
    assert guide_service.retrieve("随便问问", city="不存在的城市") == []


def test_rrf_gives_credit_to_multi_channel_hits():
    """同时被两路召回的结果，应排在只被单路召回的前面。"""
    from app.rag.retriever import rrf_fuse

    fused = rrf_fuse([("bm25", [1, 2, 3]), ("cos", [1, 5, 6])], k=60)
    best = max(fused, key=fused.get)
    assert best == 1
