"""DocumentStorage 受控存储测试：路径穿越、文件签名、大小限制。"""

from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import UploadFile

from app.core.exceptions import ValidationException
from app.knowledge.services.document_storage import DocumentStorage


@pytest.mark.asyncio
async def test_save_ignores_client_path_and_scopes_user(tmp_path) -> None:
    user_id = uuid4()
    storage = DocumentStorage(tmp_path, max_file_bytes=1024)
    upload = UploadFile(filename="../../paper.pdf", file=BytesIO(b"%PDF-1.4\nbody"))

    stored = await storage.save(user_id, upload)

    assert stored.path.parent == tmp_path / str(user_id)
    assert stored.path.name != "paper.pdf"
    assert stored.original_name == "paper.pdf"


@pytest.mark.asyncio
async def test_save_rejects_bad_pdf_signature(tmp_path) -> None:
    storage = DocumentStorage(tmp_path, max_file_bytes=1024)
    upload = UploadFile(filename="paper.pdf", file=BytesIO(b"not-pdf"))
    with pytest.raises(ValidationException, match="文件签名"):
        await storage.save(uuid4(), upload)


@pytest.mark.asyncio
async def test_save_rejects_oversized_file(tmp_path) -> None:
    storage = DocumentStorage(tmp_path, max_file_bytes=4)
    upload = UploadFile(filename="paper.txt", file=BytesIO(b"12345"))
    with pytest.raises(ValidationException, match="30 MB"):
        await storage.save(uuid4(), upload)
