"""LLM_CREDENTIAL：用户自带的模型凭据（Issue #15 · BYOK）。

## 与 `API_KEY` 表的区别（**不要混用**）

| | `api_key` | `llm_credential` |
| --- | --- | --- |
| 方向 | 我们发给用户，用户来调我们 | 用户给我们，我们去调厂商 |
| 存储 | **只存哈希**（校验时比对） | **必须可逆**（要发给厂商）→ AES-256-GCM |
| 明文 | 只在创建响应里出现一次 | 任何响应都不出现，只回显 `api_key_hint` |

## 为什么 `key_version` 单独一列

运行时按 `(credential_id, key_version, model)` 缓存 LLM 客户端。用户换 key 时
把版本 +1，旧实例自然不会被命中 —— 比「遍历缓存删 key」可靠（没有遗漏路径）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: 凭据状态。`invalid` 预留给「连通性自检失败自动禁用」（P1）。
CREDENTIAL_STATUSES: tuple[str, ...] = ("active", "invalid", "disabled")


class LLMCredential(Base):
    """用户自带的模型 API Key。

    ⚠️ **绝不添加明文列**（`api_key` / `plaintext` / `secret` …）——
    `tests/test_llm_credential_endpoints.py::TestModelContract::test_no_plaintext_column`
    会立刻失败。
    """

    __tablename__ = "llm_credential"

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_llm_credential_user_name"),
        # 每用户至多一个默认。应用层会先清空再设置；索引是并发下的最后一道闸
        # （两个请求同时「设默认」时，后者会被 DB 拒绝，而不是留下两个默认）。
        Index(
            "uq_llm_credential_user_default",
            "user_id",
            unique=True,
            postgresql_where=text("is_default"),
            sqlite_where=text("is_default"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="凭据ID")
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="归属用户ID",
    )
    name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="用户可见名称（同用户内唯一）"
    )
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="厂商键：openai/deepseek/.../custom"
    )
    base_url: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="实际调用的端点：预置厂商由服务端写入，custom 由用户提供",
    )
    model: Mapped[str] = mapped_column(String(160), nullable=False, comment="模型名")
    context_window: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="模型上下文窗口（token）；NULL = 用户未声明，界面显示「未设置」",
    )
    max_output_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="单次输出上限（token）；NULL = 不传 max_tokens，沿用厂商默认",
    )

    # ===== 密钥（加密存储）=====
    api_key_ciphertext: Mapped[str] = mapped_column(
        Text, nullable=False, comment="AES-256-GCM 密文（v1:base64）"
    )
    api_key_hint: Mapped[str] = mapped_column(
        String(24), nullable=False, comment="脱敏回显（如 sk-…abcd），不含完整密钥"
    )
    key_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="HMAC-SHA256 前 32 位：去重/审计用，不可逆",
    )
    key_version: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, comment="换 key 时 +1，用于失效运行时缓存"
    )

    # ===== 元数据 =====
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False, comment='能力探测结果：{"json_mode": bool, ...}'
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="是否该用户的默认模型凭据"
    )
    allow_platform_fallback: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
        comment="凭据不可用（401/402/403）时是否允许回退平台模型（Issue #28）",
    )
    status: Mapped[str] = mapped_column(
        String(16), default="active", nullable=False, comment="active/invalid/disabled"
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="连续失败次数（自检或运行时鉴权失败）；达阈值自动置 invalid",
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近一次连通性自检通过时间"
    )
    last_error: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="最近一次自检/调用的可读错误（不含密钥）"
    )

    # Base 已提供 created_at / updated_at


class LLMNodeRouting(Base):
    """节点级模型路由（Issue #22 延伸：混合路由的用户级配置）。

    ## 为什么做成用户配置

    混合路由最初只在平台 `.env` 里（`LLM__NODE_ROUTING` + `LLM__ROUTING_TARGETS`），
    用户看不到也改不了。但「哪个节点用哪把模型」本质是**每个用户的取舍**：
    长输入抽取走本地零成本模型、规划/写作走云端强模型——只有用户自己知道。

    ## 语义

    - 每个 `(user_id, node_type)` 至多一条：该节点固定用 `credential_id` 那把模型；
    - **未配置的节点 = 跟随任务勾选的模型**（创建任务时选的那把），默认行为不变；
    - 凭据删除时级联清理（FK ondelete=CASCADE），不留悬空路由。
    """

    __tablename__ = "llm_node_routing"

    __table_args__ = (
        UniqueConstraint("user_id", "node_type", name="uq_llm_node_routing_user_node"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="路由ID")
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="归属用户ID",
    )
    node_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="节点类型：planner/search/extract/write/validate/review",
    )
    credential_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("llm_credential.id", ondelete="CASCADE"),
        nullable=False,
        comment="该节点固定使用的凭据",
    )

    # Base 已提供 created_at / updated_at
