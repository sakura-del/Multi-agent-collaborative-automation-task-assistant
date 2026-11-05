"""MCP server 实现（Phase 2 · 开放 API Task 5）。

用**官方 MCP Python SDK**（`mcp>=2.0`），不自研协议 —— 协议细节（JSON-RPC 帧、
能力协商、SSE 重连）自己实现只会引入 bug 且跟不上规范演进。

## SDK 2.x 的 API 形态（与 1.x 不同，容易踩）

1.x 用装饰器：

```python
@server.list_tools()
async def _list_tools(): ...
```

2.x 改成**构造函数注入 handler**：

```python
server = Server(name, on_list_tools=..., on_call_tool=...)
```

handler 签名也变了：`(ServerRequestContext, Params) -> Result`，
返回 `ListToolsResult` / `CallToolResult` 而不是裸 list。
工具 schema 字段是 **`input_schema`**（snake_case），`inputSchema` 只是 alias。

## 两种传输

| 传输 | 入口 | 用途 |
| --- | --- | --- |
| **stdio** | `python -m app.mcp.server` | 本地工具（Claude Desktop / Cursor / Continue） |
| **HTTP** | `streamable_http_app()` | 机构部署（一个地址所有人用） |

## stdio 用法

```bash
export MAA_API_KEY=maa_xxxx
export MAA_API_BASE=http://127.0.0.1:48080/api/v1
python -m app.mcp.server
```

## 为什么 MCP 不直连数据库

见 `app/mcp/client.py`：权限/限流自动生效、可独立部署、
不成为绕过配额的后门。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from mcp import types
from mcp.server import Server

from app.core.logging import get_logger
from app.mcp.client import build_client
from app.mcp.tools import (
    MCP_TOOLS,
    McpToolError,
    create_literature_review,
    export_task,
    get_task_output,
    get_task_status,
    list_tasks,
)

logger = get_logger(__name__)

#: server 名称（客户端里显示的名字）。
SERVER_NAME = "veripaper"

#: server 版本（MCP 客户端握手时展示）。
SERVER_VERSION = "1.0.0"


def _to_tools() -> list[types.Tool]:
    """把 `MCP_TOOLS`（JSON Schema 字典）转成 SDK 的 `Tool` 对象。

    ⚠️ SDK 2.x 字段名是 `input_schema`（snake_case）。
    传 `inputSchema=` 也能构造（pydantic alias），但**读属性**必须用 snake_case。
    """
    return [
        types.Tool(
            name=item["name"],
            description=item["description"],
            input_schema=item["inputSchema"],
        )
        for item in MCP_TOOLS
    ]


def _dispatch(
    client: httpx.Client, name: str, arguments: dict[str, Any]
) -> dict[str, Any] | list[dict[str, Any]]:
    """按工具名分发。"""
    if name == "create_literature_review":
        return create_literature_review(
            client,
            topic=str(arguments.get("topic", "")),
            keywords=list(arguments.get("keywords") or []),
            max_documents=arguments.get("max_documents"),
            citation_style=arguments.get("citation_style"),
        )
    if name == "get_task_status":
        return get_task_status(client, str(arguments.get("task_id", "")))
    if name == "list_tasks":
        return list_tasks(
            client,
            limit=int(arguments.get("limit") or 20),
            status=arguments.get("status"),
        )
    if name == "get_task_output":
        return get_task_output(client, str(arguments.get("task_id", "")))
    if name == "export_task":
        return export_task(
            client,
            str(arguments.get("task_id", "")),
            format=str(arguments.get("format", "")),
        )
    raise McpToolError(f"未知工具：{name}")


def build_server(api_key: str, *, base_url: str | None = None) -> Server:
    """构造 MCP server（注入 Key）。

    每次工具调用复用同一个 httpx 客户端（连接池），在 server 生命周期结束时关闭。
    """
    client = build_client(api_key, base_url=base_url)

    async def _on_list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=_to_tools())

    async def _on_call_tool(
        _context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        """执行工具。

        **异常必须转成 `is_error` 结果返回**，不能向上抛：MCP 协议里工具错误
        应作为内容返回，抛异常会让客户端看到协议层错误而不是业务提示。
        """
        name = params.name
        arguments = dict(params.arguments or {})
        try:
            result = await asyncio.to_thread(_dispatch, client, name, arguments)
        except McpToolError as exc:
            logger.warning("mcp_tool_error", tool=name, error=str(exc))
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"错误：{exc}")],
                is_error=True,
            )
        except Exception as exc:  # pragma: no cover - 防御性兜底
            logger.exception("mcp_tool_unexpected", tool=name)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"内部错误：{exc}")],
                is_error=True,
            )

        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False, indent=2),
                )
            ],
            is_error=False,
        )

    server: Server = Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        on_list_tools=_on_list_tools,
        on_call_tool=_on_call_tool,
    )
    # 挂上 client 便于测试与生命周期管理
    server._maa_client = client  # type: ignore[attr-defined]
    return server


async def run_stdio(api_key: str, *, base_url: str | None = None) -> None:
    """stdio 模式（本地工具）。"""
    from mcp.server.stdio import stdio_server

    server = build_server(api_key, base_url=base_url)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        server._maa_client.close()  # type: ignore[attr-defined]


def streamable_http_app(api_key: str, *, base_url: str | None = None) -> Any:
    """HTTP 模式（可挂在既有 FastAPI 上，路径 `/mcp`）。

    注意：当前实现用**服务启动时的 Key**（单租户）。多租户需要按请求解析
    `X-API-Key` 并构造 per-request client —— 那要求 MCP SDK 支持请求级
    上下文注入，属于后续迭代（已在 runbook 记录）。
    """
    server = build_server(api_key, base_url=base_url)
    return server.streamable_http_app()


def main() -> None:
    """stdio 入口（`python -m app.mcp.server`）。"""
    import os

    from app.mcp.client import API_BASE_ENV, API_KEY_ENV

    api_key = os.environ.get(API_KEY_ENV, "")
    if not api_key:
        raise SystemExit(
            f"缺少环境变量 {API_KEY_ENV}。请在网页「API Key」页生成后设置：\n"
            f"  export {API_KEY_ENV}=maa_xxxx"
        )
    base_url = os.environ.get(API_BASE_ENV)
    logger.info("mcp_server_starting", transport="stdio", base_url=base_url or "(default)")
    asyncio.run(run_stdio(api_key, base_url=base_url))


if __name__ == "__main__":
    main()
