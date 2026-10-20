"""参考文献导入 HTTP 端点测试（Phase 1 P1-E 接线）。

对应计划 §1.5 的「遗留：`POST /references/import` 端点未接线」。

端点：
    POST /api/v1/references/import   导入 BibTeX / RIS，返回解析后的条目预览

设计约定：
- **纯解析、不落库**：导入只做「文本 → CslItem 列表」的预览，用户确认后再另走保存；
  这样避免半成品数据污染引用库，也让前端能做「导入前预览」。
- 格式可显式指定（`format=bibtex|ris`），也可省略由服务端按内容探测。
- 非法输入 → **400** 且 `detail` 可读（逐条定位信息，绝不静默返回半成品）。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import references as references_endpoint

BIBTEX_SAMPLE = """
@article{es2023ragas,
  author = {Es, Shahul and James, Jithin},
  title = {Ragas: Automated Evaluation of Retrieval Augmented Generation},
  journal = {arXiv preprint},
  year = {2023},
  doi = {10.18653/v1/2023.evalnlp-1.16}
}
"""

RIS_SAMPLE = """TY  - JOUR
AU  - 牛耕田
AU  - 李泽
TI  - 面向中文文献综述的自动化方法
PY  - 2024
JO  - 中文信息学报
VL  - 38
IS  - 4
SP  - 100
EP  - 110
DO  - 10.1234/zh.journal.2024.001
ER  -
"""


@pytest.fixture
def client() -> TestClient:
    application = FastAPI()
    application.include_router(references_endpoint.router, prefix="/api/v1")

    from app.core.exceptions import AppException

    @application.exception_handler(AppException)
    async def _handle_app(_: Any, exc: AppException) -> Any:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": exc.error_code, "message": exc.message},
        )

    return TestClient(application)


class TestImportBibtex:
    def test_imports_bibtex_entries(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/references/import",
            json={"content": BIBTEX_SAMPLE, "format": "bibtex"},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["format"] == "bibtex"
        assert len(payload["items"]) == 1
        item = payload["items"][0]
        assert "Ragas" in item["title"]
        assert item["authors"] == ["Es, Shahul", "James, Jithin"]
        assert item["year"] == 2023

    def test_autodetects_bibtex_without_format(self, client: TestClient) -> None:
        response = client.post("/api/v1/references/import", json={"content": BIBTEX_SAMPLE})

        assert response.status_code == 200
        assert response.json()["format"] == "bibtex"

    def test_invalid_bibtex_returns_400(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/references/import",
            json={"content": "@article{K1, title = {Unclosed", "format": "bibtex"},
        )

        assert response.status_code == 400
        assert response.json()["detail"]


class TestImportRis:
    def test_imports_ris_entries_preserving_chinese(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/references/import",
            json={"content": RIS_SAMPLE, "format": "ris"},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["format"] == "ris"
        assert len(payload["items"]) == 1
        item = payload["items"][0]
        assert item["title"] == "面向中文文献综述的自动化方法"
        assert item["authors"] == ["牛耕田", "李泽"]
        assert item["year"] == 2024
        assert item["page"] == "100-110"

    def test_autodetects_ris_without_format(self, client: TestClient) -> None:
        response = client.post("/api/v1/references/import", json={"content": RIS_SAMPLE})

        assert response.status_code == 200
        assert response.json()["format"] == "ris"

    def test_invalid_ris_returns_400(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/references/import",
            json={"content": "TY  - JOUR\nTI  - x\n", "format": "ris"},
        )

        assert response.status_code == 400


class TestEdgeCases:
    def test_empty_content_returns_empty_list(self, client: TestClient) -> None:
        """空输入 → 空列表（不是错误）。"""
        response = client.post(
            "/api/v1/references/import", json={"content": "   ", "format": "bibtex"}
        )

        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_unknown_format_returns_400(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/references/import", json={"content": BIBTEX_SAMPLE, "format": "endnote"}
        )

        assert response.status_code == 400

    def test_undetectable_content_returns_400(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/references/import", json={"content": "这既不是 BibTeX 也不是 RIS"}
        )

        assert response.status_code == 400

    def test_too_large_content_returns_400(self, client: TestClient) -> None:
        # 1 MB 上限：单条约 23 字节，构造 50000 条 ≈ 1.15 MB
        oversized = "@article{k, title={t}}\n" * 50000
        assert len(oversized.encode("utf-8")) > 1024 * 1024  # 前提校验

        response = client.post(
            "/api/v1/references/import",
            json={"content": oversized, "format": "bibtex"},
        )

        assert response.status_code == 400
        assert "上限" in response.json()["detail"]
