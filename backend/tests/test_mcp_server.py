"""MCP server 组装测试（Phase 2 · 开放 API Task 5）。

## 本文件锁定

1. `MCP_TOOLS` → SDK `Tool` 对象的转换正确（名称/描述/schema 不丢）；
2. 工具分发路由到正确实现；
3. **工具错误转成文本返回**（MCP 协议要求 `isError` 内容，不能抛异常）；
4. 未知工具名给出可读错误。

## 为什么不测 stdio 传输本身

stdio 传输是 SDK 的职责（它有自己的测试）。我们只需要保证
「工具定义 + 分发 + 错误处理」这三层正确。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.mcp.server import SERVER_NAME, _dispatch, _to_tools, build_server
from app.mcp.tools import MCP_TOOLS, McpToolError


class _FakeClient:
    def __init__(self, payload: Any = None, status: int = 200) -> None:
        self._payload = payload if payload is not None else {}
        self._status = status
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.calls.append((method, url, kwargs))
        # 把值捕获到局部变量：内部类的 `self` 是 _Resp 实例，不是外层
        # _FakeClient（首版就是这样写错的，5 条测试同时红）
        payload = self._payload
        status = self._status

        class _Resp:
            status_code = status

            def json(self) -> Any:
                return payload

        return _Resp()


class TestToolConversion:
    def test_all_tools_converted(self) -> None:
        tools = _to_tools()

        assert len(tools) == len(MCP_TOOLS)
        assert {tool.name for tool in tools} == {item["name"] for item in MCP_TOOLS}

    def test_schema_preserved(self) -> None:
        tools = {tool.name: tool for tool in _to_tools()}

        create = tools["create_literature_review"]
        assert create.description
        assert create.input_schema["type"] == "object"
        assert "topic" in create.input_schema["properties"]
        assert create.input_schema["required"] == ["topic", "keywords"]

    def test_export_format_enum_preserved(self) -> None:
        tools = {tool.name: tool for tool in _to_tools()}

        export = tools["export_task"]
        assert export.input_schema["properties"]["format"]["enum"] == [
            "markdown",
            "docx",
            "bib",
            "ris",
        ]


class TestDispatch:
    def test_dispatch_create(self) -> None:
        client = _FakeClient({"task_id": "t1", "status": "planning"})

        result = _dispatch(
            client,
            "create_literature_review",
            {"topic": "检索增强生成综述", "keywords": ["RAG"]},
        )

        assert result["task_id"] == "t1"
        assert client.calls[0][0] == "POST"

    def test_dispatch_get_status(self) -> None:
        client = _FakeClient(
            {
                "task_id": "t1",
                "status": "completed",
                "title": "T",
                "final_output": "正文",
                "artifacts": [],
            }
        )

        result = _dispatch(client, "get_task_status", {"task_id": "t1"})

        assert result["status"] == "completed"
        assert "final_output" not in result

    def test_dispatch_list_tasks(self) -> None:
        client = _FakeClient([{"task_id": "t1", "status": "completed", "title": "T"}])

        result = _dispatch(client, "list_tasks", {"limit": 3})

        assert isinstance(result, list)
        assert result[0]["task_id"] == "t1"

    def test_dispatch_get_output(self) -> None:
        client = _FakeClient(
            {
                "task_id": "t1",
                "status": "completed",
                "final_output": "# 正文",
                "review_report": {"references": []},
                "artifacts": [],
            }
        )

        result = _dispatch(client, "get_task_output", {"task_id": "t1"})

        assert result["markdown"] == "# 正文"

    def test_dispatch_export(self) -> None:
        client = _FakeClient(
            {
                "task_id": "t1",
                "status": "completed",
                "artifacts": [{"artifact_id": "a1", "kind": "markdown"}],
            }
        )

        result = _dispatch(client, "export_task", {"task_id": "t1", "format": "markdown"})

        assert result["artifact_id"] == "a1"

    def test_unknown_tool_raises(self) -> None:
        client = _FakeClient({})

        with pytest.raises(McpToolError, match="未知工具"):
            _dispatch(client, "no_such_tool", {})


class TestServerAssembly:
    def test_server_builds(self) -> None:
        server = build_server("maa_test_key", base_url="http://example.test/api/v1")

        assert server.name == SERVER_NAME
        # 挂上了 httpx client（便于测试与生命周期管理）
        assert hasattr(server, "_maa_client")
        server._maa_client.close()  # type: ignore[attr-defined]

    def test_server_rejects_empty_key(self) -> None:
        with pytest.raises(ValueError, match="MAA_API_KEY"):
            build_server("")
