"""Repository 层：数据访问抽象。

阶段1任务5：基础 CRUD Repository 模式。

设计原则：
- BaseRepository 提供泛型 CRUD，子类按需扩展专属查询
- 不在 Repository 层 commit/rollback，事务边界由 service 层控制
- 所有方法异步，接收 AsyncSession（通过依赖注入获取）
- 查询强制 limit，避免全表扫描导致内存溢出
"""

from __future__ import annotations

from app.db.repositories.base import BaseRepository
from app.db.repositories.user import UserRepository

__all__ = ["BaseRepository", "UserRepository"]
