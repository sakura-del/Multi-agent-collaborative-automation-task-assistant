"""智能文档分块：将长文档切分为适合嵌入的短文本块。

三种分块策略：
- 递归字符分块（RecursiveCharacterSplitter）：按分隔符层级递归切分，
  保证语义连贯性，是默认策略
- 语义分块（SemanticChunker）：按文档结构（标题/段落）切分，
  保留章节完整性
- 滑动窗口分块（SlidingWindowSplitter）：固定窗口 + 重叠，
  适合需要上下文连续性的场景

设计要点：
- 分块 ID 确定性：基于 doc_id + 序号，保证重复分块结果一致（幂等）
- 偏移量追踪：记录每块在原文中的字符偏移，支持检索结果高亮定位
- 配置驱动：chunk_size / chunk_overlap 从 RAGConfig 读取，可运行时覆盖
"""

from __future__ import annotations

import hashlib

from app.core.config import get_settings
from app.core.logging import get_logger
from app.knowledge.models.document import ChunkStrategy, ParsedDocument, TextChunk

logger = get_logger(__name__)

# 递归分块的分隔符层级：从语义强到弱
# 段落分隔 > 换行 > 句号 > 空格
_RECURSIVE_SEPARATORS: list[str] = ["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""]


class DocumentChunker:
    """文档分块器：按指定策略将文档切分为 TextChunk 列表。

    用法：
        chunker = DocumentChunker()
        chunks = chunker.chunk(parsed_doc, strategy=ChunkStrategy.RECURSIVE)
    """

    def __init__(
        self,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> None:
        rag = get_settings().rag
        self.chunk_size = chunk_size or rag.chunk_size
        self.chunk_overlap = chunk_overlap or rag.chunk_overlap

    def chunk(
        self,
        document: ParsedDocument,
        strategy: ChunkStrategy = ChunkStrategy.RECURSIVE,
    ) -> list[TextChunk]:
        """按策略分块，返回 TextChunk 列表。"""
        if not document.text.strip():
            return []

        if strategy is ChunkStrategy.SEMANTIC:
            pieces = self._semantic_chunk(document)
        elif strategy is ChunkStrategy.SLIDING_WINDOW:
            pieces = self._sliding_window_chunk(document.text)
        else:
            pieces = self._recursive_chunk(document.text)

        chunks = self._build_chunks(document, pieces)
        logger.info(
            "document_chunked",
            doc_id=document.doc_id,
            strategy=strategy.value,
            chunks=len(chunks),
        )
        return chunks

    def _recursive_chunk(self, text: str) -> list[tuple[str, int, int]]:
        """递归字符分块：按分隔符层级切分至目标长度。

        返回 [(块文本, 起始偏移, 结束偏移), ...]
        """
        return _split_recursively(
            text,
            separators=_RECURSIVE_SEPARATORS,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )

    def _sliding_window_chunk(self, text: str) -> list[tuple[str, int, int]]:
        """滑动窗口分块：固定窗口大小，相邻块有 overlap 重叠。"""
        size = self.chunk_size
        overlap = self.chunk_overlap
        if size <= 0:
            return []
        step = max(1, size - overlap)
        pieces: list[tuple[str, int, int]] = []
        start = 0
        text_len = len(text)
        while start < text_len:
            end = min(start + size, text_len)
            piece = text[start:end].strip()
            if piece:
                pieces.append((piece, start, end))
            if end >= text_len:
                break
            start += step
        return pieces

    def _semantic_chunk(self, document: ParsedDocument) -> list[tuple[str, int, int]]:
        """语义分块：按文档结构（章节/段落）切分，超长段落再递归切分。"""
        pieces: list[tuple[str, int, int]] = []
        search_from = 0

        for section in document.sections:
            content = section.get("content", "").strip()
            if not content:
                continue
            # 在原文中定位该段落偏移
            offset = document.text.find(content, search_from)
            if offset < 0:
                offset = search_from
            search_from = offset + len(content)

            # 超长段落：递归切分保留边界信息
            if len(content) > self.chunk_size:
                sub_pieces = _split_recursively(
                    content,
                    separators=_RECURSIVE_SEPARATORS,
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                )
                for sub_text, sub_start, sub_end in sub_pieces:
                    pieces.append((sub_text, offset + sub_start, offset + sub_end))
            else:
                pieces.append((content, offset, offset + len(content)))

        # 无结构化段落时回退为递归分块
        if not pieces:
            return self._recursive_chunk(document.text)
        return pieces

    def _build_chunks(
        self,
        document: ParsedDocument,
        pieces: list[tuple[str, int, int]],
    ) -> list[TextChunk]:
        """将切分片段组装为 TextChunk，附确定性 ID。"""
        chunks: list[TextChunk] = []
        for idx, (text, start, end) in enumerate(pieces):
            chunk_id = _make_chunk_id(document.doc_id, idx)
            chunks.append(
                TextChunk(
                    chunk_id=chunk_id,
                    doc_id=document.doc_id,
                    text=text,
                    metadata={
                        "doc_source": document.source,
                        "doc_type": document.doc_type.value,
                        "chunk_index": idx,
                        **document.metadata,
                    },
                    start_offset=start,
                    end_offset=end,
                )
            )
        return chunks


def _split_recursively(
    text: str,
    separators: list[str],
    chunk_size: int,
    chunk_overlap: int,
) -> list[tuple[str, int, int]]:
    """递归字符切分：按分隔符层级分割至每块 <= chunk_size。

    使用首个有效分隔符切分，若块仍超长则用下一级分隔符继续切分。
    相邻块之间保留 chunk_overlap 个字符的重叠，保持上下文连贯。
    """
    if len(text) <= chunk_size:
        return [(text.strip(), 0, len(text))] if text.strip() else []

    # 找到能切分当前文本的分隔符
    sep = ""
    for s in separators:
        if s and s in text:
            sep = s
            break

    if not sep:
        # 无分隔符可用：按 chunk_size 硬切分
        return _hard_split(text, chunk_size, chunk_overlap)

    # 按分隔符切分，保留分隔符位置信息用于偏移计算
    pieces: list[tuple[str, int, int]] = []
    current_parts: list[tuple[str, int]] = []  # (文本片段, 起始偏移)
    current_len = 0

    for part in _split_with_positions(text, sep):
        part_text, part_start = part
        part_len = len(part_text)

        if current_len + part_len > chunk_size and current_parts:
            # 当前累积已超限：输出一块
            merged_text, start_off = _merge_parts(current_parts, sep)
            if merged_text.strip():
                pieces.append((merged_text.strip(), start_off, start_off + len(merged_text)))
            # 保留 overlap：从尾部取若干 part 形成下一块开头
            overlap_parts = _take_overlap(current_parts, chunk_overlap, sep)
            current_parts = overlap_parts
            current_len = sum(len(p[0]) for p in current_parts)

        current_parts.append((part_text, part_start))
        current_len += part_len

    # 收尾
    if current_parts:
        merged_text, start_off = _merge_parts(current_parts, sep)
        if merged_text.strip():
            pieces.append((merged_text.strip(), start_off, start_off + len(merged_text)))

    # 对仍然超长的块二次递归（用更细的分隔符）
    next_seps = separators[separators.index(sep) + 1 :] if sep in separators else []
    final: list[tuple[str, int, int]] = []
    for text_piece, s_off, e_off in pieces:
        if len(text_piece) > chunk_size and next_seps:
            sub = _split_recursively(text_piece, next_seps, chunk_size, chunk_overlap)
            for sub_text, sub_s, sub_e in sub:
                final.append((sub_text, s_off + sub_s, s_off + sub_e))
        else:
            final.append((text_piece, s_off, e_off))
    return final


def _split_with_positions(text: str, sep: str) -> list[tuple[str, int]]:
    """按分隔符切分文本，返回 (片段, 起始偏移) 列表，保留分隔符在前段尾部。"""
    parts: list[tuple[str, int]] = []
    start = 0
    while True:
        idx = text.find(sep, start)
        if idx < 0:
            remainder = text[start:]
            if remainder:
                parts.append((remainder, start))
            break
        # 分隔符归入前一段尾部
        segment = text[start : idx + len(sep)]
        parts.append((segment, start))
        start = idx + len(sep)
    return parts


def _merge_parts(parts: list[tuple[str, int]], sep: str) -> tuple[str, int]:
    """合并多个片段为一段，返回 (合并文本, 起始偏移)。"""
    if not parts:
        return "", 0
    return "".join(p[0] for p in parts), parts[0][1]


def _take_overlap(
    parts: list[tuple[str, int]],
    chunk_overlap: int,
    sep: str,
) -> list[tuple[str, int]]:
    """从尾部逆序取片段，累计长度达到 overlap 阈值。"""
    if chunk_overlap <= 0 or not parts:
        return []
    overlap: list[tuple[str, int]] = []
    total = 0
    for part_text, part_start in reversed(parts):
        overlap.insert(0, (part_text, part_start))
        total += len(part_text)
        if total >= chunk_overlap:
            break
    return overlap


def _hard_split(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
) -> list[tuple[str, int, int]]:
    """无分隔符时的硬切分：按固定步长切分。"""
    step = max(1, chunk_size - chunk_overlap)
    pieces: list[tuple[str, int, int]] = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        piece = text[start:end]
        if piece.strip():
            pieces.append((piece, start, end))
        if end >= text_len:
            break
        start += step
    return pieces


def _make_chunk_id(doc_id: str, index: int) -> str:
    """生成确定性分块 ID。"""
    raw = f"{doc_id}:{index}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]
