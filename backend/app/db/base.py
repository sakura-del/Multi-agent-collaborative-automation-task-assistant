"""SQLAlchemy ORM 基类声明。

阶段0仅提供 Declarative Base；13 张表的 ORM 模型将在阶段1任务1.5 中补充。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。

    内置通用字段：
    - created_at / updated_at：所有业务表都需审计时间
    - id 字段由具体子类按业务需要定义（UUID 自增主键策略各表可不同）
    """

    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805
        # 类名转 snake_case，子类可显式覆盖
        name = cls.__name__
        result = [name[0].lower()]
        for ch in name[1:]:
            result.append("_" + ch.lower() if ch.isupper() else ch)
        return "".join(result)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间",
    )
