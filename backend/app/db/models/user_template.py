"""USER_TEMPLATE 表：用户上传的 Word 模板（Phase 1 · P1-A 接线）。

语义：
- 每个模板归属一个用户（`user_id`），owner-scoped 访问；跨用户统一 404；
- `template_id` 由源文件内容 sha256 派生（`tpl-<前 16 位>`），同一内容稳定不变；
- 文件本体落盘（`{storage_root}/{user_id}/{template_id}.docx`），DB 只存元数据；
- `placeholders` / `style_map` 以 JSON 存（结构见 `app.tools.templates.models`）。

约束：
- `UniqueConstraint("user_id", "template_id")`：同一用户内 template_id 唯一
  （同内容重复上传视为同一模板）。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserTemplate(Base):
    """用户上传的 Word 模板元数据。"""

    __tablename__ = "user_template"

    __table_args__ = (
        UniqueConstraint("user_id", "template_id", name="uq_user_template_user_template_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="记录ID")
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="所有者ID",
    )

    template_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, comment="模板标识（由内容 sha256 派生）"
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, comment="用户可见名称")
    checksum: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="源 docx 字节的 sha256（完整性/去重）"
    )
    placeholders: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list, comment="占位符契约（PlaceholderField 列表）"
    )
    style_map: Mapped[dict[str, str]] = mapped_column(
        JSON, nullable=False, default=dict, comment="Word styleId → 语义角色"
    )
    size: Mapped[int] = mapped_column(nullable=False, comment="源文件字节数")
