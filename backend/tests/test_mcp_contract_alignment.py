"""MCP 工具与真实 API 契约的对齐测试（2026-10-23 巡检发现）。

## 为什么需要这条测试

`tests/test_mcp_tools.py` 用**手写 dict 替身**模拟 `/tasks/{id}` 响应：

```python
{"task_id": "t1", "status": "completed", "final_output": "很长的正文" * 500}
```

替身里有 `final_output`，**但真实 API 的响应契约（`TaskSnapshot`）当时没有这个字段**。
于是：

- 单测全绿（测的是替身）；
- 真实调用 `get_task_output` 永远返回 `markdown: null`；
- `get_task_status` 永远提示「尚无产出」。

这是本项目**第二次**踩「假测试掩盖真实缺陷」（第一次是前端 `flushPromises()`）。

## 本文件的做法

不mock响应体，而是**用真实的 `TaskSnapshot` 契约**生成响应 payload，再喂给 MCP 工具。
这样一旦契约丢了字段，测试立刻变红。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.mcp.tools import get_task_output, get_task_status
from app.orchestration.contracts import TaskSnapshot


class _ContractClient:
    """按真实 `TaskSnapshot` 契约返回响应的假 client。

    **关键**：payload 由 `TaskSnapshot.model_dump(mode="json")` 生成，
    而不是手写 dict —— 手写 dict 正是当初假测试的成因。
    """

    def __init__(self, snapshot: TaskSnapshot) -> None:
        self._payload = snapshot.model_dump(mode="json")

    def request(self, method: str, url: str, **kwargs: Any) -> Any:

        class _Response:
            status_code = 200

            def json(self) -> dict[str, Any]:
                return json.loads(json.dumps(self_payload))

        self_payload = self._payload
        return _Response()


def _snapshot(**overrides: Any) -> TaskSnapshot:
    base: dict[str, Any] = {
        "task_id": uuid.uuid4(),
        "status": "completed",
        "title": "RAG 综述",
        "plan_version": 1,
        "revision": 2,
        "artifacts": [{"artifact_id": "a1", "kind": "markdown"}],
    }
    base.update(overrides)
    return TaskSnapshot(**base)


class TestGetTaskOutputOnRealContract:
    def test_returns_markdown_from_snapshot(self) -> None:
        """**核心断言**：真实契约里有正文时，MCP 必须能取到。"""
        body = "# 综述\n\n这是正文内容。"
        client = _ContractClient(_snapshot(final_output=body))

        result = get_task_output(client, "t1")  # type: ignore[arg-type]

        assert result["markdown"] == body, (
            "MCP 取不到正文 —— 说明 TaskSnapshot 契约里没有 final_output，"
            "或 _build_snapshot 没传它"
        )

    def test_no_output_returns_none(self) -> None:
        """还没产出时是 `None`（不是空串，工具靠它判断「尚无产出」）。"""
        client = _ContractClient(_snapshot(status="running", final_output=None))

        result = get_task_output(client, "t1")  # type: ignore[arg-type]

        assert result["markdown"] is None


class TestGetTaskStatusOnRealContract:
    def test_has_output_true_when_body_present(self) -> None:
        client = _ContractClient(_snapshot(final_output="# 正文"))

        result = get_task_status(client, "t1")  # type: ignore[arg-type]

        assert result["has_output"] is True, (
            "has_output 为 False 说明快照里读不到 final_output —— "
            "模型会被告知「尚无产出」，然后反复轮询"
        )

    def test_has_output_false_when_no_body(self) -> None:
        client = _ContractClient(_snapshot(status="running", final_output=None))

        result = get_task_status(client, "t1")  # type: ignore[arg-type]

        assert result["has_output"] is False

    def test_status_tool_does_not_leak_full_text(self) -> None:
        """状态工具返回的字段里不能有正文（避免撑爆上下文）—— 即便契约里有。"""
        client = _ContractClient(_snapshot(final_output="很长的正文" * 500))

        result = get_task_status(client, "t1")  # type: ignore[arg-type]

        assert "final_output" not in result
        assert "markdown" not in result


@pytest.mark.parametrize("field", ["task_id", "status", "title"])
def test_snapshot_contract_keeps_core_fields(field: str) -> None:
    """顺带锁住 MCP 依赖的其它字段（防止未来重构改字段名）。"""
    payload = _snapshot().model_dump(mode="json")
    assert field in payload
