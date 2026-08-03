"""BM25Index 测试：中文分词、关键词检索、增量更新与去重。

覆盖 tokenize 分词器、索引构建、search 召回、remove_doc 删除等核心行为。
"""

from __future__ import annotations

from app.knowledge.models.document import TextChunk
from app.knowledge.services.bm25_index import BM25Index, tokenize


class TestTokenize:
    """tokenize()：中英文混合分词。"""

    def test_chinese_segmentation(self) -> None:
        """中文应被切分为有意义的词项。"""
        tokens = tokenize("多智能体协作系统")
        assert isinstance(tokens, list)
        # 至少切出部分词项
        assert len(tokens) >= 1
        # 全部小写
        for tok in tokens:
            assert tok == tok.lower()

    def test_english_tokenization(self) -> None:
        """英文应按空格切分并小写化。"""
        tokens = tokenize("RAG knowledge base retrieval")
        assert "rag" in tokens
        assert "knowledge" in tokens
        assert "retrieval" in tokens

    def test_mixed_content(self) -> None:
        """中英文混合内容应同时保留两类词项。"""
        tokens = tokenize("使用 RAG 检索知识库")
        assert "rag" in tokens
        # 中文部分至少切出一个词
        assert any(len(t) >= 2 for t in tokens if not t.isascii())

    def test_filters_punctuation(self) -> None:
        """纯标点与空白应被过滤。"""
        tokens = tokenize("，。 ！ ？ \n\t")
        assert tokens == []

    def test_filters_single_chars(self) -> None:
        """单字符（非数字）应被过滤。"""
        tokens = tokenize("a b c 12 34")
        # 数字保留（长度>=2 或 isdigit）
        assert "12" in tokens
        assert "34" in tokens
        # 单字符英文被过滤
        assert "a" not in tokens

    def test_empty_string(self) -> None:
        """空字符串返回空列表。"""
        assert tokenize("") == []

    def test_preserves_digits(self) -> None:
        """数字应被保留。"""
        tokens = tokenize("版本 2024 发布")
        assert "2024" in tokens


class TestBM25IndexBasic:
    """BM25Index 基础属性与构建。"""

    def test_empty_index_not_ready(self) -> None:
        """空索引 is_ready 为 False。"""
        index = BM25Index()
        assert not index.is_ready
        assert index.size == 0

    def test_add_chunk_increases_size(self) -> None:
        """添加分块后 size 应递增。"""
        index = BM25Index()
        chunk = TextChunk(chunk_id="c1", doc_id="d1", text="多智能体协作系统")
        index.add_chunk(chunk)
        assert index.size == 1
        assert index.is_ready

    def test_add_chunks_batch(self) -> None:
        """批量添加多个分块。"""
        index = BM25Index()
        chunks = [TextChunk(chunk_id=f"c{i}", doc_id="d1", text=f"内容片段 {i}") for i in range(3)]
        index.add_chunks(chunks)
        assert index.size == 3

    def test_add_duplicate_chunk_idempotent(self) -> None:
        """相同 chunk_id 重复添加应幂等（不重复计数）。"""
        index = BM25Index()
        chunk = TextChunk(chunk_id="dup", doc_id="d1", text="重复内容")
        index.add_chunk(chunk)
        index.add_chunk(chunk)
        assert index.size == 1


class TestBM25Search:
    """BM25 关键词检索。"""

    def test_search_returns_relevant_results(self) -> None:
        """关键词匹配的文档应排在前面。"""
        index = BM25Index()
        index.add_chunks(
            [
                TextChunk(chunk_id="c1", doc_id="d1", text="多智能体协作自动化系统"),
                TextChunk(chunk_id="c2", doc_id="d2", text="今日天气预报晴朗"),
                TextChunk(chunk_id="c3", doc_id="d3", text="智能体框架设计要点"),
            ]
        )
        results = index.search("智能体", top_k=2)
        assert len(results) >= 1
        # 关键词命中的两个 chunk 应在结果中
        hit_ids = {r.chunk_id for r in results}
        assert "c1" in hit_ids or "c3" in hit_ids

    def test_search_no_match_returns_empty(self) -> None:
        """无关键词匹配应返回空列表。"""
        index = BM25Index()
        index.add_chunk(TextChunk(chunk_id="c1", doc_id="d1", text="多智能体协作"))
        results = index.search("xyz", top_k=5)
        assert results == []

    def test_search_empty_index_returns_empty(self) -> None:
        """空索引检索返回空列表。"""
        index = BM25Index()
        assert index.search("任意查询", top_k=5) == []

    def test_search_top_k_limit(self) -> None:
        """top_k 应限制返回数量。"""
        index = BM25Index()
        index.add_chunks(
            [TextChunk(chunk_id=f"c{i}", doc_id="d1", text=f"智能体 编号 {i}") for i in range(5)]
        )
        results = index.search("智能体", top_k=2)
        assert len(results) <= 2

    def test_search_result_fields(self) -> None:
        """检索结果应包含完整字段。"""
        index = BM25Index()
        # 使用 3+ 文档避免 BM25 IDF 为 0（N=2,n=1 时 IDF=log(1)=0）
        index.add_chunks(
            [
                TextChunk(
                    chunk_id="c1",
                    doc_id="d1",
                    text="多智能体协作系统",
                    metadata={"doc_source": "test.txt"},
                ),
                TextChunk(chunk_id="c2", doc_id="d2", text="其他无关内容一"),
                TextChunk(chunk_id="c3", doc_id="d3", text="其他无关内容二"),
            ]
        )
        results = index.search("智能体", top_k=3)
        assert len(results) >= 1
        r = results[0]
        assert r.chunk_id == "c1"
        assert r.doc_id == "d1"
        assert r.text == "多智能体协作系统"
        assert r.source == "bm25"
        assert r.score > 0

    def test_search_empty_query_returns_empty(self) -> None:
        """空查询（无有效词项）应返回空列表。"""
        index = BM25Index()
        index.add_chunk(TextChunk(chunk_id="c1", doc_id="d1", text="内容"))
        assert index.search("", top_k=5) == []
        assert index.search(" ， 。 ", top_k=5) == []

    def test_search_filters_zero_score(self) -> None:
        """零分结果（无关键词命中）应被过滤。"""
        index = BM25Index()
        index.add_chunks(
            [
                TextChunk(chunk_id="c1", doc_id="d1", text="智能体 检索"),
                TextChunk(chunk_id="c2", doc_id="d2", text="无关 内容 xyz"),
            ]
        )
        results = index.search("智能体", top_k=10)
        # 仅 c1 命中，c2 应被过滤
        assert all(r.chunk_id == "c1" for r in results)


class TestBM25Remove:
    """BM25 索引删除操作。"""

    def test_remove_single_chunk(self) -> None:
        """remove 应移除指定分块。"""
        index = BM25Index()
        index.add_chunk(TextChunk(chunk_id="c1", doc_id="d1", text="内容一"))
        assert index.remove("c1") is True
        assert index.size == 0
        assert not index.is_ready

    def test_remove_nonexistent_returns_false(self) -> None:
        """移除不存在的 chunk_id 应返回 False。"""
        index = BM25Index()
        assert index.remove("nonexistent") is False

    def test_remove_doc_removes_all_chunks(self) -> None:
        """remove_doc 应删除该文档的所有分块。"""
        index = BM25Index()
        index.add_chunks(
            [TextChunk(chunk_id=f"c{i}", doc_id="d1", text=f"内容 {i}") for i in range(3)]
        )
        index.add_chunk(TextChunk(chunk_id="c9", doc_id="d2", text="其他文档"))
        removed = index.remove_doc("d1")
        assert removed == 3
        assert index.size == 1

    def test_remove_doc_nonexistent_returns_zero(self) -> None:
        """删除不存在的文档应返回 0。"""
        index = BM25Index()
        index.add_chunk(TextChunk(chunk_id="c1", doc_id="d1", text="内容"))
        assert index.remove_doc("nonexistent") == 0


class TestBM25Clear:
    """BM25 索引清空。"""

    def test_clear_empties_index(self) -> None:
        """clear 后索引应为空。"""
        index = BM25Index()
        index.add_chunks(
            [
                TextChunk(chunk_id="c1", doc_id="d1", text="内容一"),
                TextChunk(chunk_id="c2", doc_id="d2", text="内容二"),
            ]
        )
        index.clear()
        assert index.size == 0
        assert not index.is_ready

    def test_clear_idempotent(self) -> None:
        """对空索引 clear 也应正常工作。"""
        index = BM25Index()
        index.clear()
        assert index.size == 0
