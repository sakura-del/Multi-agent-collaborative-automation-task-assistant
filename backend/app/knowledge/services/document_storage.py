"""受控文档存储：把客户端上传落到用户专属目录，隔离路径穿越。

支持两种后端（`StorageConfig.backend`）：

- ``local``（默认）：``root/user_id/<uuid><ext>`` 本地磁盘，行为与引入对象存储前一致；
- ``s3``：七牛云 Kodo / Cloudflare R2 等 S3 兼容服务，对象键
  ``{prefix}/{user_id}/<uuid><ext>``，`knowledge_doc.file_path` 存的就是这个 key。

**安全校验与后端无关**：扩展名白名单、30 MB 流式限流、文件签名（PDF 魔数 /
DOCX 双 XML / TXT 可 UTF-8 解码）全部在**写临时文件阶段**完成，校验通过后才
移动或上传——坏文件既不会落盘也不会产生对象（见 `tests/test_document_storage_s3.py`）。

两个七牛云特有的约束：

- 必须 virtual-hosted 寻址 + s3v4 签名（path 样式会 SignatureDoesNotMatch）；
- 对象键不能带前导 `/`，否则控制台里会出现空目录层级。
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
import tempfile
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID, uuid4

import anyio
from fastapi import UploadFile

from app.core.config import StorageConfig
from app.core.exceptions import NotFoundException, ValidationException

# 单块读取大小：1MB，兼顾内存与循环次数
_READ_CHUNK_SIZE = 1024 * 1024


def _build_s3_client(config: StorageConfig) -> Any:
    """构造 S3 客户端（惰性导入 boto3：local 部署不需要该依赖参与启动）。

    单独抽成模块级函数是为了让测试可以用 monkeypatch 替换掉真实客户端。
    """
    import boto3  # type: ignore[import-untyped]
    from botocore.config import Config as BotoConfig  # type: ignore[import-untyped]

    return boto3.client(
        "s3",
        endpoint_url=config.s3_endpoint,
        region_name=config.s3_region,
        aws_access_key_id=config.s3_access_key_id,
        aws_secret_access_key=config.s3_secret_access_key,
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": config.s3_addressing_style},
        ),
    )


@dataclass(frozen=True)
class StoredDocument:
    """落盘后的文档描述。

    doc_uuid 由文件名（uuid4）派生，供上层作为 KnowledgeDoc 主键，
    保证文件与数据库记录一一对应。

    S3 后端下 `path` 是对象键（如 `uploads/<user>/<uuid>.pdf`），
    `path.stem` 仍是 uuid，`doc_uuid` 语义不变。
    """

    path: Path
    original_name: str
    extension: str
    size: int

    @property
    def doc_uuid(self) -> UUID:
        """从文件名解析出的 UUID（落盘时以 uuid4.hex 命名）。"""
        return UUID(self.path.stem)


class DocumentStorage:
    """受控文档存储：root/user_id/<uuid><ext> 三层结构（local）或同构对象键（s3）。"""

    # 扩展名白名单（小写，含点）
    ALLOWED: ClassVar[frozenset[str]] = frozenset({".pdf", ".docx", ".md", ".markdown", ".txt"})

    def __init__(
        self,
        root: str | Path,
        *,
        max_file_bytes: int,
        config: StorageConfig | None = None,
    ) -> None:
        self.config = config or StorageConfig()
        self.backend = (self.config.backend or "local").strip().lower()
        self.root = Path(root).resolve()
        self.max_file_bytes = max_file_bytes
        # 临时目录必须在 root 内：同盘才能用 os.replace 原子移动
        self._tmp_dir = self.root / ".tmp"
        self._tmp_dir.mkdir(parents=True, exist_ok=True)
        if self.backend == "s3" and not self.config.s3_bucket:
            raise ValidationException("S3 存储未配置：请设置 STORAGE__S3_BUCKET")
        self._s3: Any | None = None

    # ------------------------------------------------------------------
    # S3 客户端
    # ------------------------------------------------------------------
    @property
    def _s3_client(self) -> Any:
        if self._s3 is None:
            self._s3 = _build_s3_client(self.config)
        return self._s3

    def _object_key(self, user_id: UUID, filename: str) -> str:
        prefix = self.config.s3_prefix.strip("/")
        return f"{prefix}/{user_id}/{filename}"

    @staticmethod
    def _as_key(path: str) -> str:
        """把存储路径规范成对象键：统一 `/`、去掉前导 `/`。

        Windows 上 `str(Path(...))` 会用 `\\`，直接当 key 会造出带反斜杠的对象名。
        """
        return str(path).replace("\\", "/").lstrip("/")

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    async def save(self, user_id: UUID, upload: UploadFile) -> StoredDocument:
        """保存上传文件，返回存储描述。

        校验顺序：扩展名白名单 → 流式写临时文件（大小限制）→ 文件签名。
        任一校验失败都会删除临时文件；S3 模式只在**全部校验通过后**才上传。
        """
        original = Path(upload.filename or "").name
        extension = Path(original).suffix.lower()
        if extension not in self.ALLOWED:
            raise ValidationException("不支持的文件类型")
        if not original:
            raise ValidationException("文件名不能为空")

        stem = uuid4().hex
        staging = self._tmp_dir / f"{stem}{extension}"
        size = 0
        try:
            async with await anyio.open_file(staging, "wb") as output:
                while chunk := await upload.read(_READ_CHUNK_SIZE):
                    size += len(chunk)
                    if size > self.max_file_bytes:
                        # 超限：关闭输出、删临时文件，再抛业务异常
                        await output.aclose()
                        staging.unlink(missing_ok=True)
                        raise ValidationException("文件超过 30 MB 限制")
                    await output.write(chunk)
            self._validate_signature(extension, staging)
        except (ValidationException, OSError):
            staging.unlink(missing_ok=True)
            raise

        # 校验通过后才移动/上传；finally 兜底清理（上传失败时不留临时文件）
        try:
            if self.backend == "s3":
                key = self._object_key(user_id, f"{stem}{extension}")
                content_type = mimetypes.guess_type(original)[0] or "application/octet-stream"
                # boto3 是同步阻塞 IO：必须放线程，避免卡住事件循环
                await asyncio.to_thread(
                    self._s3_client.upload_file,
                    str(staging),
                    self.config.s3_bucket,
                    key,
                    {"ContentType": content_type},
                )
                return StoredDocument(Path(key), original, extension, size)

            target_dir = self.root / str(user_id)
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{stem}{extension}"
            os.replace(staging, target)
            return StoredDocument(target, original, extension, size)
        finally:
            staging.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    @asynccontextmanager
    async def localize(self, path: str) -> AsyncIterator[Path]:
        """把文档变成「本地可读路径」，用完自动清理。

        为什么需要：`document_parser` 用 `fitz.open(file_path)` / `python-docx`
        读本地文件，S3 模式必须先下载。

        兼容迁移前的历史记录：`file_path` 是本地绝对路径时直接用，不下载、
        也不删除（那是用户的原始文件，不是临时副本）。
        """
        candidate = Path(path)
        if candidate.is_file():
            yield candidate
            return
        if self.backend != "s3":
            yield candidate  # 交给上层抛 FileNotFoundError，保持旧行为
            return

        key = self._as_key(path)
        suffix = candidate.suffix or ".bin"
        handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        temp_path = Path(handle.name)
        try:
            with handle:
                await asyncio.to_thread(
                    self._s3_client.download_fileobj,
                    self.config.s3_bucket,
                    key,
                    handle,
                )
            yield temp_path
        finally:
            temp_path.unlink(missing_ok=True)

    def presigned_url(self, path: str, expires_in: int | None = None) -> str | None:
        """生成下载直链（仅 S3 后端；本地返回 None 由调用方流式返回文件）。"""
        if self.backend != "s3":
            return None
        key = self._as_key(path)
        expiry = expires_in or self.config.presign_expires_seconds
        return str(
            self._s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.config.s3_bucket, "Key": key},
                ExpiresIn=expiry,
            )
        )

    # ------------------------------------------------------------------
    # 删
    # ------------------------------------------------------------------
    def delete(self, user_id: UUID, path: str) -> None:
        """删除文件；S3 模式先校验 key 属于该用户（防越权删他人对象）。"""
        if self.backend == "s3":
            key = self._as_key(path)
            expected_prefix = f"{self.config.s3_prefix.strip('/')}/{user_id}/"
            if not key.startswith(expected_prefix):
                raise NotFoundException("文档不存在")
            self._s3_client.delete_object(Bucket=self.config.s3_bucket, Key=key)
            return

        candidate = Path(path).resolve()
        allowed_root = (self.root / str(user_id)).resolve()
        if candidate.parent != allowed_root:
            raise NotFoundException("文档不存在")
        candidate.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------
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
