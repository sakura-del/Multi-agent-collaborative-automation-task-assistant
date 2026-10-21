"""MCP 客户端（Phase 2 · 开放 API Task 5）。

## 为什么 MCP server 不直连数据库

它是**客户端**，调开放 API：

1. **权限与限流自动生效**：不用在 MCP 层重写 scope 校验与速率限制；
2. **可独立部署**：甚至能跑在用户机器上（`pip install` 后配一个 Key）；
3. **避免后门**：直连数据库的 MCP 会绕过配额与审计。

代价是多一跳 HTTP。对「发起一次综述要跑几分钟」的场景，这一跳可以忽略。
"""

from __future__ import annotations

import os

import httpx

#: 默认 API 基地址（可用 `MAA_API_BASE` 覆盖）。
DEFAULT_API_BASE = "http://127.0.0.1:48080/api/v1"

#: Key 环境变量名（stdio 模式下从这里读）。
API_KEY_ENV = "MAA_API_KEY"

#: 基地址环境变量名。
API_BASE_ENV = "MAA_API_BASE"

#: 超时：建任务是异步的（立刻返回），查状态也快；但导出可能慢一点。
DEFAULT_TIMEOUT = 30.0


def build_client(api_key: str, *, base_url: str | None = None) -> httpx.Client:
    """构造带 Key 的 httpx 客户端。

    **用 `X-API-Key` 头，不用 Bearer**：两条通道物理隔离（Key 塞 Bearer 会 401）。
    """
    key = (api_key or "").strip()
    if not key:
        raise ValueError(f"缺少 API Key：请设置环境变量 {API_KEY_ENV}（在网页「API Key」页生成）")
    resolved_base = base_url or os.environ.get(API_BASE_ENV, DEFAULT_API_BASE)
    return httpx.Client(
        base_url=resolved_base,
        headers={"X-API-Key": key},
        timeout=DEFAULT_TIMEOUT,
        # 本机环境可能有非法代理配置（见 AGENTS #4），这里显式忽略环境代理
        trust_env=False,
    )


def client_from_env() -> httpx.Client:
    """从环境变量构造客户端（stdio 模式入口用）。"""
    return build_client(os.environ.get(API_KEY_ENV, ""))
