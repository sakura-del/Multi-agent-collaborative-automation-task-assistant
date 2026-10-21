"""工作区领域服务测试（Phase 2 · 团队工作区 Task 2）。

## 本文件锁定

1. **创建**：owner 自动入组为 `owner` 角色，占一个席位；
2. **席位上限**：满员后接受邀请被拒（**并发下也不能超发**）；
3. **邀请码**：一次性（用过即失效）、过期失效、角色继承；
4. **权限**：只有 owner/admin 能邀请/移除；不能移除 owner；不能操作同级或更高角色；
5. **越权口径**：非成员访问返回 None（端点层转 404），角色不足抛 403。

## 为什么席位检查必须在事务内 `FOR UPDATE`

「先 COUNT 再 INSERT」在并发下必然超发：两个请求同时读到 4/5，都插入 → 6 席。
计费按席位，超发就是直接少收钱。所以锁工作区行。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.collaboration.services.workspace_service import (
    InviteError,
    PermissionError_,
    WorkspaceError,
    WorkspaceService,
)
from app.db.base import Base
from app.db.models.user import User


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


class TestCreateWorkspace:
    @pytest.mark.asyncio
    async def test_owner_is_auto_joined_as_owner(self, factory) -> None:
        """创建者自动成为 owner 成员（占席位）。"""
        owner = await _user(factory, "w_owner1")
        service = WorkspaceService(factory)

        workspace = await service.create(owner.id, name="研究组", slug="rg1", seat_limit=5)

        assert workspace.slug == "rg1"
        members = await service.list_members(owner.id, workspace.id)
        assert len(members) == 1
        assert members[0].user_id == owner.id
        assert members[0].role == "owner"

    @pytest.mark.asyncio
    async def test_slug_conflict_raises(self, factory) -> None:
        owner = await _user(factory, "w_owner2")
        service = WorkspaceService(factory)
        await service.create(owner.id, name="A", slug="dup", seat_limit=5)

        with pytest.raises(WorkspaceError, match="标识已被占用"):
            await service.create(owner.id, name="B", slug="dup", seat_limit=5)

    @pytest.mark.asyncio
    async def test_list_only_my_workspaces(self, factory) -> None:
        alice = await _user(factory, "alice")
        bob = await _user(factory, "bob")
        service = WorkspaceService(factory)
        ws_a = await service.create(alice.id, name="Alice 组", slug="alice-ws", seat_limit=5)
        await service.create(bob.id, name="Bob 组", slug="bob-ws", seat_limit=5)

        mine = await service.list_for_user(alice.id)

        assert [w.id for w in mine] == [ws_a.id]


class TestInviteFlow:
    @pytest.mark.asyncio
    async def test_invite_and_accept(self, factory) -> None:
        owner = await _user(factory, "i_owner")
        student = await _user(factory, "i_student")
        service = WorkspaceService(factory)
        workspace = await service.create(owner.id, name="组", slug="inv1", seat_limit=5)

        invite = await service.create_invite(owner.id, workspace.id, role="member")
        member = await service.accept_invite(student.id, invite.code)

        assert member.role == "member"
        assert member.user_id == student.id

    @pytest.mark.asyncio
    async def test_invite_is_single_use(self, factory) -> None:
        """一次性：同一码第二次使用必须被拒（防止席位被共享超发）。"""
        owner = await _user(factory, "su_owner")
        s1 = await _user(factory, "su_s1")
        s2 = await _user(factory, "su_s2")
        service = WorkspaceService(factory)
        workspace = await service.create(owner.id, name="组", slug="inv2", seat_limit=5)
        invite = await service.create_invite(owner.id, workspace.id, role="member")

        await service.accept_invite(s1.id, invite.code)

        with pytest.raises(InviteError, match="已使用"):
            await service.accept_invite(s2.id, invite.code)

    @pytest.mark.asyncio
    async def test_expired_invite_rejected(self, factory) -> None:
        owner = await _user(factory, "ex_owner")
        student = await _user(factory, "ex_student")
        service = WorkspaceService(factory)
        workspace = await service.create(owner.id, name="组", slug="inv3", seat_limit=5)
        invite = await service.create_invite(
            owner.id, workspace.id, role="member", ttl=timedelta(seconds=-1)
        )

        with pytest.raises(InviteError, match="过期"):
            await service.accept_invite(student.id, invite.code)

    @pytest.mark.asyncio
    async def test_unknown_code_rejected(self, factory) -> None:
        student = await _user(factory, "un_student")
        service = WorkspaceService(factory)

        with pytest.raises(InviteError, match="无效"):
            await service.accept_invite(student.id, "no-such-code")

    @pytest.mark.asyncio
    async def test_seat_limit_enforced(self, factory) -> None:
        """席位满 → 拒绝加入（计费按席位，超发等于少收钱）。"""
        owner = await _user(factory, "sl_owner")
        s1 = await _user(factory, "sl_s1")
        s2 = await _user(factory, "sl_s2")
        service = WorkspaceService(factory)
        # seat_limit=2 → owner 占 1 席，只能再进 1 人
        workspace = await service.create(owner.id, name="组", slug="seat2", seat_limit=2)

        inv1 = await service.create_invite(owner.id, workspace.id, role="member")
        await service.accept_invite(s1.id, inv1.code)

        inv2 = await service.create_invite(owner.id, workspace.id, role="member")
        with pytest.raises(WorkspaceError, match="席位"):
            await service.accept_invite(s2.id, inv2.code)

    @pytest.mark.asyncio
    async def test_already_member_rejected(self, factory) -> None:
        owner = await _user(factory, "am_owner")
        student = await _user(factory, "am_student")
        service = WorkspaceService(factory)
        workspace = await service.create(owner.id, name="组", slug="inv4", seat_limit=5)

        inv1 = await service.create_invite(owner.id, workspace.id, role="member")
        await service.accept_invite(student.id, inv1.code)

        inv2 = await service.create_invite(owner.id, workspace.id, role="member")
        with pytest.raises(WorkspaceError, match="已在"):
            await service.accept_invite(student.id, inv2.code)


class TestPermissions:
    @pytest.mark.asyncio
    async def test_member_cannot_invite(self, factory) -> None:
        owner = await _user(factory, "pm_owner")
        member = await _user(factory, "pm_member")
        service = WorkspaceService(factory)
        workspace = await service.create(owner.id, name="组", slug="perm1", seat_limit=5)
        inv = await service.create_invite(owner.id, workspace.id, role="member")
        await service.accept_invite(member.id, inv.code)

        with pytest.raises(PermissionError_, match="权限"):
            await service.create_invite(member.id, workspace.id, role="member")

    @pytest.mark.asyncio
    async def test_admin_can_invite(self, factory) -> None:
        owner = await _user(factory, "pa_owner")
        admin = await _user(factory, "pa_admin")
        service = WorkspaceService(factory)
        workspace = await service.create(owner.id, name="组", slug="perm2", seat_limit=5)
        inv = await service.create_invite(owner.id, workspace.id, role="admin")
        await service.accept_invite(admin.id, inv.code)

        # admin 能邀请
        new_invite = await service.create_invite(admin.id, workspace.id, role="member")
        assert new_invite.code

    @pytest.mark.asyncio
    async def test_cannot_remove_owner(self, factory) -> None:
        """owner 不可移除（否则工作区变成无主）。"""
        owner = await _user(factory, "ro_owner")
        service = WorkspaceService(factory)
        workspace = await service.create(owner.id, name="组", slug="perm3", seat_limit=5)

        with pytest.raises(WorkspaceError, match="owner"):
            await service.remove_member(owner.id, workspace.id, owner.id)

    @pytest.mark.asyncio
    async def test_cannot_remove_higher_role(self, factory) -> None:
        """admin 不能移除 owner。

        错误类型是 `WorkspaceError`（「owner 不可移除」是业务硬规则），
        **不是** `PermissionError_`。这个区分有意义：前者说明「谁来都删不掉」，
        后者说明「你级别不够，找 owner 来」——排障与提示文案完全不同。
        """
        owner = await _user(factory, "rh_owner")
        admin = await _user(factory, "rh_admin")
        service = WorkspaceService(factory)
        workspace = await service.create(owner.id, name="组", slug="perm4", seat_limit=5)
        inv = await service.create_invite(owner.id, workspace.id, role="admin")
        await service.accept_invite(admin.id, inv.code)

        with pytest.raises(WorkspaceError, match="owner"):
            await service.remove_member(admin.id, workspace.id, owner.id)

    @pytest.mark.asyncio
    async def test_cannot_remove_peer_or_higher_non_owner(self, factory) -> None:
        """同级不可互删：admin A 不能移除 admin B（否则互相踢人）。"""
        owner = await _user(factory, "rp_owner")
        admin_a = await _user(factory, "rp_admin_a")
        admin_b = await _user(factory, "rp_admin_b")
        service = WorkspaceService(factory)
        workspace = await service.create(owner.id, name="组", slug="perm4b", seat_limit=5)
        for user in (admin_a, admin_b):
            inv = await service.create_invite(owner.id, workspace.id, role="admin")
            await service.accept_invite(user.id, inv.code)

        with pytest.raises(PermissionError_, match="权限"):
            await service.remove_member(admin_a.id, workspace.id, admin_b.id)

    @pytest.mark.asyncio
    async def test_non_member_gets_none(self, factory) -> None:
        """**越权口径**：非成员访问返回 None（端点层转 404，不泄漏存在性）。"""
        owner = await _user(factory, "nm_owner")
        outsider = await _user(factory, "nm_outsider")
        service = WorkspaceService(factory)
        workspace = await service.create(owner.id, name="组", slug="perm5", seat_limit=5)

        assert await service.get_for_user(outsider.id, workspace.id) is None
        assert await service.list_members(outsider.id, workspace.id) is None

    @pytest.mark.asyncio
    async def test_member_can_leave(self, factory) -> None:
        owner = await _user(factory, "lv_owner")
        member = await _user(factory, "lv_member")
        service = WorkspaceService(factory)
        workspace = await service.create(owner.id, name="组", slug="perm6", seat_limit=5)
        inv = await service.create_invite(owner.id, workspace.id, role="member")
        await service.accept_invite(member.id, inv.code)

        await service.leave(member.id, workspace.id)

        members = await service.list_members(owner.id, workspace.id)
        assert [m.user_id for m in members] == [owner.id]


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_accept_does_not_exceed_seat_limit(self, tmp_path) -> None:
        """**并发接受邀请不能超发席位**（计费按席位，超发直接少收钱）。

        为什么用**文件型 SQLite + 独立 engine**：`:memory:` 库在多个连接间不共享，
        而共享同一个 engine 时 `asyncio.gather` 会让 aiosqlite 的 greenlet 上下文
        互相干扰（实测 `MissingGreenlet`）。文件型 + 每任务独立 engine 更接近
        真实多连接场景。

        SQLite 的 `FOR UPDATE` 是空操作，所以这条测的是「插入后复查计数」这条
        兜底路径 —— 它在 PostgreSQL 上与行锁叠加，不会退化。
        """
        from sqlalchemy.ext.asyncio import AsyncSession as _Session
        from sqlalchemy.ext.asyncio import async_sessionmaker as _factory
        from sqlalchemy.ext.asyncio import create_async_engine as _engine

        db_path = tmp_path / "concurrent.db"
        url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        setup_engine = _engine(url)
        async with setup_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await setup_engine.dispose()

        setup_factory = _factory(setup_engine, class_=_Session, expire_on_commit=False)
        # 先建 owner / 学生 / 工作区 / 邀请码（串行，避免测试自身制造竞态）
        async with setup_factory() as session:
            owner = User(
                username="cc_owner", email="cc_owner@example.com", password_hash="x", is_active=True
            )
            students = [
                User(
                    username=f"cc_s{i}",
                    email=f"cc_s{i}@example.com",
                    password_hash="x",
                    is_active=True,
                )
                for i in range(4)
            ]
            session.add_all([owner, *students])
            await session.commit()
            owner_id = owner.id
            student_ids = [s.id for s in students]

        service = WorkspaceService(setup_factory)
        workspace = await service.create(owner_id, name="组", slug="conc1", seat_limit=2)
        invites = [
            await service.create_invite(owner_id, workspace.id, role="member") for _ in range(4)
        ]

        async def attempt(user_id, code: str) -> bool:
            # 每条任务独立 engine（独立连接），模拟真实并发
            engine = _engine(url)
            factory = _factory(engine, class_=_Session, expire_on_commit=False)
            try:
                await WorkspaceService(factory).accept_invite(user_id, code)
                return True
            except (WorkspaceError, InviteError):
                return False
            finally:
                await engine.dispose()

        results = await asyncio.gather(
            *(attempt(uid, inv.code) for uid, inv in zip(student_ids, invites, strict=True))
        )

        members = await service.list_members(owner_id, workspace.id)
        assert len(members) <= 2, f"席位超发：{len(members)} > 2"
        assert sum(results) <= 1, "seat_limit=2 且 owner 占 1 席，最多只能再进 1 人"


class TestInviteHygiene:
    @pytest.mark.asyncio
    async def test_code_is_high_entropy(self, factory) -> None:
        """邀请码必须够长（可被猜到的码等于后门）。"""
        owner = await _user(factory, "hy_owner")
        service = WorkspaceService(factory)
        workspace = await service.create(owner.id, name="组", slug="hyg1", seat_limit=5)

        invite = await service.create_invite(owner.id, workspace.id, role="member")

        assert len(invite.code) >= 16
        assert invite.code.isalnum() or all(c.isalnum() or c in "-_" for c in invite.code)

    @pytest.mark.asyncio
    async def test_expires_at_defaults_to_seven_days(self, factory) -> None:
        owner = await _user(factory, "dt_owner")
        service = WorkspaceService(factory)
        workspace = await service.create(owner.id, name="组", slug="hyg2", seat_limit=5)

        before = datetime.now(UTC)
        invite = await service.create_invite(owner.id, workspace.id, role="member")

        # SQLite 读回的时间戳可能不带 tz（PostgreSQL 带），统一按 UTC 比较
        expires_at = invite.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        delta = expires_at - before
        assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1)
