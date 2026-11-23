"""平台模型凭据的**配置态**探测（Issue #25 沉淀项 2，关联 #19）。

## 为什么不发网络请求

`/health/ready` 的既有原则是「LLM 可用性不纳入容器就绪」（配额/网络抖动
不应让容器被判未就绪，见 health.py 文档）。但 #19 的教训是另一类问题：
**功能在、密钥缺**——配置层面就没配，所有平台模型调用都会 401，而健康
检查一切正常。

所以这里只看配置是否就位（不联外网）：
- `ok`：有 key 且不是占位符；
- `missing_credentials`：key 为空；
- `placeholder_credentials`：`your-...` / `<...>` / `changeme` 之类的示例值。

判定为 `ok` 不代表 key 一定有效（那需要真实调用，代价与副作用都不合适），
只代表「不像忘配了」。真实有效性由首次调用自然暴露，且 BYOK 用户可绕开。
"""

from __future__ import annotations

from app.core.config import settings

#: 明显是示例值的 key 前缀/全值
_PLACEHOLDER_PREFIXES = ("your-", "your_", "sk-your", "placeholder")
_PLACEHOLDER_VALUES = {"changeme", "none", "todo", "xxx"}


def platform_llm_status() -> str:
    """平台模型凭据配置状态：`ok` / `missing_credentials` / `placeholder_credentials`。"""
    key = (settings.llm.openai_api_key or "").strip()
    if not key:
        return "missing_credentials"
    lowered = key.lower()
    if lowered.startswith(_PLACEHOLDER_PREFIXES) or lowered in _PLACEHOLDER_VALUES:
        return "placeholder_credentials"
    if lowered.startswith("<") and lowered.endswith(">"):
        return "placeholder_credentials"
    return "ok"
