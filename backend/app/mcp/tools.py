"""MCP 工具实现（Phase 2 · 开放 API Task 5）。

## 五个工具覆盖完整闭环

| 工具 | 闭环环节 |
| --- | --- |
| `create_literature_review` | 发起 |
| `get_task_status` | 查状态（**只返摘要**） |
| `list_tasks` | 找任务 |
| `get_task_output` | 取产出（Markdown 全文） |
| `export_task` | 下载其他格式 |

**为什么只有 5 个**：MCP 客户端通常把全部工具描述塞进模型上下文，
工具太多会让模型选择困难。这 5 个已覆盖「发起 → 查 → 取 → 导出」。

## `get_task_status` 为什么只返摘要

任务产出可能是几千字的综述。若状态工具返回全文，一次「查状态」就吃掉
模型大半个上下文窗口，后续对话全被挤掉。
需要全文时用 `get_task_output`（工具名明确表达了「要内容」）。

## 错误映射

HTTP 状态码 → 人类可读的 `McpToolError`。模型能读懂「Key 无效」并提示用户
去检查配置，但读不懂 `HTTPStatusError: 401`。
"""

from __future__ import annotations

from typing import Any

import httpx

#: 工具清单（MCP `tools/list` 的返回）。
MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "create_literature_review",
        "description": (
            "发起一次文献综述任务。返回 task_id，任务在后台异步执行"
            "（通常几分钟）。用 get_task_status 查进度。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "minLength": 5,
                    "maxLength": 200,
                    "description": "综述主题",
                },
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 10,
                    "description": "检索关键词",
                },
                "max_documents": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "最多纳入文献数（默认 20）",
                },
                "citation_style": {
                    "type": "string",
                    "description": "引用样式（默认 gbt7714，即 GB/T 7714—2025）",
                },
            },
            "required": ["topic", "keywords"],
        },
    },
    {
        "name": "get_task_status",
        "description": ("查询任务状态与摘要（**不含正文**）。需要正文时用 get_task_output。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 ID"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "list_tasks",
        "description": "列出最近的任务（按创建时间倒序）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "返回条数（默认 20）",
                },
            },
        },
    },
    {
        "name": "get_task_output",
        "description": "获取任务的综述正文（Markdown 全文）与引用数量。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 ID"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "export_task",
        "description": (
            "导出任务产出。format=markdown/docx 返回 artifact 下载路径；"
            "format=bib/ris 返回参考文献文件路径。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 ID"},
                "format": {
                    "type": "string",
                    "enum": ["markdown", "docx", "bib", "ris"],
                    "description": "导出格式",
                },
            },
            "required": ["task_id", "format"],
        },
    },
]

#: 支持的导出格式。
EXPORT_FORMATS: tuple[str, ...] = ("markdown", "docx", "bib", "ris")


class McpToolError(Exception):
    """工具执行失败（消息面向模型/用户，必须可读）。"""


def _check(response: Any, *, action: str) -> Any:
    """把 HTTP 错误映射成可读的 `McpToolError`。"""
    status = response.status_code
    if 200 <= status < 300:
        return response.json()

    # 尽量取后端返回的 message（比状态码有用得多）
    message = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            message = str(payload.get("message") or payload.get("detail") or "")
    except Exception:
        message = ""

    if status == 401:
        raise McpToolError(
            "API Key 无效或已失效：请在网页「API Key」页确认 Key 未吊销/未过期，"
            f"并检查环境变量（{action}）"
        )
    if status == 403:
        raise McpToolError(f"权限不足：当前 API Key 缺少该操作所需的 scope（{message or action}）")
    if status == 404:
        raise McpToolError(f"任务不存在（{action}）")
    if status == 422:
        raise McpToolError(f"参数不合法：{message or '请检查输入'}")
    if status >= 500:
        raise McpToolError(f"服务端错误（HTTP {status}）：请稍后重试（{action}）")
    raise McpToolError(f"请求失败（HTTP {status}）：{message or action}")


def _task_snapshot(client: httpx.Client, task_id: str) -> dict[str, Any]:
    response = client.request("GET", f"/tasks/{task_id}")
    payload = _check(response, action=f"查询任务 {task_id}")
    if not isinstance(payload, dict):  # pragma: no cover - 契约保证
        raise McpToolError("任务响应格式异常")
    return payload


# ===== 工具实现 =====


def create_literature_review(
    client: httpx.Client,
    *,
    topic: str,
    keywords: list[str],
    max_documents: int | None = None,
    citation_style: str | None = None,
) -> dict[str, Any]:
    """发起综述任务（异步）。"""
    clean_topic = (topic or "").strip()
    if len(clean_topic) < 5:
        raise McpToolError("topic 太短（至少 5 个字符）")
    clean_keywords = [item.strip() for item in (keywords or []) if item and item.strip()]
    if not clean_keywords:
        raise McpToolError("keywords 不能为空（至少一个关键词）")

    payload: dict[str, Any] = {"topic": clean_topic, "keywords": clean_keywords}
    if max_documents is not None:
        payload["max_documents"] = int(max_documents)
    if citation_style:
        payload["citation_style"] = citation_style

    response = client.request("POST", "/tasks", json=payload)
    result = _check(response, action="创建任务")
    return {
        "task_id": result.get("task_id"),
        "status": result.get("status"),
        "hint": "任务已提交，用 get_task_status 查进度（通常几分钟）",
    }


def get_task_status(client: httpx.Client, task_id: str) -> dict[str, Any]:
    """查状态 + 摘要（**不含正文**，避免撑爆上下文）。"""
    snapshot = _task_snapshot(client, task_id)
    artifacts = snapshot.get("artifacts") or []
    return {
        "task_id": snapshot.get("task_id"),
        "status": snapshot.get("status"),
        "title": snapshot.get("title"),
        "plan_version": snapshot.get("plan_version"),
        "revision": snapshot.get("revision"),
        "has_output": bool(snapshot.get("final_output")),
        "artifact_count": len(artifacts),
        "hint": (
            "有产出，可用 get_task_output 取正文，或 export_task 导出其他格式"
            if snapshot.get("final_output")
            else "尚无产出"
        ),
    }


def list_tasks(
    client: httpx.Client, *, limit: int = 20, status: str | None = None
) -> list[dict[str, Any]]:
    """列出最近任务（紧凑字段，不返正文）。"""
    safe_limit = max(1, min(int(limit or 20), 100))
    response = client.request("GET", "/tasks", params={"limit": safe_limit})
    payload = _check(response, action="列出任务")
    if not isinstance(payload, list):  # pragma: no cover - 契约保证
        raise McpToolError("任务列表格式异常")

    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if status and item.get("status") != status:
            continue
        rows.append(
            {
                "task_id": item.get("task_id"),
                "status": item.get("status"),
                "title": item.get("title"),
                "created_at": item.get("created_at"),
            }
        )
    return rows


def get_task_output(client: httpx.Client, task_id: str) -> dict[str, Any]:
    """取综述正文（Markdown 全文）+ 引用数量。"""
    snapshot = _task_snapshot(client, task_id)
    report = snapshot.get("review_report") or {}
    references = report.get("references") if isinstance(report, dict) else None
    artifacts = snapshot.get("artifacts") or []
    return {
        "task_id": snapshot.get("task_id"),
        "status": snapshot.get("status"),
        "markdown": snapshot.get("final_output"),
        "reference_count": len(references) if isinstance(references, list) else 0,
        "artifact_count": len(artifacts),
    }


def export_task(client: httpx.Client, task_id: str, *, format: str) -> dict[str, Any]:
    """导出产出。

    - `markdown` / `docx`：从 `artifacts` 里按 `kind` 找对应 artifact，返回下载路径；
    - `bib` / `ris`：走参考文献端点（与 artifact 无关）。
    """
    if format not in EXPORT_FORMATS:
        raise McpToolError(f"不支持的 format：{format}（可选：{', '.join(EXPORT_FORMATS)}）")

    if format in ("bib", "ris"):
        return {
            "task_id": task_id,
            "format": format,
            "download_path": f"/tasks/{task_id}/references.{format}",
            "hint": "用 GET 该路径并带 X-API-Key 头即可下载",
        }

    snapshot = _task_snapshot(client, task_id)
    artifacts = snapshot.get("artifacts") or []
    target = next(
        (item for item in artifacts if isinstance(item, dict) and item.get("kind") == format),
        None,
    )
    if target is None:
        available: list[str] = [
            str(item.get("kind"))
            for item in artifacts
            if isinstance(item, dict) and item.get("kind")
        ]
        raise McpToolError(
            f"该任务没有 {format} 产出"
            + (f"（现有：{', '.join(available)}）" if available else "（任务可能尚未完成）")
        )
    artifact_id = target.get("artifact_id")
    return {
        "task_id": task_id,
        "format": format,
        "artifact_id": artifact_id,
        "download_path": f"/tasks/{task_id}/artifacts/{artifact_id}",
        "hint": "用 GET 该路径并带 X-API-Key 头即可下载",
    }
