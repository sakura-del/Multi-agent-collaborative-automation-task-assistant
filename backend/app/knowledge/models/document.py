"""知识库层数据模型：文档、分块、检索结果。

定义 RAG 流水线中各阶段传递的数据结构，
使解析→分块→嵌入→检索→重排各环节接口清晰、类型可验。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DocumentType(StrEnum):
    """支持的文档类型。"""

    PDF = "pdf"
    DOCX = "docx"
    MARKDOWN = "markdown"
    TXT = "txt"
    UNKNOWN = "unknown"


class ChunkStrategy(StrEnum):
    """分块策略标识。"""

    RECURSIVE = "recursive"  # 递归字符分块
    SEMANTIC = "semantic"  # 语义分块（按段落边界）
    SLIDING_WINDOW = "sliding_window"  # 滑动窗口分块


@dataclass
class ParsedDocument:
    """解析后的文档：含纯文本与结构化元数据。

    由 DocumentParser 产出，供 Chunker 消费。
    """

    doc_id: str
    source: str  # 文件路径或标识
    doc_type: DocumentType
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    # 结构化段落：保留标题层级与段落边界，供语义分块使用
    sections: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TextChunk:
    """文本分块：嵌入与索引的最小单元。"""

    chunk_id: str
    doc_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    # 分块在原文中的字符偏移（起止），用于结果高亮定位
    start_offset: int = 0
    end_offset: int = 0


@dataclass
class RetrievalResult:
    """单条检索结果。"""

    chunk_id: str
    doc_id: str
    text: str
    score: float  # 相关性得分（越高越相关）
    metadata: dict[str, Any] = field(default_factory=dict)
    # 检索来源：vector / bm25 / hybrid
    source: str = "hybrid"


@dataclass
class IndexStats:
    """索引统计信息。"""

    total_docs: int = 0
    total_chunks: int = 0
    vector_index_ready: bool = False
    bm25_index_ready: bool = False
    last_update: str | None = None
