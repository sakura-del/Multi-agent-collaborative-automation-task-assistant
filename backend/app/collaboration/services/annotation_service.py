"""批注领域服务（Phase 2 · 团队工作区 Task 4）。

## 锚点用段落指纹，不用字符偏移

任务产出会因**重跑**整体变化。字符偏移在重跑后立刻失效，更糟的是会
**静默错挂**——导师的「这段引用缺页码」可能挂到完全无关的文字上。

用 `sha256(text.strip())[:16]` 做指纹：

- 段落没变 → 指纹一致 → 批注正常挂载；
- 段落被改写 → 指纹失配 → 报告「原文已变更」（`matched=False`），**不静默错挂**。

这是有意的取舍：宁可让导师知道「这条批注的原文已改」，也不要让批注出现在
错误的位置上。

## 权限（导师场景优先）

| 操作 | 要求 |
| --- | --- |
| 读批注 | 能看到任务即可（个人任务 owner / 工作区成员） |
| 写批注 | 同上——**viewer 也能写**（导师核心用法就是只看 + 批注） |
| 删自己的批注 | 同上 |
| 删他人的批注 | 工作区 `admin` 及以上 |

**为什么 viewer 能写批注**：把批注权限绑到「能创建任务」会让导师无法工作，
而导师正是付费方（机构版按席位）。这是产品决策，不是实现细节。

## 不删除已解决批注

`resolved_at` 只标记时间，**不删行**：导师需要看到历史意见（「这条我提过吗」）。
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collaboration.services.workspace_service import WorkspaceService
from app.core.logging import get_logger
from app.db.models.annotation import ANNOTATION_ANCHOR_TYPES, TaskAnnotation, paragraph_fingerprint
from app.db.models.task import Task
from app.db.models.workspace import ROLE_RANK

logger = get_logger(__name__)

#: 批注正文长度上限（防止有人贴整篇论文）。
MAX_CONTENT_LENGTH = 4000

#: Markdown 段落切分：连续空行分段。
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


class AnnotationError(Exception):
    """批注通用错误（端点层 400/404）。"""


class AnnotationPermissionError(Exception):
    """批注权限不足（端点层 **403**）。"""


class AnnotationService:
    """批注的读写与锚点匹配。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        workspace_service: WorkspaceService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._workspaces = workspace_service or WorkspaceService(session_factory)

    # ===== 可见性 =====

    async def _load_task_for(self, user_id: uuid.UUID, task_id: uuid.UUID) -> Task:
        """取任务并校验可见性；不可见抛 `AnnotationError`（端点层 404）。

        与任务端点同一口径：非成员/非 owner 一律「不存在」，不泄漏存在性。
        """
        async with self._session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                raise AnnotationError("任务不存在")
            if task.user_id == user_id:
                return task
            if task.workspace_id is not None:
                role = await self._workspaces.get_role(user_id, task.workspace_id)
                if role is not None:
                    return task
            raise AnnotationError("任务不存在")

    # ===== 写 =====

    async def create(
        self,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        *,
        anchor_type: str,
        content: str,
        anchor_text: str | None = None,
        anchor_key: str | None = None,
    ) -> TaskAnnotation:
        """新建批注。

        `anchor_type="paragraph"` 时必须给 `anchor_text`（服务自己算指纹）或
        `anchor_key`（已算好的指纹，供重放/导入用）。**不让前端算指纹**：
        前后端各算一份迟早口径不一致（空白归一化、换行风格）。
        """
        if anchor_type not in ANNOTATION_ANCHOR_TYPES:
            raise AnnotationError(
                f"未知锚点类型：{anchor_type}（可选：{', '.join(ANNOTATION_ANCHOR_TYPES)}）"
            )
        text = (content or "").strip()
        if not text:
            raise AnnotationError("批注内容不能为空")
        if len(text) > MAX_CONTENT_LENGTH:
            raise AnnotationError(f"批注内容过长（上限 {MAX_CONTENT_LENGTH} 字）")

        resolved_key: str | None = None
        if anchor_type == "paragraph":
            if anchor_text is not None:
                resolved_key = paragraph_fingerprint(anchor_text)
            elif anchor_key:
                resolved_key = anchor_key
            else:
                raise AnnotationError("段落批注必须提供段落文本（anchor_text）")
        elif anchor_type == "citation":
            if not anchor_key:
                raise AnnotationError("引用批注必须提供 source_id（anchor_key）")
            resolved_key = anchor_key
        # task 级：anchor_key 保持 None

        task = await self._load_task_for(user_id, task_id)
        async with self._session_factory() as session:
            annotation = TaskAnnotation(
                task_id=task.id,
                author_id=user_id,
                anchor_type=anchor_type,
                anchor_key=resolved_key,
                content=text,
            )
            session.add(annotation)
            await session.commit()
            await session.refresh(annotation)
            logger.info(
                "annotation_created",
                annotation_id=str(annotation.id),
                task_id=str(task.id),
                anchor_type=anchor_type,
            )
            return annotation

    async def update_content(
        self, user_id: uuid.UUID, annotation_id: uuid.UUID, *, content: str
    ) -> TaskAnnotation:
        """改正文（仅作者本人；admin 也不改别人的话——那是篡改）。"""
        text = (content or "").strip()
        if not text:
            raise AnnotationError("批注内容不能为空")
        if len(text) > MAX_CONTENT_LENGTH:
            raise AnnotationError(f"批注内容过长（上限 {MAX_CONTENT_LENGTH} 字）")

        async with self._session_factory() as session:
            annotation = await session.get(TaskAnnotation, annotation_id)
            if annotation is None:
                raise AnnotationError("批注不存在")
            await self._load_task_for(user_id, annotation.task_id)
            if annotation.author_id != user_id:
                raise AnnotationPermissionError("权限不足：只能修改自己的批注")
            annotation.content = text
            await session.commit()
            await session.refresh(annotation)
            return annotation

    async def delete(self, user_id: uuid.UUID, annotation_id: uuid.UUID) -> None:
        """删除批注：本人可删；他人批注需工作区 admin+。"""
        async with self._session_factory() as session:
            annotation = await session.get(TaskAnnotation, annotation_id)
            if annotation is None:
                raise AnnotationError("批注不存在")
            task = await self._load_task_for(user_id, annotation.task_id)

            if annotation.author_id != user_id:
                if task.workspace_id is None:
                    # 个人任务里不存在「管理员」概念
                    raise AnnotationPermissionError("权限不足：只能删除自己的批注")
                actor_role = await self._workspaces.get_role(user_id, task.workspace_id)
                if actor_role is None or ROLE_RANK[actor_role] < ROLE_RANK["admin"]:
                    raise AnnotationPermissionError("权限不足：删除他人批注需要 admin 及以上角色")

            await session.delete(annotation)
            await session.commit()
            logger.info("annotation_deleted", annotation_id=str(annotation_id))

    # ===== 已解决 =====

    async def resolve(self, user_id: uuid.UUID, annotation_id: uuid.UUID) -> TaskAnnotation:
        """标记已解决（幂等：重复标记不改变首次时间）。"""
        async with self._session_factory() as session:
            annotation = await session.get(TaskAnnotation, annotation_id)
            if annotation is None:
                raise AnnotationError("批注不存在")
            await self._load_task_for(user_id, annotation.task_id)
            if annotation.resolved_at is None:
                from datetime import UTC, datetime

                annotation.resolved_at = datetime.now(UTC)
                await session.commit()
                await session.refresh(annotation)
            return annotation

    async def reopen(self, user_id: uuid.UUID, annotation_id: uuid.UUID) -> TaskAnnotation:
        """重新打开（清空 `resolved_at`）。"""
        async with self._session_factory() as session:
            annotation = await session.get(TaskAnnotation, annotation_id)
            if annotation is None:
                raise AnnotationError("批注不存在")
            await self._load_task_for(user_id, annotation.task_id)
            annotation.resolved_at = None
            await session.commit()
            await session.refresh(annotation)
            return annotation

    # ===== 读 =====

    async def list_for_task(self, user_id: uuid.UUID, task_id: uuid.UUID) -> list[TaskAnnotation]:
        """列出任务的全部批注（含已解决），按创建时间正序。

        排序键带 `id`：同事务写入的行 `created_at` 相同（AGENTS #14）。
        """
        task = await self._load_task_for(user_id, task_id)
        async with self._session_factory() as session:
            rows = await session.execute(
                select(TaskAnnotation)
                .where(TaskAnnotation.task_id == task.id)
                .order_by(TaskAnnotation.created_at.asc(), TaskAnnotation.id.asc())
            )
            return list(rows.scalars().all())

    async def match_paragraphs(
        self, user_id: uuid.UUID, task_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        """把段落级批注与当前产出做匹配，返回**每条批注**的匹配状态。

        返回结构（前端直接消费）：

        ```json
        [{"annotation_id": "...", "fingerprint": "...", "content": "...",
          "matched": true, "paragraph_index": 2, "resolved": false}]
        ```

        `matched=False` 表示「原文已变更」——前端必须显示这个状态，
        不能把批注挂到别的段落上。
        """
        task = await self._load_task_for(user_id, task_id)
        paragraphs = self._split_paragraphs(task.final_output or "")
        index_by_fp: dict[str, int] = {}
        for index, paragraph in enumerate(paragraphs):
            index_by_fp.setdefault(paragraph_fingerprint(paragraph), index)

        annotations = await self.list_for_task(user_id, task_id)
        result: list[dict[str, Any]] = []
        for annotation in annotations:
            if annotation.anchor_type != "paragraph" or not annotation.anchor_key:
                continue
            position = index_by_fp.get(annotation.anchor_key)
            result.append(
                {
                    "annotation_id": str(annotation.id),
                    "fingerprint": annotation.anchor_key,
                    "content": annotation.content,
                    "author_id": str(annotation.author_id),
                    "matched": position is not None,
                    "paragraph_index": position,
                    "resolved": annotation.resolved_at is not None,
                }
            )
        return result

    @staticmethod
    def _split_paragraphs(output: str) -> list[str]:
        """按空行切分 Markdown 段落（去掉空段）。

        **与指纹口径一致**：切分后每段都过 `paragraph_fingerprint`，
        它内部会 `strip()`，所以切分时不必额外处理首尾空白。
        """
        return [chunk for chunk in _PARAGRAPH_SPLIT.split(output) if chunk.strip()]
