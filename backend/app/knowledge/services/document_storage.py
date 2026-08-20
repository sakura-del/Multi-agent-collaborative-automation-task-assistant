"""受控文档存储：把客户端上传落到用户专属目录，隔离路径穿越。

安全要点：
- 客户端文件名只取 basename，落盘名替换为 UUID，杜绝 ``../../`` 穿越
- 按 user_id 划分子目录，删除时强制校验父目录归属（防越权删文件）
- 流式写入并限制大小（超过即删临时文件），首 8 字节做文件签名校验
- 扩展名白名单 + 签名校验：PDF 魔数 / DOCX 双 XML / TXT 与 Markdown 可 UTF-8 解码
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
from uuid import UUID, uuid4

import anyio
from fastapi import UploadFile

from app.core.exceptions import NotFoundException, ValidationException

# 单块读取大小：1MB，兼顾内存与循环次数
_READ_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class StoredDocument:
    """落盘后的文档描述。"""

    path: Path
    original_name: str
    extension: str
    size: int


class DocumentStorage:
    """受控文档存储：root/user_id/<uuid><ext> 三层结构。"""

    # 扩展名白名单（小写，含点）
    ALLOWED: ClassVar[frozenset[str]] = frozenset({".pdf", ".docx", ".md", ".markdown", ".txt"})

    def __init__(self, root: str | Path, *, max_file_bytes: int) -> None:
        self.root = Path(root).resolve()
        self.max_file_bytes = max_file_bytes

    async def save(self, user_id: UUID, upload: UploadFile) -> StoredDocument:
        """保存上传文件到用户目录，返回落盘描述。

        校验顺序：扩展名白名单 → 流式写盘（大小限制）→ 文件签名。
        任一校验失败都会删除已写入的临时文件。
        """
        original = Path(upload.filename or "").name
        extension = Path(original).suffix.lower()
        if extension not in self.ALLOWED:
            raise ValidationException("不支持的文件类型")
        if not original:
            raise ValidationException("文件名不能为空")

        target_dir = self.root / str(user_id)
        # 本地目录创建是快速系统调用，直接同步执行即可
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{uuid4().hex}{extension}"
        size = 0
        head = b""
        try:
            async with await anyio.open_file(target, "wb") as output:
                while chunk := await upload.read(_READ_CHUNK_SIZE):
                    size += len(chunk)
                    if size > self.max_file_bytes:
                        # 超限：关闭输出、删临时文件，再抛业务异常
                        await output.aclose()
                        target.unlink(missing_ok=True)
                        raise ValidationException("文件超过 30 MB 限制")
                    if not head:
                        head = chunk[:8]
                    await output.write(chunk)
            self._validate_signature(extension, target)
        except (ValidationException, OSError):
            target.unlink(missing_ok=True)
            raise
        return StoredDocument(target, original, extension, size)

    def delete(self, user_id: UUID, path: str) -> None:
        """删除用户目录内的文件；父目录不属于该用户时按"不存在"处理（防越权）。"""
        candidate = Path(path).resolve()
        allowed_root = (self.root / str(user_id)).resolve()
        if candidate.parent != allowed_root:
            raise NotFoundException("文档不存在")
        candidate.unlink(missing_ok=True)

    def _validate_signature(self, extension: str, target: Path) -> None:
        """按扩展名校验文件签名，不一致时删文件并抛异常。"""
        valid = True
        if extension == ".pdf":
            with target.open("rb") as f:
                valid = f.read(5).startswith(b"%PDF-")
        elif extension == ".docx":
            valid = self._is_valid_docx(target)
        elif extension in (".md", ".markdown", ".txt"):
            try:
                target.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                valid = False
        if not valid:
            raise ValidationException("文件签名与扩展名不一致")

    @staticmethod
    def _is_valid_docx(target: Path) -> bool:
        """DOCX 校验：ZIP 容器且同时含 [Content_Types].xml 与 word/document.xml。"""
        try:
            with zipfile.ZipFile(target) as zf:
                names = set(zf.namelist())
        except (zipfile.BadZipFile, OSError):
            return False
        return "[Content_Types].xml" in names and "word/document.xml" in names
