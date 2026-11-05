"""DocumentStorage 的 S3 分支（七牛云 Kodo）单元测试。

## 为什么必须 mock 而不是连真库

CI 没有七牛凭据，且对象存储不是单测的职责；这里只锁住「协议与安全契约」：

- **坏文件绝不允许产生对象**（签名/大小校验必须先于上传）；
- 对象键必须是 `{prefix}/{user_id}/{uuid}{ext}`，`path.stem` 仍是 uuid
  （`StoredDocument.doc_uuid` 依赖它）；
- 删除必须先校验「key 属于该用户」，否则等于允许越权删别人文件；
- 预签名有效期取配置值；
- `localize` 用完必须删临时文件（否则每次解析都泄漏一个 30MB 文件）。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import UploadFile

from app.core.config import StorageConfig
from app.core.exceptions import NotFoundException, ValidationException
from app.knowledge.services.document_storage import DocumentStorage


class FakeS3Client:
    """极简 S3 替身：记录调用，不联网。"""

    def __init__(self) -> None:
        self.uploads: list[tuple[str, str, str, dict | None]] = []
        self.deletes: list[tuple[str, str]] = []
        self.presigns: list[tuple[str, dict, int]] = []
        self.downloads: list[tuple[str, str]] = []
        self.download_body = b"s3-file-body"

    def upload_file(
        self,
        Filename: str,
        Bucket: str,
        Key: str,
        ExtraArgs: dict | None = None,
    ) -> None:
        # 记录磁盘上的临时文件确实存在（上传发生在写盘之后）
        assert Path(Filename).exists(), "上传时临时文件应仍存在"
        self.uploads.append((Filename, Bucket, Key, ExtraArgs))

    def delete_object(self, Bucket: str, Key: str) -> None:
        self.deletes.append((Bucket, Key))

    def generate_presigned_url(self, operation: str, Params: dict, ExpiresIn: int) -> str:
        self.presigns.append((operation, Params, ExpiresIn))
        return "https://cdn.example.com/signed?token=1"

    def download_fileobj(self, Bucket: str, Key: str, Fileobj: object) -> None:
        self.downloads.append((Bucket, Key))
        Fileobj.write(self.download_body)  # type: ignore[attr-defined]


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeS3Client:
    client = FakeS3Client()
    monkeypatch.setattr(
        "app.knowledge.services.document_storage._build_s3_client",
        lambda _config: client,
    )
    return client


def _s3_config(**overrides: object) -> StorageConfig:
    payload: dict[str, object] = {
        "backend": "s3",
        "s3_endpoint": "https://s3-cn-east-1.qiniucs.com",
        "s3_region": "cn-east-1",
        "s3_bucket": "veripaper-storage",
        "s3_access_key_id": "ak",
        "s3_secret_access_key": "sk",
        "s3_prefix": "uploads",
        "presign_expires_seconds": 600,
    }
    payload.update(overrides)
    return StorageConfig(**payload)  # type: ignore[arg-type]


def _upload(name: str, body: bytes) -> UploadFile:
    return UploadFile(filename=name, file=BytesIO(body))


@pytest.mark.asyncio
async def test_save_uploads_after_validation_and_cleans_temp(
    tmp_path: Path, fake_client: FakeS3Client
) -> None:
    user_id = uuid4()
    storage = DocumentStorage(tmp_path, max_file_bytes=1024, config=_s3_config())

    stored = await storage.save(user_id, _upload("paper.pdf", b"%PDF-1.4\nbody"))

    assert stored.path == Path(f"uploads/{user_id}") / stored.path.name
    assert stored.path.suffix == ".pdf"
    # doc_uuid 由 path.stem 反解（stem 是 uuid4().hex，无连字符）
    assert stored.doc_uuid.hex == stored.path.stem

    assert len(fake_client.uploads) == 1
    filename, bucket, key, extra = fake_client.uploads[0]
    assert bucket == "veripaper-storage"
    assert key == f"uploads/{user_id}/{stored.path.name}"
    assert extra == {"ContentType": "application/pdf"}
    # 上传后临时文件必须清理
    assert not Path(filename).exists()
    assert list((tmp_path / ".tmp").glob("*.pdf")) == []


@pytest.mark.asyncio
async def test_bad_signature_never_uploads(tmp_path: Path, fake_client: FakeS3Client) -> None:
    storage = DocumentStorage(tmp_path, max_file_bytes=1024, config=_s3_config())

    with pytest.raises(ValidationException, match="文件签名"):
        await storage.save(uuid4(), _upload("paper.pdf", b"not-a-pdf"))

    assert fake_client.uploads == []
    assert list((tmp_path / ".tmp").glob("*.pdf")) == []


@pytest.mark.asyncio
async def test_oversized_file_never_uploads(tmp_path: Path, fake_client: FakeS3Client) -> None:
    storage = DocumentStorage(tmp_path, max_file_bytes=4, config=_s3_config())

    with pytest.raises(ValidationException, match="30 MB"):
        await storage.save(uuid4(), _upload("paper.txt", b"12345"))

    assert fake_client.uploads == []
    assert list((tmp_path / ".tmp").glob("*")) == []


@pytest.mark.asyncio
async def test_delete_rejects_prefix_of_another_user(
    tmp_path: Path, fake_client: FakeS3Client
) -> None:
    owner, intruder = uuid4(), uuid4()
    storage = DocumentStorage(tmp_path, max_file_bytes=1024, config=_s3_config())

    with pytest.raises(NotFoundException):
        storage.delete(intruder, f"uploads/{owner}/abc.pdf")

    assert fake_client.deletes == []


@pytest.mark.asyncio
async def test_delete_removes_object_for_owner(tmp_path: Path, fake_client: FakeS3Client) -> None:
    user_id = uuid4()
    storage = DocumentStorage(tmp_path, max_file_bytes=1024, config=_s3_config())

    storage.delete(user_id, f"uploads/{user_id}/abc.pdf")

    assert fake_client.deletes == [("veripaper-storage", f"uploads/{user_id}/abc.pdf")]


def test_presigned_url_uses_configured_expiry(tmp_path: Path, fake_client: FakeS3Client) -> None:
    user_id = uuid4()
    storage = DocumentStorage(tmp_path, max_file_bytes=1024, config=_s3_config())

    url = storage.presigned_url(f"uploads/{user_id}/abc.pdf")

    assert url == "https://cdn.example.com/signed?token=1"
    operation, params, expires_in = fake_client.presigns[0]
    assert operation == "get_object"
    assert params == {"Bucket": "veripaper-storage", "Key": f"uploads/{user_id}/abc.pdf"}
    assert expires_in == 600


def test_presigned_url_none_in_local_mode(tmp_path: Path) -> None:
    storage = DocumentStorage(tmp_path, max_file_bytes=1024)

    assert storage.presigned_url("any/path.pdf") is None


@pytest.mark.asyncio
async def test_localize_downloads_then_removes_temp(
    tmp_path: Path, fake_client: FakeS3Client
) -> None:
    user_id = uuid4()
    storage = DocumentStorage(tmp_path, max_file_bytes=1024, config=_s3_config())
    key = f"uploads/{user_id}/abc.pdf"

    async with storage.localize(key) as local_path:
        assert Path(local_path).read_bytes() == b"s3-file-body"
        assert fake_client.downloads == [("veripaper-storage", key)]
        temp_path = Path(local_path)

    assert not temp_path.exists()


@pytest.mark.asyncio
async def test_localize_prefers_existing_local_file(
    tmp_path: Path, fake_client: FakeS3Client
) -> None:
    """迁移前写入的 `file_path` 是本地绝对路径，S3 模式下也必须能读。"""
    legacy = tmp_path / "legacy.pdf"
    legacy.write_bytes(b"%PDF-1.4 legacy")
    storage = DocumentStorage(tmp_path, max_file_bytes=1024, config=_s3_config())

    async with storage.localize(str(legacy)) as local_path:
        assert Path(local_path).read_bytes() == b"%PDF-1.4 legacy"

    assert fake_client.downloads == []
    assert legacy.exists()  # 本地文件不能被「用完删除」
