"""向量库不可达时检索端点必须返回 503（不是 500）。

为什么区分：500 表示「服务端有缺陷」，调用方不会重试；
503 表示「依赖暂时不可用」，网关与前端可以退避重试、
监控也能把「向量库挂了」和「代码写错了」分开报警。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.knowledge.api.endpoints import search
from app.knowledge.services.vector_backends.base import VectorStoreUnavailableError
from app.schemas.knowledge import KnowledgeSearchIn


class _FailingService:
    async def search_for_context(self, user_id: Any, query: str, *, top_k: int) -> list[Any]:
        raise VectorStoreUnavailableError("pgvector 不可达：connection refused")


class _User:
    id = uuid4()


class TestSearchUnavailableMapping:
    async def test_returns_503(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await search(
                KnowledgeSearchIn(query="任意查询"),
                current_user=_User(),  # type: ignore[arg-type]
                service=_FailingService(),  # type: ignore[arg-type]
            )

        assert exc_info.value.status_code == 503
        # 错误消息不能把内部 DSN/栈暴露给调用方
        assert "connection refused" not in str(exc_info.value.detail)
        assert "向量库" in str(exc_info.value.detail)

    async def test_other_errors_are_not_swallowed(self) -> None:
        """非「依赖不可达」的异常必须继续抛（不能被 503 掩盖成可重试）。"""

        class _BrokenService:
            async def search_for_context(self, *args: Any, **kwargs: Any) -> list[Any]:
                raise ValueError("查询参数非法")

        with pytest.raises(ValueError):
            await search(
                KnowledgeSearchIn(query="任意查询"),
                current_user=SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
                service=_BrokenService(),  # type: ignore[arg-type]
            )
