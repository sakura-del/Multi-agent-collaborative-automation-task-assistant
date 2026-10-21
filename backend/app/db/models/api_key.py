"""API_KEY：对外开放 API 与 MCP 的凭证（Phase 2 · 开放能力）。

## 安全设计（三条硬约束）

**① 只存哈希，绝不存明文。**
与密码同一原则：数据库泄漏时明文 Key 直接可用。存 `sha256(明文)` 后，
攻击者拿到也只是一串摘要。

代价：用户忘记 Key 只能重建。这是**正确的取舍**——Key 泄漏的代价
（他人消耗配额、读写用户数据）远高于「重建一次」的不便。

**② 固定前缀 `maa_`，便于泄漏扫描。**
GitHub Secret Scanning / gitleaks 能通过固定前缀识别并告警。行业惯例
（`sk-`、`ghp_`、`AKIA`）。同时 `prefix` 列存明文前几位，让用户在列表里
认出「这是我哪个脚本的 Key」。

**③ 吊销与过期用时间戳（软删除）。**
`revoked_at` / `expires_at` 非空即失效，但**行保留** —— 审计需要回答
「这个 Key 什么时候被谁吊销的」「上周那个 401 是因为吊销还是过期」。

## Scope 粒度（为什么是 4 个）

| Scope | 允许 |
| --- | --- |
| `tasks:read` | 列任务 / 查快照 / 回放事件 |
| `tasks:write` | 建任务 / 确认规划 / 控制（取消、重试） |
| `artifacts:read` | 下载 Markdown/DOCX/`.bib`/`.ris` |
| `annotations:write` | 写批注（导师脚本场景） |

**不只给「全权」**：只读 Key 是最常见的集成需求（把任务状态同步到自己的看板），
给全权等于过度授权。
**不再细**：再细（如 `tasks:cancel` 单独一个）会让用户面对 10 个勾选框，
实际收益极低。

## 配额与限流口径

- **配额按 `user_id` 扣**（Key 归属人）—— 与既有口径一致，不改配额模型；
- **限流按 Key**（`key:{prefix}` 维度），与 IP 维度**同时生效**（不是二选一）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: 合法 scope 全集（**契约**：端点声明的 scope 必须在这里，拼错会让鉴权静默失效）。
API_KEY_SCOPES: tuple[str, ...] = (
    "tasks:read",
    "tasks:write",
    "artifacts:read",
    "annotations:write",
)

#: 新建 Key 的默认 scope（「读 + 写任务」是最常见组合）。
DEFAULT_API_KEY_SCOPES: tuple[str, ...] = ("tasks:read", "tasks:write")

#: 明文 Key 的固定前缀（便于泄漏扫描；行业惯例 `sk-` / `ghp_` / `AKIA`）。
API_KEY_PREFIX = "maa_"

#: `prefix` 列存多少个明文字符（够用户在列表里辨认，又不足以被暴力补齐）。
PREFIX_DISPLAY_LENGTH = 12


class ApiKey(Base):
    """对外开放 API 凭证。

    ⚠️ **绝不添加明文列**（`key` / `plaintext` / `secret` …）——
    `tests/test_api_key_models.py::test_no_plaintext_column` 会立刻失败。
    """

    __tablename__ = "api_key"

    __table_args__ = (UniqueConstraint("key_hash", name="uq_api_key_key_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="Key ID")
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="归属人ID（配额与计费按它）",
    )
    name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="用户可见名称（如「我的脚本」）"
    )
    prefix: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        index=True,
        comment="明文前缀（列表展示用，如 maa_7Kx9mQ2p）",
    )
    key_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="sha256(明文)；绝不存明文"
    )
    scopes: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, comment="权限范围（API_KEY_SCOPES 子集）"
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最后使用时间（判断是否还在用）"
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="过期时间（NULL=永久）"
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="吊销时间（非空即失效，软删除）"
    )
    # created_at / updated_at 由 `Base` 统一提供
