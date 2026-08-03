"""BM25 倒排索引：基于关键词的字面匹配检索。

与向量检索（语义匹配）互补，两者融合可提升检索精度。
中文场景使用 jieba 分词，英文场景按空格切分。

设计要点：
- 增量更新：支持单条/批量添加与删除，无需全量重建
- 持久化友好：内部维护 chunk_id → 文本的映射，可序列化存储
- 线程安全：build 与 search 可并发（读多写少场景）
"""

from __future__ import annotations

import re
from typing import Any

from app.core.logging import get_logger
from app.knowledge.models.document import RetrievalResult, TextChunk

logger = get_logger(__name__)

# jieba 延迟导入：首次使用时才加载词典（启动优化）
_jieba_loaded = False


def _ensure_jieba() -> None:
    """延迟加载 jieba 词典。"""
    global _jieba_loaded
    if not _jieba_loaded:
        import jieba

        jieba.setLogLevel("ERROR")  # 抑制 jieba 初始化日志
        _jieba_loaded = True


def tokenize(text: str) -> list[str]:
    """中英文混合分词：jieba 中文分词 + 英文按空格/标点切分。

    过滤空白与单字符停用词，保留有意义的词项。
    """
    _ensure_jieba()
    import jieba

    # 中英文混合分词
    raw_tokens = jieba.cut_for_search(text)
    # 过滤空白、纯标点、单字符（保留数字与英文单词）
    tokens: list[str] = []
    for tok in raw_tokens:
        tok = tok.strip()
        if not tok:
            continue
        # 保留长度>=2的中文词、英文单词、数字
        if len(tok) >= 2 or tok.isalnum():
            # 进一步拆分英文与标点
            sub_tokens = re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]+", tok)
            for sub in sub_tokens:
                if len(sub) >= 2 or sub.isdigit():
                    tokens.append(sub.lower())
    return tokens


class BM25Index:
    """BM25 倒排索引：关键词检索引擎。

    用法：
        index = BM25Index()
        index.add_chunks(chunks)
        results = index.search("查询文本", top_k=5)
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        # chunk_id → 分词列表
        self._documents: dict[str, list[str]] = {}
        # chunk 元信息：chunk_id → (text, metadata, doc_id)
        self._meta: dict[str, tuple[str, dict[str, Any], str]] = {}
        # BM25Okapi 实例（延迟构建）
        self._bm25: Any = None
        self._doc_ids_ordered: list[str] = []  # 保持插入顺序，与 BM25 内部对齐
        self._dirty = True

    @property
    def size(self) -> int:
        """已索引的文档数。"""
        return len(self._documents)

    @property
    def is_ready(self) -> bool:
        """索引是否可检索（至少含 1 条文档）。"""
        return len(self._documents) > 0

    def add_chunk(self, chunk: TextChunk) -> None:
        """添加单条分块到索引。"""
        if chunk.chunk_id in self._documents:
            return  # 幂等：已存在则跳过
        tokens = tokenize(chunk.text)
        self._documents[chunk.chunk_id] = tokens
        self._meta[chunk.chunk_id] = (chunk.text, dict(chunk.metadata), chunk.doc_id)
        self._doc_ids_ordered.append(chunk.chunk_id)
        self._dirty = True

    def add_chunks(self, chunks: list[TextChunk]) -> None:
        """批量添加分块。"""
        for chunk in chunks:
            self.add_chunk(chunk)

    def remove(self, chunk_id: str) -> bool:
        """从索引中移除指定分块。返回是否实际移除。"""
        if chunk_id not in self._documents:
            return False
        del self._documents[chunk_id]
        del self._meta[chunk_id]
        self._doc_ids_ordered.remove(chunk_id)
        self._dirty = True
        return True

    def remove_doc(self, doc_id: str) -> int:
        """移除指定文档的所有分块，返回移除数量。"""
        ids_to_remove = [cid for cid, (_, _, did) in self._meta.items() if did == doc_id]
        for cid in ids_to_remove:
            self.remove(cid)
        return len(ids_to_remove)

    def clear(self) -> None:
        """清空索引。"""
        self._documents.clear()
        self._meta.clear()
        self._doc_ids_ordered.clear()
        self._bm25 = None
        self._dirty = True

    def search(self, query: str, top_k: int = 10) -> list[RetrievalResult]:
        """关键词检索，返回 top_k 条结果。"""
        if not self.is_ready:
            return []
        self._rebuild_if_dirty()

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        # BM25Okapi.get_scores 返回每个文档的得分数组
        scores = self._bm25.get_scores(query_tokens)
        # 按得分降序取 top_k
        scored = list(zip(self._doc_ids_ordered, scores, strict=False))
        scored.sort(key=lambda x: x[1], reverse=True)

        results: list[RetrievalResult] = []
        for chunk_id, score in scored[:top_k]:
            if score <= 0:
                continue  # 过滤零分（无关键词匹配）
            text, metadata, doc_id = self._meta[chunk_id]
            results.append(
                RetrievalResult(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    text=text,
                    score=float(score),
                    metadata=metadata,
                    source="bm25",
                )
            )
        return results

    def _rebuild_if_dirty(self) -> None:
        """若有变更则重建 BM25 内部结构。"""
        if not self._dirty:
            return
        if not self._documents:
            self._bm25 = None
            self._dirty = False
            return

        from rank_bm25 import BM25Okapi

        # BM25Okapi 需要语料为分词列表的列表，顺序与 _doc_ids_ordered 对齐
        corpus = [self._documents[cid] for cid in self._doc_ids_ordered]
        self._bm25 = BM25Okapi(corpus, k1=self._k1, b=self._b)
        self._dirty = False
        logger.info("bm25_index_rebuilt", docs=len(corpus))
