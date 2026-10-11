"""混合检索器：BM25 + 字符 n-gram 余弦（+ 可选向量）→ RRF 融合 → 可选精排。

【检索流水线】
    ① 稀疏召回：BM25（jieba 分词）        —— 关键词精确命中
    ② 轻量稠密：字符二元组 TF-IDF 余弦    —— 抗分词错误、抗口语化
    ③ 稠密召回：向量（可选，需装模型）    —— 语义相近也能召回
    ④ RRF 融合：把多路排序按名次融合       —— 不依赖各路的分数尺度
    ⑤ 精排（可选）：Cross-encoder 逐对打分 —— 把最相关的顶到前面

【为什么用 RRF 而不是"加权求和"】
    BM25 的分数是十几到几十，余弦是 0~1，量纲完全不同，
    直接加权要先做归一化，而归一化对分布敏感、很脆。
    RRF 只用"名次"：score = Σ 1/(K + rank)，天然免调参、免归一化。
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from app.rag.tokenize import char_bigrams, tokenize

# ==================== 数据结构 ====================

@dataclass
class Doc:
    """一条被检索的语料（一段攻略的自然语言切分结果）。"""

    id: str
    city: str
    kind: str            # 景点 / 美食 / 住宿 / 贴士
    title: str
    text: str
    meta: dict = field(default_factory=dict)


@dataclass
class Retrieved:
    """一条检索结果。"""

    doc: Doc
    score: float
    channels: list[str] = field(default_factory=list)   # 被哪几路召回到

    @property
    def source(self) -> str:
        """给前端展示的出处，如「北京 · 景点 · 故宫博物院」。"""
        return f"{self.doc.city} · {self.doc.kind} · {self.doc.title}"


# ==================== ① BM25 ====================

class BM25:
    """BM25（Okapi）实现。

    【和 TF-IDF 的差别】
        TF-IDF 里词频线性增长：出现 10 次就是 10 倍权重。
        BM25 给词频加了饱和：出现 10 次和 20 次差别不大（k1 控制饱和度），
        同时按文档长度做惩罚（b 控制惩罚强度），避免长文档靠"词多"刷分。
    """

    def __init__(self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.n = len(corpus_tokens)
        self.doc_len = [len(d) for d in corpus_tokens]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.tf = [Counter(d) for d in corpus_tokens]

        df: Counter[str] = Counter()
        for d in corpus_tokens:
            df.update(set(d))
        # 加 1 平滑，避免高频词 idf 变成负数
        self.idf = {
            w: math.log(1 + (self.n - f + 0.5) / (f + 0.5))
            for w, f in df.items()
        }

    def scores(self, query_tokens: list[str]) -> list[float]:
        """返回每个文档的 BM25 分数（未归一化，只用于排序）。"""
        out = [0.0] * self.n
        if not self.avgdl:
            return out
        for w in set(query_tokens):
            idf = self.idf.get(w)
            if idf is None:
                continue
            for i, tf in enumerate(self.tf):
                f = tf.get(w, 0)
                if not f:
                    continue
                dl = self.doc_len[i]
                denom = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                out[i] += idf * f * (self.k1 + 1) / denom
        return out


# ==================== ② 字符二元组余弦 ====================

class BigramCosine:
    """字符二元组 TF-IDF 余弦相似度（轻量"稠密"近似）。

    【为什么不用真向量】
        真向量要下 100MB+ 的模型。这一路是为了在"没有模型"的环境里
        也能有一路与 BM25 互补的召回（不依赖分词，对错别字更宽容）。
    """

    def __init__(self, corpus_texts: list[str]) -> None:
        self.n = len(corpus_texts)
        self.doc_grams = [Counter(char_bigrams(t)) for t in corpus_texts]

        df: Counter[str] = Counter()
        for g in self.doc_grams:
            df.update(g.keys())
        self.idf = {
            w: math.log((self.n + 1) / (f + 1)) + 1
            for w, f in df.items()
        }
        self.vectors = [self._vectorize(c) for c in self.doc_grams]

    def _vectorize(self, counter: Counter[str]) -> dict[str, float]:
        vec = {g: (1 + math.log(c)) * self.idf.get(g, 1.0) for g, c in counter.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {g: v / norm for g, v in vec.items()}

    def scores(self, query: str) -> list[float]:
        q = self._vectorize(Counter(char_bigrams(query)))
        if not q:
            return [0.0] * self.n
        return [
            sum(w * vec.get(g, 0.0) for g, w in q.items())
            for vec in self.vectors
        ]


# ==================== ④ RRF 融合 ====================

def rrf_fuse(ranked_lists: list[tuple[str, list[int]]], k: int = 60) -> dict[int, float]:
    """Reciprocal Rank Fusion。

    入参：[(通道名, 按分数降序的文档下标列表), ...]
    返回：{文档下标: 融合分数}
    """
    fused: dict[int, float] = {}
    for _name, indices in ranked_lists:
        for rank, idx in enumerate(indices):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return fused


# ==================== 主检索器 ====================

class HybridRetriever:
    """混合检索器。构造时建索引，`search()` 时查。"""

    def __init__(
        self,
        docs: list[Doc],
        *,
        enable_vector: bool = False,
        enable_rerank: bool = False,
        rrf_k: int = 60,
        vector_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        rerank_model: str = "BAAI/bge-reranker-base",
    ) -> None:
        self.docs = docs
        self.rrf_k = rrf_k
        self._bm25 = BM25([tokenize(d.text) for d in docs]) if docs else None
        self._cos = BigramCosine([d.text for d in docs]) if docs else None

        # 可选：向量召回
        self._vector_on = False
        self._embedder = None
        self._doc_vecs: list[list[float]] = []
        if enable_vector and docs:
            try:  # pragma: no cover - 取决于环境
                from sentence_transformers import SentenceTransformer

                self._embedder = SentenceTransformer(vector_model, device="cpu")
                self._doc_vecs = self._embedder.encode(
                    [d.text for d in docs], normalize_embeddings=True
                ).tolist()
                self._vector_on = True
            except Exception:
                self._vector_on = False

        # 可选：Cross-encoder 精排
        self._rerank_on = False
        self._reranker = None
        if enable_rerank and docs:
            try:  # pragma: no cover - 取决于环境
                from sentence_transformers import CrossEncoder

                self._reranker = CrossEncoder(rerank_model, device="cpu")
                self._rerank_on = True
            except Exception:
                self._rerank_on = False

    # ---------- 对外信息 ----------
    @property
    def channels(self) -> list[str]:
        """当前实际启用的召回通道，用于接口自检与前端展示。"""
        names = ["bm25", "bigram-cosine"]
        if self._vector_on:
            names.append("vector")
        if self._rerank_on:
            names.append("cross-encoder-rerank")
        return names

    # ---------- 检索 ----------
    def search(self, query: str, *, city: str | None = None, top_k: int = 4) -> list[Retrieved]:
        """检索最相关的文档。city 非空时只在该城市内检索（防止跨城市污染）。"""
        if not self.docs:
            return []

        allowed = [
            i for i, d in enumerate(self.docs)
            if city is None or d.city == city
        ]
        if not allowed:
            return []
        allowed_set = set(allowed)

        ranked_lists: list[tuple[str, list[int]]] = []

        # ① BM25
        assert self._bm25 is not None
        bm = self._bm25.scores(tokenize(query))
        bm_order = [i for i in sorted(range(len(bm)), key=lambda i: bm[i], reverse=True)
                    if i in allowed_set][:20]
        if bm_order:
            ranked_lists.append(("bm25", bm_order))

        # ② 字符二元组余弦
        assert self._cos is not None
        cs = self._cos.scores(query)
        cs_order = [i for i in sorted(range(len(cs)), key=lambda i: cs[i], reverse=True)
                    if i in allowed_set][:20]
        if cs_order:
            ranked_lists.append(("bigram-cosine", cs_order))

        # ③ 向量（可选）
        if self._vector_on and self._embedder is not None:
            qv = self._embedder.encode([query], normalize_embeddings=True)[0]
            sims = [sum(a * b for a, b in zip(qv, dv)) for dv in self._doc_vecs]
            vs_order = [i for i in sorted(range(len(sims)), key=lambda i: sims[i], reverse=True)
                        if i in allowed_set][:20]
            if vs_order:
                ranked_lists.append(("vector", vs_order))

        if not ranked_lists:
            return []

        # ④ RRF 融合
        fused = rrf_fuse(ranked_lists, k=self.rrf_k)

        # 记录每条结果被哪些通道召回（用于解释"为什么它排前面"）
        hit_channels: dict[int, list[str]] = {}
        for name, indices in ranked_lists:
            for idx in indices:
                hit_channels.setdefault(idx, []).append(name)

        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
        candidates = ranked[: max(top_k * 2, top_k)]

        # ⑤ 精排（可选）
        if self._rerank_on and self._reranker is not None and candidates:  # pragma: no cover
            pairs = [[query, self.docs[i].text] for i, _ in candidates]
            scores = self._reranker.predict(pairs)
            order = sorted(range(len(candidates)), key=lambda j: scores[j], reverse=True)
            candidates = [candidates[j] for j in order]

        results: list[Retrieved] = []
        for idx, score in candidates[:top_k]:
            results.append(
                Retrieved(doc=self.docs[idx], score=round(float(score), 6),
                          channels=hit_channels.get(idx, []))
            )
        return results
