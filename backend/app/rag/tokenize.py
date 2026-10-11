"""中文分词。

【为什么单独抽一层】
    检索质量一半靠分词。中文没有空格，`jieba` 能把「兵马俑」切成一个词，
    切错就会导致召回失败。把它单独放一层，将来换分词器只改这里。

【降级策略】
    没装 jieba 时退到"字符二元组"（bigram）：
    「兵马俑」→ 兵马 / 马俑。
    精度略低，但保证服务在任何环境都能起来 —— 不因为一个依赖缺失就全线崩溃。
"""

from __future__ import annotations

try:
    import jieba

    jieba.setLogLevel(20)  # 关掉 jieba 的初始化日志，避免污染服务输出

    TOKENIZER_NAME = "jieba"

    def tokenize(text: str) -> list[str]:
        """按词切分，丢掉空白与标点。"""
        return [w for w in jieba.lcut(text) if w.strip()]

except ImportError:  # pragma: no cover - 取决于环境
    TOKENIZER_NAME = "char-bigram"

    def tokenize(text: str) -> list[str]:
        """降级分词：中文按字符二元组，英文按空格切。"""
        import re

        tokens: list[str] = []
        for seg in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", text):
            if seg[0].isascii():
                tokens.append(seg.lower())
            elif len(seg) == 1:
                tokens.append(seg)
            else:
                tokens.extend(seg[i : i + 2] for i in range(len(seg) - 1))
        return tokens


def char_bigrams(text: str) -> list[str]:
    """字符二元组，用于"轻量稠密"那一路召回。

    它不依赖分词：对「三里屯」这种新词、错别字、口语化查询更稳，
    恰好补上 BM25（依赖分词）的短板 —— 这正是把它放在第二路的原因。
    """
    import re

    grams: list[str] = []
    for seg in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", text):
        if len(seg) < 2:
            grams.append(seg)
        else:
            grams.extend(seg[i : i + 2] for i in range(len(seg) - 1))
    return grams
