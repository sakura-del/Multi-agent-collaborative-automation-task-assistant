"""MCP server 工具测试（Phase 2 · 开放 API Task 5）。

## 设计约束（本文件锁定）

1. **MCP server 不直连数据库**：它是**客户端**，调开放 API。理由：
   - 权限与限流自动生效（不用在 MCP 层重写）；
   - MCP server 可独立部署（甚至跑在用户机器上）；
   - 避免 MCP 成为绕过配额的后门。
2. **5 个工具覆盖完整闭环**：发起 → 查状态 → 取产出 → 导出。
3. **`get_task_status` 只返摘要**：不返全文，避免撑爆模型上下文。
4. **错误映射**：HTTP 401 → 「Key 无效」；403 → 「权限不足」；404 → 「任务不存在」。

## 为什么不测真实网络

MCP 工具是薄封装（参数校验 + 调 API + 格式化）。用 httpx mock 测封装逻辑，
真实端到端在服务器验收（Task 6）跑。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.mcp.tools import (
    MCP_TOOLS,
    McpToolError,
    export_task,
    get_task_output,
    get_task_status,
    list_tasks,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if not isinstance(payload, str) else payload

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    """记录调用的假 httpx 客户端。"""

    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((method, url, kwargs))
        for pattern, response in self.responses.items():
            if pattern in url:
                return response
        return _FakeResponse(404, {"message": "not found"})


class TestToolCatalog:
    def test_five_tools_defined(self) -> None:
        """工具太多会让模型选择困难（MCP 客户端把全部描述塞进上下文）。"""
        names = {tool["name"] for tool in MCP_TOOLS}
        assert names == {
            "create_literature_review",
            "get_task_status",
            "list_tasks",
            "get_task_output",
            "export_task",
        }

    def test_every_tool_has_schema(self) -> None:
        for tool in MCP_TOOLS:
            assert tool["description"], f"{tool['name']} 缺描述"
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"


class TestGetTaskStatus:
    def test_returns_summary_not_full_output(self) -> None:
        """**关键**：状态工具不能返全文（会撑爆上下文）。"""
        client = _FakeClient(
            {
                "/tasks/t1": _FakeResponse(
                    200,
                    {
                        "task_id": "t1",
                        "status": "completed",
                        "title": "RAG 综述",
                        "plan_version": 1,
                        "revision": 2,
                        "final_output": "很长的正文" * 500,
                        "artifacts": [{"artifact_id": "a1", "kind": "markdown"}],
                    },
                )
            }
        )

        result = get_task_status(client, "t1")

        assert result["status"] == "completed"
        assert result["title"] == "RAG 综述"
        assert "final_output" not in result, "状态工具绝不能返回全文"
        # 但应告知「有产出可下载」
        assert result["has_output"] is True
        assert result["artifact_count"] == 1

    def test_not_found_raises(self) -> None:
        client = _FakeClient({"/tasks/missing": _FakeResponse(404, {"message": "任务不存在"})})

        with pytest.raises(McpToolError, match="不存在"):
            get_task_status(client, "missing")

    def test_unauthorized_raises_readable_error(self) -> None:
        client = _FakeClient({"/tasks/t1": _FakeResponse(401, {"message": "invalid"})})

        with pytest.raises(McpToolError, match="Key"):
            get_task_status(client, "t1")


class TestListTasks:
    def test_returns_compact_rows(self) -> None:
        client = _FakeClient(
            {
                "/tasks": _FakeResponse(
                    200,
                    [
                        {
                            "task_id": "t1",
                            "status": "completed",
                            "title": "A",
                            "plan_version": 1,
                            "revision": 1,
                            "created_at": "2026-10-21T10:00:00Z",
                            "updated_at": "2026-10-21T10:10:00Z",
                        }
                    ],
                )
            }
        )

        rows = list_tasks(client, limit=5)

        assert len(rows) == 1
        assert rows[0]["task_id"] == "t1"
        assert rows[0]["status"] == "completed"
        # 调用应带上 limit
        assert client.calls[0][2]["params"]["limit"] == 5

    def test_status_filter_passed(self) -> None:
        client = _FakeClient({"/tasks": _FakeResponse(200, [])})

        list_tasks(client, status="completed")

        assert client.calls[0][2]["params"]["limit"] == 20


class TestGetTaskOutput:
    def test_returns_markdown(self) -> None:
        client = _FakeClient(
            {
                "/tasks/t1": _FakeResponse(
                    200,
                    {
                        "task_id": "t1",
                        "status": "completed",
                        "title": "RAG",
                        "final_output": "# 综述\n\n正文内容",
                        "review_report": {"references": [{"id": 1}, {"id": 2}]},
                        "artifacts": [{"artifact_id": "a1", "kind": "markdown"}],
                    },
                )
            }
        )

        result = get_task_output(client, "t1")

        assert result["markdown"] == "# 综述\n\n正文内容"
        assert result["reference_count"] == 2
        assert result["artifact_count"] == 1

    def test_no_output_yet(self) -> None:
        client = _FakeClient(
            {
                "/tasks/t1": _FakeResponse(
                    200,
                    {
                        "task_id": "t1",
                        "status": "running",
                        "title": "RAG",
                        "final_output": None,
                        "review_report": None,
                        "artifacts": [],
                    },
                )
            }
        )

        result = get_task_output(client, "t1")

        assert result["markdown"] is None
        assert result["status"] == "running"


class TestExportTask:
    def test_markdown_returns_artifact_path(self) -> None:
        client = _FakeClient(
            {
                "/tasks/t1": _FakeResponse(
                    200,
                    {
                        "task_id": "t1",
                        "status": "completed",
                        "artifacts": [
                            {"artifact_id": "a-md", "kind": "markdown"},
                            {"artifact_id": "a-docx", "kind": "docx"},
                        ],
                    },
                )
            }
        )

        result = export_task(client, "t1", format="markdown")

        assert result["artifact_id"] == "a-md"
        assert "a-md" in result["download_path"]

    def test_docx_picks_docx_artifact(self) -> None:
        client = _FakeClient(
            {
                "/tasks/t1": _FakeResponse(
                    200,
                    {
                        "task_id": "t1",
                        "status": "completed",
                        "artifacts": [
                            {"artifact_id": "a-md", "kind": "markdown"},
                            {"artifact_id": "a-docx", "kind": "docx"},
                        ],
                    },
                )
            }
        )

        result = export_task(client, "t1", format="docx")

        assert result["artifact_id"] == "a-docx"

    def test_bib_format_uses_references_endpoint(self) -> None:
        client = _FakeClient(
            {
                "/tasks/t1": _FakeResponse(
                    200, {"task_id": "t1", "status": "completed", "artifacts": []}
                )
            }
        )

        result = export_task(client, "t1", format="bib")

        assert result["download_path"].endswith("/references.bib")

    def test_unknown_format_rejected(self) -> None:
        client = _FakeClient({})

        with pytest.raises(McpToolError, match="format"):
            export_task(client, "t1", format="pdf")

    def test_missing_artifact_raises(self) -> None:
        client = _FakeClient(
            {
                "/tasks/t1": _FakeResponse(
                    200, {"task_id": "t1", "status": "completed", "artifacts": []}
                )
            }
        )

        with pytest.raises(McpToolError, match="产出"):
            export_task(client, "t1", format="docx")


class TestErrorMapping:
    def test_403_maps_to_permission_error(self) -> None:
        client = _FakeClient(
            {"/tasks/t1": _FakeResponse(403, {"message": "API Key 缺少所需权限：tasks:read"})}
        )

        with pytest.raises(McpToolError, match="权限"):
            get_task_status(client, "t1")

    def test_500_maps_to_generic_error(self) -> None:
        client = _FakeClient({"/tasks/t1": _FakeResponse(500, {"message": "内部错误"})})

        with pytest.raises(McpToolError, match="服务"):
            get_task_status(client, "t1")


class TestCreateReview:
    def test_create_posts_payload(self) -> None:
        from app.mcp.tools import create_literature_review

        client = _FakeClient(
            {"/tasks": _FakeResponse(201, {"task_id": "new1", "status": "planning"})}
        )

        result = create_literature_review(client, topic="检索增强生成综述", keywords=["RAG", "LLM"])

        assert result["task_id"] == "new1"
        assert result["status"] == "planning"
        payload = client.calls[0][2]["json"]
        assert payload["topic"] == "检索增强生成综述"
        assert payload["keywords"] == ["RAG", "LLM"]

    def test_short_topic_rejected(self) -> None:
        from app.mcp.tools import create_literature_review

        client = _FakeClient({})

        with pytest.raises(McpToolError, match="topic"):
            create_literature_review(client, topic="短", keywords=["RAG"])

    def test_empty_keywords_rejected(self) -> None:
        from app.mcp.tools import create_literature_review

        client = _FakeClient({})

        with pytest.raises(McpToolError, match="keywords"):
            create_literature_review(client, topic="检索增强生成综述", keywords=[])


class TestClientFactory:
    def test_client_uses_api_key_header(self) -> None:
        """MCP 客户端必须用 `X-API-Key`（不是 Bearer）。"""
        from app.mcp.client import build_client

        client = build_client("maa_test_key", base_url="http://example.test/api/v1")

        assert isinstance(client, httpx.Client)
        assert client.headers["X-API-Key"] == "maa_test_key"
        assert "Authorization" not in client.headers
        client.close()

    def test_empty_key_rejected(self) -> None:
        from app.mcp.client import build_client

        with pytest.raises(ValueError, match="MAA_API_KEY"):
            build_client("")
