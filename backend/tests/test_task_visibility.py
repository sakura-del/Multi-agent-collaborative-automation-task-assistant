"""任务归属双轨测试（Phase 2 · 团队工作区 Task 3）。

## 双轨语义

| `workspace_id` | 含义 | 可见范围 |
| --- | --- | --- |
| `NULL` | 个人任务（**存量任务全部是这一类**） | 仅 `user_id` 本人 |
| 非空 | 工作区任务 | 工作区内全部成员 |

## 本文件锁定

1. 个人任务**不**因工作区成员身份而被别人看到；
2. 工作区任务对**全部成员**可见（含 viewer）；
3. 非成员访问工作区任务 → **404 语义**（返回 None）；
4. 旧方法 `get_for_user` / `list_for_user` **仍然只认 owner**（向后兼容，
   避免存量调用点行为突变）；
5. 创建任务到工作区时校验成员身份与角色（viewer 不能创建）。

## 为什么保留旧方法

存量代码有几十处 `get_for_user` 调用（含编排层后台任务）。一次性全改成
`get_visible` 会让「后台任务按 task_id 取任务」这类内部调用突然带上工作区语义，
风险远大于收益。所以旧方法**语义不变**，新代码走新方法。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.collaboration.services.workspace_service import WorkspaceService
from app.db.base import Base
from app.db.models.task import Task
from app.db.models.user import User
from app.db.repositories.task import TaskRepository


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _user(factory, name: str) -> User:
    async with factory() as session:
        user = User(username=name, email=f"{name}@example.com", password_hash="x", is_active=True)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _task(factory, user_id, *, title: str, workspace_id=None) -> Task:
    async with factory() as session:
        task = Task(
            user_id=user_id,
            title=title,
            description="d",
            status="completed",
            workspace_id=workspace_id,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task


async def _workspace_with_members(factory, owner, *members, roles=None) -> uuid.UUID:
    service = WorkspaceService(factory)
    workspace = await service.create(
        owner.id, name="组", slug=f"ws-{uuid.uuid4().hex[:8]}", seat_limit=10
    )
    for member in members:
        role = (roles or {}).get(member.username, "member")
        invite = await service.create_invite(owner.id, workspace.id, role=role)
        await service.accept_invite(member.id, invite.code)
    return workspace.id


class TestPersonalTaskIsolation:
    @pytest.mark.asyncio
    async def test_personal_task_not_visible_to_workspace_mates(self, factory) -> None:
        """**关键**：个人任务不因「同属一个工作区」而被同事看到。

        注意这里必须让 bob **有**工作区（`workspace_ids` 非空），否则
        可见性条件退化成只有 `personal` 分支，`workspace_id IS NULL` 这个
        条件根本不会被检验 —— 首版就是这样写的，把 `IS NULL` 删掉测试仍然全绿
        （AGENTS #13 的假测试陷阱）。
        """
        alice = await _user(factory, "iso_alice")
        bob = await _user(factory, "iso_bob")
        await _workspace_with_members(factory, alice, bob)
        personal = await _task(factory, alice.id, title="Alice 私人任务")

        async with factory() as session:
            repo = TaskRepository(session)
            workspace_ids = await WorkspaceService(factory).workspace_ids_for_user(bob.id)
            assert workspace_ids, "bob 必须至少属于一个工作区，否则测不到 OR 分支"
            visible = await repo.get_visible(bob.id, workspace_ids, personal.id)

        assert visible is None, "个人任务绝不能对同事可见"

    @pytest.mark.asyncio
    async def test_owner_still_sees_own_personal_task(self, factory) -> None:
        alice = await _user(factory, "own_alice")
        personal = await _task(factory, alice.id, title="我的任务")

        async with factory() as session:
            repo = TaskRepository(session)
            visible = await repo.get_visible(alice.id, [], personal.id)

        assert visible is not None
        assert visible.id == personal.id


class TestWorkspaceTaskVisibility:
    @pytest.mark.asyncio
    async def test_all_members_see_workspace_task(self, factory) -> None:
        owner = await _user(factory, "wv_owner")
        member = await _user(factory, "wv_member")
        viewer = await _user(factory, "wv_viewer")
        workspace_id = await _workspace_with_members(
            factory, owner, member, viewer, roles={"wv_viewer": "viewer"}
        )
        task = await _task(factory, owner.id, title="组任务", workspace_id=workspace_id)

        for user in (owner, member, viewer):
            async with factory() as session:
                repo = TaskRepository(session)
                workspace_ids = await WorkspaceService(factory).workspace_ids_for_user(user.id)
                visible = await repo.get_visible(user.id, workspace_ids, task.id)
            assert visible is not None, f"{user.username} 应该能看到工作区任务"

    @pytest.mark.asyncio
    async def test_non_member_cannot_see_workspace_task(self, factory) -> None:
        owner = await _user(factory, "nm_owner2")
        outsider = await _user(factory, "nm_outsider2")
        workspace_id = await _workspace_with_members(factory, owner)
        task = await _task(factory, owner.id, title="组任务", workspace_id=workspace_id)

        async with factory() as session:
            repo = TaskRepository(session)
            visible = await repo.get_visible(outsider.id, [], task.id)

        assert visible is None, "非成员访问必须返回 None（端点层 404）"

    @pytest.mark.asyncio
    async def test_list_visible_merges_personal_and_workspace(self, factory) -> None:
        owner = await _user(factory, "lm_owner")
        member = await _user(factory, "lm_member")
        workspace_id = await _workspace_with_members(factory, owner, member)

        await _task(factory, owner.id, title="owner 组任务", workspace_id=workspace_id)
        await _task(factory, owner.id, title="owner 私人任务")
        await _task(factory, member.id, title="member 私人任务")
        await _task(factory, member.id, title="member 组任务", workspace_id=workspace_id)

        async with factory() as session:
            repo = TaskRepository(session)
            workspace_ids = await WorkspaceService(factory).workspace_ids_for_user(member.id)
            visible = await repo.list_visible(member.id, workspace_ids)

        titles = {t.title for t in visible}
        # member 应该看到：自己的私人 + 两个组任务
        assert titles == {"member 私人任务", "owner 组任务", "member 组任务"}
        assert "owner 私人任务" not in titles, "owner 的私人任务不该被 member 看到"


class TestLegacyMethodsUnchanged:
    @pytest.mark.asyncio
    async def test_get_for_user_still_owner_only(self, factory) -> None:
        """**向后兼容**：旧方法只认 owner，避免存量调用点行为突变。"""
        owner = await _user(factory, "lg_owner")
        member = await _user(factory, "lg_member")
        workspace_id = await _workspace_with_members(factory, owner, member)
        task = await _task(factory, owner.id, title="组任务", workspace_id=workspace_id)

        async with factory() as session:
            repo = TaskRepository(session)
            assert await repo.get_for_user(member.id, task.id) is None
            assert await repo.get_for_user(owner.id, task.id) is not None

    @pytest.mark.asyncio
    async def test_list_for_user_still_owner_only(self, factory) -> None:
        owner = await _user(factory, "lf_owner")
        member = await _user(factory, "lf_member")
        workspace_id = await _workspace_with_members(factory, owner, member)
        await _task(factory, owner.id, title="组任务", workspace_id=workspace_id)

        async with factory() as session:
            repo = TaskRepository(session)
            mine = await repo.list_for_user(member.id)

        assert mine == [], "旧方法不该返回别人的任务（哪怕是同组）"


class TestCreateInWorkspace:
    @pytest.mark.asyncio
    async def test_viewer_cannot_create_task(self, factory) -> None:
        """viewer 只读 + 可批注，但**不能创建任务**。"""
        owner = await _user(factory, "cv_owner")
        viewer = await _user(factory, "cv_viewer")
        workspace_id = await _workspace_with_members(
            factory, owner, viewer, roles={"cv_viewer": "viewer"}
        )
        service = WorkspaceService(factory)

        assert await service.can_create_task(viewer.id, workspace_id) is False
        assert await service.can_create_task(owner.id, workspace_id) is True

    @pytest.mark.asyncio
    async def test_non_member_cannot_create_task(self, factory) -> None:
        owner = await _user(factory, "cn_owner")
        outsider = await _user(factory, "cn_outsider")
        workspace_id = await _workspace_with_members(factory, owner)
        service = WorkspaceService(factory)

        assert await service.can_create_task(outsider.id, workspace_id) is False
