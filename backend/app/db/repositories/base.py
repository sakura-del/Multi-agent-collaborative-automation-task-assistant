"""Repository 泛型基类：基础 CRUD。

阶段1任务5：实现基础 CRUD Repository 模式。

设计要点：
- Generic[ModelT]：子类指定具体 ORM 模型类型
- 异步：所有方法 async，适配 SQLAlchemy 2.0 asyncio
- 事务边界：本层只 add/flush/delete，不 commit；commit 由 service 层统一控制
- 内存安全：get_multi 强制 limit（默认 100，上限 1000），避免一次性加载大结果集
- 软删除：delete 为物理删除；如需软删除由子类覆盖
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

# 泛型类型变量：约束为 ORM 模型（Base 子类）
ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """泛型 Repository 基类，提供标准 CRUD 操作。

    子类需设置 model 类属性：
        class UserRepository(BaseRepository[User]):
            model = User
    """

    # 子类必须覆盖：具体 ORM 模型类
    model: type[ModelT]

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, id_: Any) -> ModelT | None:
        """按主键查询单条记录，不存在返回 None。"""
        return await self.db.get(self.model, id_)

    async def get_multi(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> list[ModelT]:
        """分页查询多条记录。

        limit 上限 1000，防止一次性加载过多数据导致内存压力。
        """
        # 防御性裁剪：limit 超过 1000 时强制截断
        safe_limit = max(1, min(limit, 1000))
        stmt = select(self.model).offset(skip).limit(safe_limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create(self, obj: ModelT) -> ModelT:
        """新增记录。

        仅 add + flush（获取主键），不 commit。
        service 层确认业务逻辑无误后统一 commit。
        """
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def update(self, obj: ModelT) -> ModelT:
        """更新记录。

        SQLAlchemy 2.0 的 UnitOfWork 模式：对象属性变更后 flush 即可持久化。
        此方法主要语义化标注更新操作，并刷新得到最新值。
        """
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        """物理删除记录。

        注意：此为硬删除。如需软删除，子类应覆盖此方法并设置 is_deleted 字段。
        """
        await self.db.delete(obj)
        await self.db.flush()

    async def count(self) -> int:
        """统计总记录数。用于分页与监控。"""
        from sqlalchemy import func

        stmt = select(func.count()).select_from(self.model)
        result = await self.db.execute(stmt)
        return int(result.scalar_one())
