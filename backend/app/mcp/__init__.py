"""MCP server（Phase 2 · 开放 API Task 5）。

MCP = Model Context Protocol。让 Claude Desktop / Cursor / Continue 等客户端
直接调用本服务的能力（发起综述、查状态、取产出）。

## 两种传输

| 传输 | 入口 | 用途 |
| --- | --- | --- |
| **stdio** | `python -m app.mcp.server` | 本地工具（Claude Desktop / Cursor） |
| **HTTP/SSE** | `POST /mcp`（挂在 FastAPI 上） | 机构部署（一个地址所有人用） |

两种共用同一套 `MCP_TOOLS` 定义与 `app/mcp/tools.py` 实现。

## stdio 模式用法

```bash
export MAA_API_KEY=maa_xxxx
export MAA_API_BASE=http://127.0.0.1:48080/api/v1
python -m app.mcp.server
```

Claude Desktop 配置（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "maa": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "env": {"MAA_API_KEY": "maa_xxxx", "MAA_API_BASE": "http://..."}
    }
  }
}
```
"""

from __future__ import annotations

__all__: list[str] = []
