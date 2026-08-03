"""DocumentChunker 测试：递归/滑动窗口/语义三种分块策略。

覆盖分块数量、块长度约束、偏移量、重叠、chunk_id 确定性及元数据。
"""

from __future__ import annotations

import hashlib

from app.knowledge.models.document import ChunkStrategy, DocumentType, ParsedDocument
from app.knowledge.services.chunker import DocumentChunker, _make_chunk_id
from app.knowledge.services.document_parser import DocumentParser

# 测试用分块参数：chunk_size=100，overlap=20，便于精确断言
CHUNK_SIZE = 100
CHUNK_OVERLAP = 20


def _make_chunker() -> DocumentChunker:
    """构造固定参数的分块器，避免依赖全局 RAG 配置默认值。"""
    return DocumentChunker(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)


def _make_doc(text: str, doc_id: str = "test-doc") -> ParsedDocument:
    """通过 parse_text 构造带 sections 的 ParsedDocument，供分块消费。"""
    return DocumentParser().parse_text(text, doc_id=doc_id)


class TestRecursiveChunk:
    """递归字符分块策略。"""

    def test_short_text_returns_single_chunk(self) -> None:
        """短文本（<=chunk_size）不应被切分，返回 1 块。"""
        chunker = _make_chunker()
        text = "a" * CHUNK_SIZE  # 恰好等于 chunk_size
        doc = _make_doc(text)

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.RECURSIVE)

        assert len(chunks) == 1
        assert chunks[0].text == text

    def test_short_text_offset_covers_full_text(self) -> None:
        """单块分块的偏移量应覆盖整段文本 [0, len(text)]。"""
        chunker = _make_chunker()
        text = "短文本不分块"
        doc = _make_doc(text)

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.RECURSIVE)

        assert chunks[0].start_offset == 0
        assert chunks[0].end_offset == len(text)

    def test_long_text_split_into_multiple_chunks(self) -> None:
        """长文本应按分隔符切分为多块，每块长度 <= chunk_size。"""
        chunker = _make_chunker()
        # 5 段每段 30 字符，总长 > chunk_size，触发切分
        paragraphs = ["段落" + "x" * 28] * 5
        text = "\n\n".join(paragraphs)
        doc = _make_doc(text)

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.RECURSIVE)

        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk.text) <= CHUNK_SIZE

    def test_chunks_preserve_original_text(self) -> None:
        """每个分块文本应能在原文中找到，且偏移量定位正确。"""
        chunker = _make_chunker()
        text = "句子内容。" * 30  # 约 150 字符，触发切分
        doc = _make_doc(text)

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.RECURSIVE)

        assert len(chunks) >= 2
        for chunk in chunks:
            # 块文本是原文某子串（递归切分按分隔符保留边界）
            assert chunk.text in text
            assert 0 <= chunk.start_offset < len(text)
            assert chunk.end_offset <= len(text)


class TestSlidingWindowChunk:
    """滑动窗口分块策略。"""

    def test_step_equals_size_minus_overlap(self) -> None:
        """相邻块起始偏移之差应等于 chunk_size - chunk_overlap。"""
        chunker = _make_chunker()
        text = "abcdefghij" * 25  # 250 字符，无空白避免 strip 干扰
        doc = _make_doc(text)

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.SLIDING_WINDOW)

        assert len(chunks) >= 2
        expected_step = CHUNK_SIZE - CHUNK_OVERLAP
        for i in range(1, len(chunks)):
            assert chunks[i].start_offset - chunks[i - 1].start_offset == expected_step

    def test_adjacent_chunks_overlap(self) -> None:
        """相邻块应有 chunk_overlap 个字符的重叠。"""
        chunker = _make_chunker()
        text = "abcdefghij" * 25
        doc = _make_doc(text)

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.SLIDING_WINDOW)

        for i in range(1, len(chunks)):
            overlap = chunks[i - 1].end_offset - chunks[i].start_offset
            assert overlap == CHUNK_OVERLAP

    def test_each_chunk_size_within_limit(self) -> None:
        """每块长度不应超过 chunk_size。"""
        chunker = _make_chunker()
        text = "abcdefghij" * 25
        doc = _make_doc(text)

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.SLIDING_WINDOW)

        for chunk in chunks:
            assert len(chunk.text) <= CHUNK_SIZE

    def test_offsets_point_to_correct_slice(self) -> None:
        """块文本应与原文对应偏移处的切片完全一致。"""
        chunker = _make_chunker()
        text = "abcdefghij" * 25
        doc = _make_doc(text)

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.SLIDING_WINDOW)

        for chunk in chunks:
            assert text[chunk.start_offset : chunk.end_offset] == chunk.text

    def test_short_text_single_chunk(self) -> None:
        """短文本（<chunk_size）滑动窗口只产生 1 块。"""
        chunker = _make_chunker()
        text = "abcdefghij" * 5  # 50 字符
        doc = _make_doc(text)

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.SLIDING_WINDOW)

        assert len(chunks) == 1
        assert chunks[0].text == text


class TestSemanticChunk:
    """语义分块策略：按文档章节切分。"""

    def test_split_by_sections(self) -> None:
        """每个 section（<=chunk_size）应独立成块。"""
        chunker = _make_chunker()
        text = "第一段落内容\n\n第二段落内容\n\n第三段落内容"
        doc = _make_doc(text)

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.SEMANTIC)

        assert len(chunks) == 3
        assert chunks[0].text == "第一段落内容"
        assert chunks[1].text == "第二段落内容"
        assert chunks[2].text == "第三段落内容"

    def test_long_section_recursively_split(self) -> None:
        """超长段落（>chunk_size）应被递归切分，短段落保持完整。"""
        chunker = _make_chunker()
        long_para = "长段落" + "x" * 100  # 103 字符 > chunk_size
        text = long_para + "\n\n短段落"
        doc = _make_doc(text)

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.SEMANTIC)

        # 长段落切分为多块 + 短段落 1 块
        assert len(chunks) >= 3
        assert chunks[-1].text == "短段落"
        for chunk in chunks[:-1]:
            assert len(chunk.text) <= CHUNK_SIZE

    def test_no_sections_falls_back_to_recursive(self) -> None:
        """无结构化段落时应回退为递归分块。"""
        chunker = _make_chunker()
        doc = ParsedDocument(
            doc_id="no-sections",
            source="<raw_text>",
            doc_type=DocumentType.TXT,
            text="a" * 250,
            metadata={},
            sections=[],
        )

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.SEMANTIC)

        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk.text) <= CHUNK_SIZE

    def test_section_offsets_in_original_text(self) -> None:
        """语义分块的偏移量应定位到原文对应位置。"""
        chunker = _make_chunker()
        text = "第一段\n\n第二段"
        doc = _make_doc(text)

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.SEMANTIC)

        # 第一段在原文起始位置 0
        assert chunks[0].start_offset == 0
        assert text[chunks[0].start_offset : chunks[0].end_offset].strip() == "第一段"
        # 第二段应在第一段之后
        assert chunks[1].start_offset > chunks[0].start_offset


class TestChunkIdDeterministic:
    """chunk_id 确定性：相同 doc_id+index 产生相同 ID。"""

    def test_same_doc_and_index_same_id(self) -> None:
        """同一文档多次分块，对应序号的 chunk_id 应一致。"""
        chunker = _make_chunker()
        doc = _make_doc("测试文本内容用于分块" * 10, doc_id="stable-doc")

        chunks1 = chunker.chunk(doc, strategy=ChunkStrategy.RECURSIVE)
        chunks2 = chunker.chunk(doc, strategy=ChunkStrategy.RECURSIVE)

        assert len(chunks1) == len(chunks2)
        for c1, c2 in zip(chunks1, chunks2):  # noqa: B905
            assert c1.chunk_id == c2.chunk_id

    def test_chunk_id_matches_formula(self) -> None:
        """chunk_id 应等于 md5(doc_id:index)[:16]。"""
        chunker = _make_chunker()
        doc = _make_doc("内容", doc_id="abc123")

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.RECURSIVE)

        for idx, chunk in enumerate(chunks):
            expected = hashlib.md5(f"abc123:{idx}".encode()).hexdigest()[:16]
            assert chunk.chunk_id == expected
            assert chunk.chunk_id == _make_chunk_id("abc123", idx)

    def test_different_doc_id_different_chunk_id(self) -> None:
        """不同 doc_id 的同序号块应有不同 chunk_id。"""
        chunker = _make_chunker()
        doc1 = _make_doc("相同内容", doc_id="doc-one")
        doc2 = _make_doc("相同内容", doc_id="doc-two")

        chunks1 = chunker.chunk(doc1, strategy=ChunkStrategy.RECURSIVE)
        chunks2 = chunker.chunk(doc2, strategy=ChunkStrategy.RECURSIVE)

        assert chunks1[0].chunk_id != chunks2[0].chunk_id


class TestEmptyText:
    """空文本分块：返回空列表。"""

    def test_empty_string_returns_empty(self) -> None:
        """空字符串应返回空分块列表。"""
        chunker = _make_chunker()
        doc = _make_doc("")

        assert chunker.chunk(doc, strategy=ChunkStrategy.RECURSIVE) == []

    def test_whitespace_only_returns_empty(self) -> None:
        """仅空白字符的文本应返回空分块列表。"""
        chunker = _make_chunker()
        doc = _make_doc("   \n  \t  ")

        assert chunker.chunk(doc, strategy=ChunkStrategy.RECURSIVE) == []

    def test_empty_text_for_all_strategies(self) -> None:
        """三种策略对空文本都应返回空列表。"""
        chunker = _make_chunker()
        doc = _make_doc("")

        for strategy in ChunkStrategy:
            assert chunker.chunk(doc, strategy=strategy) == []


class TestChunkMetadata:
    """分块元数据：doc_source / doc_type / chunk_index。"""

    def test_metadata_contains_required_fields(self) -> None:
        """每个 chunk 的 metadata 应含 doc_source、doc_type、chunk_index。"""
        chunker = _make_chunker()
        doc = _make_doc("用于测试元数据的文本内容" * 5, doc_id="meta-doc")

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.RECURSIVE)

        assert len(chunks) >= 1
        for idx, chunk in enumerate(chunks):
            assert chunk.metadata["doc_source"] == doc.source
            assert chunk.metadata["doc_type"] == doc.doc_type.value
            assert chunk.metadata["chunk_index"] == idx

    def test_metadata_includes_doc_metadata(self) -> None:
        """chunk metadata 应合并 document 自身的 metadata。"""
        chunker = _make_chunker()
        doc = ParsedDocument(
            doc_id="m-doc",
            source="<raw_text>",
            doc_type=DocumentType.TXT,
            text="一段文本内容" * 20,
            metadata={"source_type": "raw_text", "custom": "值"},
            sections=[],
        )

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.RECURSIVE)

        for chunk in chunks:
            assert chunk.metadata["source_type"] == "raw_text"
            assert chunk.metadata["custom"] == "值"

    def test_chunk_doc_id_matches_document(self) -> None:
        """分块的 doc_id 应等于文档 doc_id。"""
        chunker = _make_chunker()
        doc = _make_doc("文本", doc_id="parent-doc-id")

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.RECURSIVE)

        for chunk in chunks:
            assert chunk.doc_id == "parent-doc-id"

    def test_chunk_index_sequential(self) -> None:
        """chunk_index 应从 0 开始连续递增。"""
        chunker = _make_chunker()
        doc = _make_doc("分块索引测试文本" * 15, doc_id="idx-doc")

        chunks = chunker.chunk(doc, strategy=ChunkStrategy.RECURSIVE)

        assert [c.metadata["chunk_index"] for c in chunks] == list(range(len(chunks)))
