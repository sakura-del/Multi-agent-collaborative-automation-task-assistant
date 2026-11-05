"""文档原文下载端点：S3 直链 vs 本地流式的三条分支。

为什么要单独测：下载是唯一「按 DB 里的路径去读盘 / 换直链」的入口，
路径归属校验一旦缺失就是任意文件读取（历史脏数据或手工改库都可能触发）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote
from uuid import uuid4

import pytest
from fastapi.responses import FileResponse, RedirectResponse

from app.core.exceptions import NotFoundException
from app.knowledge.api import endpoints as ep


class _Doc:
    def __init__(self, file_path: str | None, original: str = "论文终稿.pdf") -> None:
        self.id = uuid4()
        self.file_path = file_path
        self.meta_info = {"original_name": original} if file_path else {}


class _Service:
    def __init__(self, doc: _Doc | None) -> None:
        self._doc = doc

    async def get_document(self, user_id: object, doc_id: object) -> _Doc:
        if self._doc is None:
            raise NotFoundException("文档不存在")
        return self._doc


class _Storage:
    def __init__(self, url: str | None) -> None:
        self._url = url

    def presigned_url(self, path: str, expires_in: int | None = None) -> str | None:
        return self._url


def _patch_upload_root(monkeypatch: pytest.MonkeyPatch, root: str) -> None:
    monkeypatch.setattr(
        ep,
        "get_settings",
        lambda: SimpleNamespace(app=SimpleNamespace(upload_root=root)),
    )


class TestDownload:
    async def test_s3_backend_redirects_to_presigned(self) -> None:
        doc = _Doc("uploads/abc/xyz.pdf")
        response = await ep.download_document(  # type: ignore[arg-type]
            doc.id,
            current_user=SimpleNamespace(id=uuid4()),
            service=_Service(doc),
            storage=_Storage("https://cdn.example.com/signed"),
        )

        assert isinstance(response, RedirectResponse)
        assert response.status_code == 307
        assert response.headers["location"] == "https://cdn.example.com/signed"

    async def test_local_backend_returns_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user_dir = tmp_path / str(uuid4())
        user_dir.mkdir(parents=True)
        target = user_dir / "abc.pdf"
        target.write_bytes(b"%PDF-1.4 body")
        _patch_upload_root(monkeypatch, str(tmp_path))

        response = await ep.download_document(  # type: ignore[arg-type]
            uuid4(),
            current_user=SimpleNamespace(id=uuid4()),
            service=_Service(_Doc(str(target))),
            storage=_Storage(None),
        )

        assert isinstance(response, FileResponse)
        assert response.media_type == "application/pdf"
        # Starlette 对非 ASCII 文件名用 RFC5987 编码（filename*=utf-8''%XX）
        disposition = unquote(response.headers["content-disposition"])
        assert "论文终稿.pdf" in disposition

    async def test_path_outside_upload_root_is_404(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """历史脏数据可能写入任意路径；越出上传根目录必须按「不存在」处理。"""
        outside = tmp_path / "secret.txt"
        outside.write_text("top secret", encoding="utf-8")
        _patch_upload_root(monkeypatch, str(tmp_path / "uploads"))

        with pytest.raises(ep.HTTPException) as exc:
            await ep.download_document(  # type: ignore[arg-type]
                uuid4(),
                current_user=SimpleNamespace(id=uuid4()),
                service=_Service(_Doc(str(outside))),
                storage=_Storage(None),
            )

        assert exc.value.status_code == 404

    async def test_missing_file_path_is_404(self) -> None:
        with pytest.raises(ep.HTTPException) as exc:
            await ep.download_document(  # type: ignore[arg-type]
                uuid4(),
                current_user=SimpleNamespace(id=uuid4()),
                service=_Service(_Doc(None)),
                storage=_Storage(None),
            )

        assert exc.value.status_code == 404

    async def test_other_users_document_is_404(self) -> None:
        with pytest.raises(NotFoundException):
            await ep.download_document(  # type: ignore[arg-type]
                uuid4(),
                current_user=SimpleNamespace(id=uuid4()),
                service=_Service(None),
                storage=_Storage(None),
            )
