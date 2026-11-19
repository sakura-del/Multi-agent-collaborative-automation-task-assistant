"""Agent 工具网关回归（Issue #8 P1）。

⚠️ 铁律 #0：本机 Windows 不执行 pytest；本文件按静态检查交付，
实际运行在测试机 10.0.10.2（见 AGENTS.md §4.1）。

覆盖：
- 目录恰好 12 个工具，且参数都是合法 JSON Schema（模型可直接用）；
- 未知工具 → 404；
- 写工具未确认 → 409 TOOL_CONFIRMATION_REQUIRED（**不执行**）；
- 参数不合法 → 422 TOOL_ARGUMENTS_INVALID；
- 只读工具走通（list_tasks / get_quota 用轻量替身）；
- 导入非法内容 → 校验错误（不落到 DB 写入）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.core.exceptions import NotFoundException
from app.tools.agent import (
    ConfirmationRequiredException,
    ToolContext,
    ToolValidationException,
    invoke_tool,
    list_tools,
)
from app.tools.agent.registry import TOOL_SPECS
from app.tools.agent.spec import ToolSpec  # noqa: F401 - 类型自检用


class _ExecutionStub:
    """只实现测试用到的读方法。"""

    async def count_tasks(self, user_id: Any, **kwargs: Any) -> int:
        return 1

    async def list_tasks(self, user_id: Any, **kwargs: Any) -> list[Any]:
        return [
            SimpleNamespace(
                task_id=uuid4(),
                title="多智能体协作综述",
                status="completed",
                plan_version=1,
                updated_at=None,
            )
        ]


class _PlanStub:
    async def get_usage(self, user_id: Any) -> dict[str, Any]:
        return {
            "plan_name": "专业版",
            "period_key": "2026-11",
            "reviews_used": 3,
            "monthly_reviews": 50,
            "reviews_remaining": 47,
            "period_end": None,
        }


def _context() -> ToolContext:
    return ToolContext(
        db=None,  # type: ignore[arg-type] - 只读用例不碰 db
        user_id=uuid4(),
        execution=_ExecutionStub(),  # type: ignore[arg-type]
        plan_service=_PlanStub(),  # type: ignore[arg-type]
        quota=None,
        workspace_ids=(),
    )


def test_catalog_has_exactly_twelve_schema_typed_tools() -> None:
    tools = list_tools()
    assert len(tools) == 12
    assert len(TOOL_SPECS) == 12
    assert {tool["domain"] for tool in tools} == {
        "tasks",
        "references",
        "knowledge",
        "quota",
        "search",
    }
    for tool in tools:
        assert tool["parameters"]["type"] == "object"
        assert isinstance(tool["summary"], str) and tool["summary"]
    # 写操作必须被显式标记（前端据此出确认卡）
    assert {tool["name"] for tool in tools if tool["write"]} == {
        "create_task",
        "approve_task_plan",
        "cancel_task",
        "retry_task_node",
        "import_references",
    }


async def test_unknown_tool_is_404() -> None:
    with pytest.raises(NotFoundException):
        await invoke_tool(_context(), "does_not_exist", {})


async def test_write_tool_requires_confirmation_and_does_not_execute() -> None:
    with pytest.raises(ConfirmationRequiredException) as excinfo:
        await invoke_tool(
            _context(),
            "cancel_task",
            {"task_id": str(uuid4())},
            confirmed=False,
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.error_code == "TOOL_CONFIRMATION_REQUIRED"
    assert excinfo.value.details["tool"] == "cancel_task"


async def test_invalid_arguments_are_422() -> None:
    with pytest.raises(ToolValidationException) as excinfo:
        await invoke_tool(_context(), "list_tasks", {"limit": 999}, confirmed=True)
    assert excinfo.value.status_code == 422
    assert excinfo.value.error_code == "TOOL_ARGUMENTS_INVALID"


async def test_list_tasks_returns_compact_rows() -> None:
    envelope = await invoke_tool(_context(), "list_tasks", {"limit": 5}, confirmed=False)
    assert envelope["tool"] == "list_tasks"
    assert envelope["result"]["total"] == 1
    assert envelope["result"]["tasks"][0]["title"] == "多智能体协作综述"


async def test_get_quota_maps_usage_fields() -> None:
    envelope = await invoke_tool(_context(), "get_quota", {}, confirmed=False)
    result = envelope["result"]
    assert result["plan"] == "专业版"
    assert result["reviews_remaining"] == 47


async def test_import_references_rejects_unparsable_content_before_db() -> None:
    with pytest.raises(ToolValidationException) as excinfo:
        await invoke_tool(
            _context(),
            "import_references",
            {"content": "这不是任何文献格式", "format": "bibtex"},
            confirmed=True,
        )
    assert "未解析到任何文献条目" in excinfo.value.message


async def test_route_intent_falls_back_when_model_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import intent_service

    def _boom() -> Any:
        raise RuntimeError("platform llm down")

    monkeypatch.setattr("app.llm.factory.get_llm_manager", _boom)
    result = await intent_service.classify_text("帮我综述多智能体协作")
    assert result.intent == "literature_review"
    assert result.source == "fallback"
    assert result.topic.startswith("帮我综述")
