"""降级判定：404（模型不存在）必须触发降级。

## 为什么这是缺陷（2026-10-27 实测）

实测：把主模型名写成不存在的名字，`LLMManager.chat()` **直接抛出 404**，
降级链完全没走：

```
llm_chat_failed attempt=1 error='404 - Model "definitely-not-a-real-model-xyz"
is not supported by any configured account in this group'
❌ 降级也失败: NotFoundError
```

而 `_is_retryable` 的 docstring 写着「参数错误（400/422），重试与降级都无意义」，
把 404 归到这一类。**但这个归类错了**：

| 状态 | 含义 | 该不该降级 |
| --- | --- | --- |
| 401/403 | key 无效 | ❌ 换模型也没用（key 是同一个） |
| 400/422 | 请求体不合法 | ❌ 换模型也一样不合法 |
| **404** | **该模型在这个账号/分组不存在** | ✅ **正是该换一个模型的场景** |
| 429/5xx | 限流/上游故障 | ✅ 已支持 |

主模型下线/改名（404）恰恰是降级链存在的意义。不降级的话，
`model.rhzy.ai` 上次把 `gpt-6-luna` 下架时，整个应用会直接不可用。
"""

from __future__ import annotations

import pytest

from app.llm.base import BaseLLM


class _FakeStatusError(Exception):
    """模拟 openai SDK 的 APIStatusError（带 status_code）。"""

    def __init__(self, status_code: int, message: str = "") -> None:
        super().__init__(message or f"Error code: {status_code}")
        self.status_code = status_code


class TestIsRetryableTriggersFallback:
    def test_404_model_not_found_is_retryable(self) -> None:
        """404 = 该模型不存在 → 必须降级到下一个模型。"""
        assert BaseLLM._is_retryable(_FakeStatusError(404, "model_not_found")) is True

    def test_401_is_still_not_retryable(self) -> None:
        """401 = key 无效 → 换模型也一样失败，不降级（保持既有语义）。"""
        assert BaseLLM._is_retryable(_FakeStatusError(401, "invalid api key")) is False

    def test_403_is_still_not_retryable(self) -> None:
        assert BaseLLM._is_retryable(_FakeStatusError(403, "forbidden")) is False

    def test_400_is_still_not_retryable(self) -> None:
        """400 = 请求体不合法 → 换模型也一样，不降级。"""
        assert BaseLLM._is_retryable(_FakeStatusError(400, "bad request")) is False

    def test_422_is_still_not_retryable(self) -> None:
        assert BaseLLM._is_retryable(_FakeStatusError(422, "unprocessable")) is False

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_transient_statuses_still_retryable(self, status: int) -> None:
        """限流与上游 5xx 仍然可重试/降级（不回归）。"""
        assert BaseLLM._is_retryable(_FakeStatusError(status)) is True

    def test_404_without_status_code_by_class_name(self) -> None:
        """有些 SDK 不带 status_code，只能靠类名/消息识别。"""

        class NotFoundError(Exception):
            pass

        assert BaseLLM._is_retryable(NotFoundError("model not found")) is True

    def test_404_by_message_when_no_status(self) -> None:
        assert BaseLLM._is_retryable(Exception('Model "x" is not supported')) is True
