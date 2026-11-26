"""任务创建草稿持久化服务（Issue #68）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.task_draft import TaskDraft
from app.schemas.task_draft import TaskDraftOut, TaskDraftPut


class DraftConflictError(Exception):
    """客户端 base_revision 落后于云端版本，携带最新云端草稿供前端决策。"""

    def __init__(self, current: TaskDraftOut) -> None:
        super().__init__("云端草稿已有更新版本")
        self.current = current


def _to_out(row: TaskDraft) -> TaskDraftOut:
    return TaskDraftOut(
        payload=dict(row.payload or {}),
        revision=row.revision,
        updated_at=row.updated_at,
    )


class TaskDraftService:
    async def get(self, db: AsyncSession, user_id: UUID) -> TaskDraftOut | None:
        result = await db.execute(select(TaskDraft).where(TaskDraft.user_id == user_id))
        row = result.scalar_one_or_none()
        return _to_out(row) if row is not None else None

    async def upsert(self, db: AsyncSession, user_id: UUID, req: TaskDraftPut) -> TaskDraftOut:
        result = await db.execute(
            select(TaskDraft).where(TaskDraft.user_id == user_id).with_for_update()
        )
        row = result.scalar_one_or_none()
        clean_payload: dict[str, Any] = req.payload.model_dump(exclude_none=True)
        now = datetime.now(UTC)

        if row is None:
            row = TaskDraft(
                user_id=user_id,
                payload=clean_payload,
                revision=1,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                existing_result = await db.execute(
                    select(TaskDraft).where(TaskDraft.user_id == user_id).with_for_update()
                )
                existing_row = existing_result.scalar_one_or_none()
                if existing_row is None:
                    raise exc
                if req.base_revision is not None and existing_row.revision != req.base_revision:
                    raise DraftConflictError(_to_out(existing_row)) from exc
                existing_row.payload = clean_payload
                existing_row.revision += 1
                existing_row.updated_at = now
                await db.commit()
                await db.refresh(existing_row)
                return _to_out(existing_row)
            await db.refresh(row)
            return _to_out(row)

        if req.base_revision is not None and row.revision != req.base_revision:
            raise DraftConflictError(_to_out(row))
        row.payload = clean_payload
        row.revision += 1
        row.updated_at = now
        await db.commit()
        await db.refresh(row)
        return _to_out(row)

    async def delete(self, db: AsyncSession, user_id: UUID) -> None:
        await db.execute(delete(TaskDraft).where(TaskDraft.user_id == user_id))
        await db.commit()
