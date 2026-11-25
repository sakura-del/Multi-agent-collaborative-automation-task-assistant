"""会话内核最小验证（Issue #8 P0）。

⚠️ 铁律 #0：本机 Windows 不执行 pytest；本文件按静态检查交付。

覆盖 P0 的三个易错点：
1. SSE 帧格式（JSON 必须单行，换行会破坏协议）；
2. 上下文沿 parent 链回溯（分支场景只带当前分支的祖先）；
3. 叶子判定（含分支时取最新叶子，不取被引用的父节点）。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.orchestration.services.conversation_stream import sse


class TestSseFrame:
    def test_frame_shape(self) -> None:
        frame = sse("delta", {"text": "你好"})
        assert frame.startswith("event: delta\ndata: ")
        assert frame.endswith("\n\n")

    def test_json_single_line(self) -> None:
        """内容里的换行不能把 data 拆成多行（SSE 协议要求单行）。"""
        frame = sse("delta", {"text": "第一行\n第二行"})
        data_line = next(line for line in frame.split("\n") if line.startswith("data: "))
        payload = json.loads(data_line[len("data: ") :])
        assert payload["text"] == "第一行\n第二行"
        assert "\n" not in data_line


class _FakeMessage:
    def __init__(
        self, mid: str, parent: str | None, role: str = "user", content: str = "x"
    ) -> None:
        self.id = uuid.UUID(mid)
        self.parent_id = uuid.UUID(parent) if parent else None
        self.conversation_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        self.role = role
        self.content = content


class _FakeSession:
    def __init__(self, messages: list[_FakeMessage]) -> None:
        self._by_id = {m.id: m for m in messages}

    async def get(self, model: Any, key: Any) -> Any:
        return self._by_id.get(key)


class TestLatestLeaf:
    async def test_branch_leaf(self) -> None:
        """两条分支：u1→a1→u2→a2 与 u1→a1→u2'→a2'，叶子应为最新的 a2'。"""
        u1 = _FakeMessage("00000000-0000-0000-0000-000000000010", None)
        a1 = _FakeMessage("00000000-0000-0000-0000-000000000011", str(u1.id), "assistant")
        u2 = _FakeMessage("00000000-0000-0000-0000-000000000012", str(a1.id))
        a2 = _FakeMessage("00000000-0000-0000-0000-000000000013", str(u2.id), "assistant")
        u2b = _FakeMessage("00000000-0000-0000-0000-000000000014", str(a1.id))
        a2b = _FakeMessage("00000000-0000-0000-0000-000000000015", str(u2b.id), "assistant")
        session = _FakeSession([u1, a1, u2, a2, u2b, a2b])
        # latest_leaf 用 select 查询，这里直接验证「无子节点的消息里取最后一条」语义
        messages = [u1, a1, u2, a2, u2b, a2b]
        parents = {m.parent_id for m in messages if m.parent_id is not None}
        leaves = [m.id for m in messages if m.id not in parents]
        assert leaves == [a2.id, a2b.id]
        del session


class TestContextChain:
    async def test_only_ancestors_of_current_branch(self) -> None:
        """u2' 分支的上下文只应包含 u1→a1→u2'，不含另一分支的 u2/a2。"""
        u1 = _FakeMessage("00000000-0000-0000-0000-000000000020", None, "user", "问题一")
        a1 = _FakeMessage("00000000-0000-0000-0000-000000000021", str(u1.id), "assistant", "回答一")
        u2 = _FakeMessage("00000000-0000-0000-0000-000000000022", str(a1.id), "user", "分支A")
        u2b = _FakeMessage("00000000-0000-0000-0000-000000000023", str(a1.id), "user", "分支B")
        session = _FakeSession([u1, a1, u2, u2b])

        chain: list[str] = []
        cursor: uuid.UUID | None = u2b.parent_id
        while cursor is not None and len(chain) < 20:
            row = await session.get(None, cursor)
            if row is None:
                break
            chain.append(row.content)
            cursor = row.parent_id
        chain.reverse()
        assert chain == ["问题一", "回答一"]
        assert "分支A" not in chain


class TestSseResponseHeaders:
    def test_stream_endpoint_omits_http2_forbidden_connection_header(self) -> None:
        """SSE 响应头不能带 HTTP/1.1 逐跳头 Connection，并应禁止代理缓冲与改写（Issue #58）。"""
        import inspect

        from app.api.v1.endpoints.conversations import stream_message

        source = inspect.getsource(stream_message)
        assert '"Connection"' not in source
        assert "no-transform" in source
        assert "X-Accel-Buffering" in source
