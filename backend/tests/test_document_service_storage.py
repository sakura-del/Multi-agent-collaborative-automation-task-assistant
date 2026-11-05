"""上传解析必须在 `localize` 提供的本地路径上工作，且用完清理临时文件。

为什么重要：`document_parser` 用 PyMuPDF / python-docx 读**本地文件**，
S3 后端如果直接把对象 key 传进去会 `FileNotFoundError`；
而临时文件如果不在 finally 清理，每次上传都会在磁盘上漏一个最多 30MB 的副本。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import UploadFile

from app.knowledge.models.document import ChunkStrategy
from app.knowledge.services.document_service import KnowledgeDocumentService


class _FakeStorage:
    """模拟 S3 后端：save 返回对象键，localize 提供临时文件。"""

    def __init__(self, tmp_file: Path) -> None:
        self._tmp_file = tmp_file
        self.localized: list[str] = []
        self.temp_existed_during_parse = False

    async def save(self, user_id: Any, upload: UploadFile) -> Any:
        return SimpleNamespace(
            path=Path("uploads/abc/xyz.pdf"),
            doc_uuid=uuid4(),
            original_name="paper.pdf",
            extension=".pdf",
            size=12,
        )

    @asynccontextmanager
    async def localize(self, path: str) -> Any:
        self.localized.append(path)
        self.temp_existed_during_parse = self._tmp_file.exists()
        yield self._tmp_file


class _FakeManager:
    def __init__(self) -> None:
        self.received_path: str | None = None
        self.collection_name = "knowledge_base"
        self.embedding_dimension = 8

    def prepare_document(self, path: str, **kwargs: Any) -> tuple[dict[str, Any], list[Any]]:
        self.received_path = path
        chunk = SimpleNamespace(
            chunk_id="c1",
            text="正文",
            metadata={"chunk_index": 0},
        )
        return {}, [chunk]

    def index_chunks(self, chunks: list[Any]) -> None:
        return None


class _FakeDb:
    def add(self, obj: Any) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _FakeKb:
    def invalidate_cache(self, key: str) -> None:
        return None


@pytest.mark.asyncio
async def test_parse_uses_localized_path(tmp_path: Path) -> None:
    tmp_file = tmp_path / "staged.pdf"
    tmp_file.write_bytes(b"%PDF-1.4 body")
    storage = _FakeStorage(tmp_file)
    manager = _FakeManager()
    service = KnowledgeDocumentService(
        _FakeDb(),  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        index_manager=manager,  # type: ignore[arg-type]
        kb=_FakeKb(),  # type: ignore[arg-type]
    )

    await service.ingest(
        uuid4(),
        UploadFile(filename="paper.pdf", file=__import__("io").BytesIO(b"%PDF-1.4 body")),
        title="论文",
        strategy=ChunkStrategy.RECURSIVE,
    )

    assert storage.localized == ["uploads/abc/xyz.pdf"]
    # 解析器拿到的是本地临时路径，而不是对象 key
    assert manager.received_path == str(tmp_file)
    assert storage.temp_existed_during_parse is True
