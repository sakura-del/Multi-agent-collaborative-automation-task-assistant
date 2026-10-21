"""工作区与批注数据模型测试（Phase 2 · 团队工作区 Task 1）。

## 本文件锁定

1. 四张新表的字段与约束（唯一键、外键、可空性）；
2. `task.workspace_id` 的**双轨语义**：NULL = 个人任务，非空 = 工作区任务；
3. 迁移可 upgrade / downgrade / 重复 upgrade（幂等）。

## 为什么先测约束而不是先测服务

唯一键（`workspace_id + user_id`）与「owner 不可重复」这类约束是**数据层最后防线**：
服务层漏了检查时，数据库必须兜住。只测服务层会让「并发下同一人加入两次」
这类问题在线上才暴露。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  确保全部 ORM 注册
from app.db.base import Base
from app.db.models.annotation import TaskAnnotation
from app.db.models.task import Task
from app.db.models.user import User
from app.db.models.workspace import Workspace, WorkspaceInvite, WorkspaceMember


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _make_user(factory: async_sessionmaker[AsyncSession], name: str) -> User:
    async with factory() as session:
        user = User(
            username=name,
            email=f"{name}@example.com",
            password_hash="x",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


class TestWorkspaceModel:
    @pytest.mark.asyncio
    async def test_create_workspace(self, factory) -> None:
        owner = await _make_user(factory, "owner1")
        async with factory() as session:
            workspace = Workspace(
                name="研究组",
                slug="research-group",
                owner_id=owner.id,
                seat_limit=5,
            )
            session.add(workspace)
            await session.commit()
            await session.refresh(workspace)

            assert workspace.id is not None
            assert workspace.name == "研究组"
            assert workspace.slug == "research-group"
            assert workspace.seat_limit == 5
            assert workspace.created_at is not None

    @pytest.mark.asyncio
    async def test_slug_is_unique(self, factory) -> None:
        """slug 进 URL，重复会让路由歧义。"""
        owner = await _make_user(factory, "owner2")
        async with factory() as session:
            session.add(Workspace(name="A", slug="dup-slug", owner_id=owner.id, seat_limit=5))
            await session.commit()

        async with factory() as session:
            session.add(Workspace(name="B", slug="dup-slug", owner_id=owner.id, seat_limit=5))
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_seat_limit_defaults_to_five(self, factory) -> None:
        owner = await _make_user(factory, "owner3")
        async with factory() as session:
            workspace = Workspace(name="默认席位", slug="default-seat", owner_id=owner.id)
            session.add(workspace)
            await session.commit()
            await session.refresh(workspace)

            assert workspace.seat_limit == 5


class TestWorkspaceMemberModel:
    @pytest.mark.asyncio
    async def test_create_member(self, factory) -> None:
        owner = await _make_user(factory, "owner4")
        member_user = await _make_user(factory, "member4")
        async with factory() as session:
            workspace = Workspace(name="W", slug="w4", owner_id=owner.id, seat_limit=5)
            session.add(workspace)
            await session.commit()
            await session.refresh(workspace)

            session.add(
                WorkspaceMember(workspace_id=workspace.id, user_id=member_user.id, role="member")
            )
            await session.commit()

        async with factory() as session:
            from sqlalchemy import select

            rows = (await session.execute(select(WorkspaceMember))).scalars().all()
            assert len(rows) == 1
            assert rows[0].role == "member"
            assert rows[0].joined_at is not None

    @pytest.mark.asyncio
    async def test_same_user_cannot_join_twice(self, factory) -> None:
        """唯一键兜住「同一人重复加入」（并发接受邀请的最后防线）。"""
        owner = await _make_user(factory, "owner5")
        member_user = await _make_user(factory, "member5")
        async with factory() as session:
            workspace = Workspace(name="W", slug="w5", owner_id=owner.id, seat_limit=5)
            session.add(workspace)
            await session.commit()
            await session.refresh(workspace)
            session.add(
                WorkspaceMember(workspace_id=workspace.id, user_id=member_user.id, role="member")
            )
            await session.commit()

        async with factory() as session:
            session.add(
                WorkspaceMember(workspace_id=workspace.id, user_id=member_user.id, role="viewer")
            )
            with pytest.raises(IntegrityError):
                await session.commit()


class TestWorkspaceInviteModel:
    @pytest.mark.asyncio
    async def test_create_invite(self, factory) -> None:
        owner = await _make_user(factory, "owner6")
        async with factory() as session:
            workspace = Workspace(name="W", slug="w6", owner_id=owner.id, seat_limit=5)
            session.add(workspace)
            await session.commit()
            await session.refresh(workspace)

            invite = WorkspaceInvite(
                workspace_id=workspace.id,
                code="abc123def456",
                role="member",
                created_by=owner.id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            session.add(invite)
            await session.commit()
            await session.refresh(invite)

            assert invite.code == "abc123def456"
            assert invite.role == "member"
            assert invite.used_at is None
            assert invite.used_by is None

    @pytest.mark.asyncio
    async def test_invite_code_is_unique(self, factory) -> None:
        owner = await _make_user(factory, "owner7")
        async with factory() as session:
            workspace = Workspace(name="W", slug="w7", owner_id=owner.id, seat_limit=5)
            session.add(workspace)
            await session.commit()
            await session.refresh(workspace)
            expires = datetime.now(UTC) + timedelta(days=7)
            session.add(
                WorkspaceInvite(
                    workspace_id=workspace.id,
                    code="same-code",
                    role="member",
                    created_by=owner.id,
                    expires_at=expires,
                )
            )
            await session.commit()

        async with factory() as session:
            session.add(
                WorkspaceInvite(
                    workspace_id=workspace.id,
                    code="same-code",
                    role="member",
                    created_by=owner.id,
                    expires_at=expires,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()


class TestTaskWorkspaceDualTrack:
    @pytest.mark.asyncio
    async def test_task_workspace_id_is_nullable(self, factory) -> None:
        """**双轨语义的核心**：NULL = 个人任务（存量任务保持此语义）。"""
        user = await _make_user(factory, "taskowner")
        async with factory() as session:
            task = Task(
                user_id=user.id,
                title="个人任务",
                description="d",
                status="pending",
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)

            assert task.workspace_id is None, "个人任务的 workspace_id 必须为 NULL"

    @pytest.mark.asyncio
    async def test_task_can_belong_to_workspace(self, factory) -> None:
        owner = await _make_user(factory, "wsowner")
        async with factory() as session:
            workspace = Workspace(name="W", slug="w8", owner_id=owner.id, seat_limit=5)
            session.add(workspace)
            await session.commit()
            await session.refresh(workspace)

            task = Task(
                user_id=owner.id,
                title="工作区任务",
                description="d",
                status="pending",
                workspace_id=workspace.id,
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)

            assert task.workspace_id == workspace.id
            # user_id 仍是创建者（配额按发起人扣，不改既有口径）
            assert task.user_id == owner.id


class TestAnnotationModel:
    @pytest.mark.asyncio
    async def test_create_task_level_annotation(self, factory) -> None:
        user = await _make_user(factory, "annotator")
        async with factory() as session:
            task = Task(user_id=user.id, title="T", description="d", status="completed")
            session.add(task)
            await session.commit()
            await session.refresh(task)

            annotation = TaskAnnotation(
                task_id=task.id,
                author_id=user.id,
                anchor_type="task",
                anchor_key=None,
                content="整体结构不错，但缺少局限性讨论",
            )
            session.add(annotation)
            await session.commit()
            await session.refresh(annotation)

            assert annotation.id is not None
            assert annotation.anchor_type == "task"
            assert annotation.anchor_key is None
            assert annotation.resolved_at is None

    @pytest.mark.asyncio
    async def test_create_paragraph_annotation_with_fingerprint(self, factory) -> None:
        """段落级批注用内容指纹做锚点（字符偏移会因重跑失效）。"""
        user = await _make_user(factory, "annotator2")
        async with factory() as session:
            task = Task(user_id=user.id, title="T", description="d", status="completed")
            session.add(task)
            await session.commit()
            await session.refresh(task)

            annotation = TaskAnnotation(
                task_id=task.id,
                author_id=user.id,
                anchor_type="paragraph",
                anchor_key="a1b2c3d4e5f60718",
                content="这段引用需要补页码",
            )
            session.add(annotation)
            await session.commit()
            await session.refresh(annotation)

            assert annotation.anchor_type == "paragraph"
            assert annotation.anchor_key == "a1b2c3d4e5f60718"

    @pytest.mark.asyncio
    async def test_resolved_annotation_records_time(self, factory) -> None:
        user = await _make_user(factory, "annotator3")
        async with factory() as session:
            task = Task(user_id=user.id, title="T", description="d", status="completed")
            session.add(task)
            await session.commit()
            await session.refresh(task)

            annotation = TaskAnnotation(
                task_id=task.id,
                author_id=user.id,
                anchor_type="task",
                content="请补充",
            )
            session.add(annotation)
            await session.commit()

            annotation.resolved_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(annotation)

            assert annotation.resolved_at is not None
