"""WORKSPACE / WORKSPACE_MEMBER / WORKSPACE_INVITE：团队工作区与席位（Phase 2）。

## 为什么需要这三张表

路线图把「无协作」列为高校采购的硬门槛：当前只有 owner 隔离，无法回答
「一个导师带 5 个学生怎么共用」。`workspace` 是团队容器，`workspace_member`
即**席位**（计费单位），`workspace_invite` 是加入通道。

## 席位口径

- 席位 = `workspace_member` 的一行；
- **owner 也占一个席位**（否则「1 人团队」无法表达，计费会漏算）；
- 上限在 `workspace.seat_limit`（按工作区差异化，不写全局配置——
  「一个团队买 10 席、另一个买 3 席」必须能表达）。

## 为什么邀请码而不是邮件邀请

P1-C 的遗留现状：SMTP 未配齐时注册不阻塞。邀请码可复制粘贴，不依赖邮件通道；
后续配齐 SMTP 再加邮件通知，邀请码作为兜底保留。

邀请码**一次性**（`used_at` 非空即失效）：多人共享一个码会让席位超发，
一次性码 + 加入时事务内校验席位，把风险降到可控。

## 约束（数据层是最后防线）

- `slug` 唯一（进 URL，重复会让路由歧义）；
- `(workspace_id, user_id)` 唯一（同一人不能重复加入，兜住并发接受邀请）；
- `code` 唯一（高熵随机，碰撞概率可忽略，但唯一键兜住实现 bug）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: 合法角色（按权限从高到低）。**顺序有意义**：`ROLE_RANK` 用它做等级比较。
WORKSPACE_ROLES: tuple[str, ...] = ("owner", "admin", "member", "viewer")

#: 角色等级（数字越大权限越高）；用于「不能操作同级或更高权限的人」这类判断。
ROLE_RANK: dict[str, int] = {
    role: len(WORKSPACE_ROLES) - index for index, role in enumerate(WORKSPACE_ROLES)
}

#: 默认席位上限（个人/小团队起步；机构版按购买数覆盖）。
DEFAULT_SEAT_LIMIT = 5


class Workspace(Base):
    """团队工作区（计费与协作的容器）。

    时间戳字段（`created_at` / `updated_at`）由 `Base` 统一提供，**不重复声明**——
    重复声明会让「ORM 有列、迁移没建列」这类不一致更难发现。
    """

    __tablename__ = "workspace"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="工作区ID")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="团队名")
    slug: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True, comment="URL 友好标识（唯一）"
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="创建者ID（不可移除，转让需显式操作）",
    )
    seat_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_SEAT_LIMIT,
        server_default=str(DEFAULT_SEAT_LIMIT),
        comment="席位上限（计费单位；机构版按购买数）",
    )


class WorkspaceMember(Base):
    """工作区成员 = **一个席位**。

    `joined_at` 是业务字段（与 `created_at` 语义不同：转让/重入会更新加入时间，
    但记录创建时间不变），所以两列都保留。
    """

    __tablename__ = "workspace_member"

    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member_workspace_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="记录ID")
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="工作区ID",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="成员用户ID",
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="member", comment="角色：owner/admin/member/viewer"
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="加入时间"
    )


class WorkspaceInvite(Base):
    """一次性邀请码。"""

    __tablename__ = "workspace_invite"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="邀请ID")
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="工作区ID",
    )
    code: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True, comment="一次性邀请码（唯一）"
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="member", comment="受邀者加入后的角色"
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, comment="创建者ID"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="过期时间（默认 7 天）"
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="使用时间（非空即失效）"
    )
    used_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, comment="使用者ID"
    )
