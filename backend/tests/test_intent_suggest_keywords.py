"""「AI 智能拆分关键词」服务与 HTTP 端点单测（Issue #70）。

覆盖：
1. `suggest_keywords()` 正常 LLM 返回中英混合 JSON → 大小写不敏感去重、避开已有词、截断至 `max_keywords`；
2. LLM 抛异常或返回非法 JSON → fail-open 回落确定性切词，返回 `source="fallback"` 且结果非空；
3. `POST /api/v1/tasks/suggest-keywords` 未鉴权返回 401、topic 过短（含纯空白）返回 422、鉴权后正常返回 200 且参数透传。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.deps import get_current_active_user
from app.api.v1.endpoints.intent import router as intent_router
from app.core.exceptions import AppException
from app.db.session import get_db
from app.services.intent_service import suggest_keywords


def _make_manager(response_content: str | None = None, *, error: Exception | None = None) -> Any:
    """构造轻量 LLM manager 替身，验证传参并控制返回/异常。"""

    async def _chat(messages: list[Any], **kwargs: Any) -> Any:
        _ = (messages, kwargs)
        if error is not None:
            raise error
        return SimpleNamespace(content=response_content)

    return SimpleNamespace(chat=AsyncMock(side_effect=_chat))


def _make_client(*, authenticated: bool) -> TestClient:
    app = FastAPI()

    @app.exception_handler(AppException)
    async def _handle_app_exc(_: Request, exc: AppException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": exc.error_code, "message": exc.message},
        )

    app.include_router(intent_router, prefix="/api/v1")

    async def _fake_db() -> Any:
        yield None

    app.dependency_overrides[get_db] = _fake_db
    if authenticated:
        user = SimpleNamespace(id=uuid4(), username="tester", role="user", is_active=True)

        async def _fake_user() -> Any:
            return user

        app.dependency_overrides[get_current_active_user] = _fake_user

    return TestClient(app)


class TestSuggestKeywordsService:
    @pytest.mark.asyncio
    async def test_llm_suggestions_deduplicate_exclude_existing_and_cap(self) -> None:
        llm_json = """```json
{
  "suggestions": [
    {"term": "RAG", "lang": "en"},
    {"term": "检索增强生成", "lang": "zh"},
    {"term": "多智能体协作", "lang": "zh"},
    {"term": "multi-agent collaboration", "lang": "en"},
    {"term": "Multi-Agent Collaboration", "lang": "en"},
    {"term": "评估基准", "lang": "zh"},
    {"term": "evaluation benchmark", "lang": "en"}
  ]
}
```"""
        manager = _make_manager(llm_json)
        with patch("app.llm.factory.get_llm_manager", return_value=manager):
            res = await suggest_keywords(
                "检索增强生成与多智能体协作评估综述",
                output_language="zh",
                existing_keywords=["rag"],
                max_keywords=4,
            )

        assert res.source == "llm"
        terms = [item.term for item in res.suggestions]
        assert terms == [
            "检索增强生成",
            "多智能体协作",
            "multi-agent collaboration",
            "评估基准",
        ]
        assert [item.lang for item in res.suggestions] == ["zh", "zh", "en", "zh"]
        assert manager.chat.await_count == 1
        call_kwargs = manager.chat.await_args.kwargs
        assert call_kwargs["max_tokens"] <= 240

    @pytest.mark.asyncio
    async def test_llm_error_falls_back_fail_open(self) -> None:
        manager = _make_manager(error=RuntimeError("upstream timeout"))
        with patch("app.llm.factory.get_llm_manager", return_value=manager):
            res = await suggest_keywords(
                "检索增强生成、知识图谱与多智能体协同推理",
                output_language="zh",
                existing_keywords=["知识图谱"],
                max_keywords=6,
            )

        assert res.source == "fallback"
        assert res.suggestions
        terms = [item.term for item in res.suggestions]
        assert "知识图谱" not in terms
        assert any("检索增强生成" in term for term in terms)

    @pytest.mark.asyncio
    async def test_invalid_llm_json_falls_back_fail_open(self) -> None:
        manager = _make_manager("sorry, cannot answer right now")
        with patch("app.llm.factory.get_llm_manager", return_value=manager):
            res = await suggest_keywords(
                "Retrieval Augmented Generation Evaluation Benchmark",
                output_language="en",
                max_keywords=5,
            )

        assert res.source == "fallback"
        assert res.suggestions
        assert all(item.lang == "en" for item in res.suggestions)


class TestSuggestKeywordsEndpoint:
    def test_requires_authentication(self) -> None:
        client = _make_client(authenticated=False)
        resp = client.post(
            "/api/v1/tasks/suggest-keywords",
            json={"topic": "检索增强生成综述"},
        )
        assert resp.status_code == 401

    def test_rejects_short_or_blank_topic_with_422(self) -> None:
        client = _make_client(authenticated=True)
        resp = client.post(
            "/api/v1/tasks/suggest-keywords",
            json={"topic": "  RAG  "},
        )
        assert resp.status_code == 422

    def test_returns_suggestions_and_forwards_options(self) -> None:
        client = _make_client(authenticated=True)
        mock_suggest = AsyncMock(
            return_value=SimpleNamespace(
                suggestions=[
                    {"term": "大语言模型", "lang": "zh"},
                    {"term": "large language model", "lang": "en"},
                ],
                source="llm",
            )
        )
        with patch("app.api.v1.endpoints.intent.suggest_keywords", mock_suggest):
            resp = client.post(
                "/api/v1/tasks/suggest-keywords",
                json={
                    "topic": "大语言模型在代码审查中的应用综述",
                    "output_language": "en",
                    "existing_keywords": ["code review"],
                    "max_keywords": 6,
                },
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data == {
            "suggestions": [
                {"term": "大语言模型", "lang": "zh"},
                {"term": "large language model", "lang": "en"},
            ],
            "source": "llm",
        }
        mock_suggest.assert_awaited_once_with(
            "大语言模型在代码审查中的应用综述",
            output_language="en",
            existing_keywords=["code review"],
            max_keywords=6,
        )
