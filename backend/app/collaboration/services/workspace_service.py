"""工作区领域服务（Phase 2 · 团队工作区 Task 2）。

## 席位是计费单位，所以并发安全是硬要求

「先 `COUNT` 再 `INSERT`」在并发下必然超发：两个请求同时读到 4/5，都插入 → 6 席。
计费按席位，超发就是直接少收钱。三层防护：

1. **事务内锁工作区行**（`SELECT ... FOR UPDATE`）：串行化同一工作区的加入；
2. **唯一键** `(workspace_id, user_id)`：同一人重复加入由数据库兜住；
3. **失败即拒绝**（不重试、不吞异常）：宁可让用户重试一次，也不放过超发。

> SQLite 下 `FOR UPDATE` 是空操作（写锁是库级），所以本地测试验证的是
> 第 2、3 层；第 1 层在 PostgreSQL 上生效（CI 的 Alembic 作业与生产环境）。

## 越权口径（与既有 `TaskRepository` 一致）

- **非成员**访问工作区 → 返回 `None`，端点层转 **404**（不泄漏「那个 id 存在」）；
- **成员但角色不足**（如 member 想邀请）→ 抛 `PermissionError_`，端点层转 **403**。
  成员已经知道工作区存在，隐藏无意义；403 能给出可读的「权限不足」。

## 邀请码

一次性（`used_at` 非空即失效）+ 默认 7 天过期 + 高熵随机。
**为什么不用邮件邀请**：SMTP 未配齐（P1-C 遗留）。邀请码可复制粘贴，不依赖邮件通道。
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.db.models.user import User
from app.db.models.workspace import (
    DEFAULT_SEAT_LIMIT,
    ROLE_RANK,
    WORKSPACE_ROLES,
    Workspace,
    WorkspaceInvite,
    WorkspaceMember,
)

logger = get_logger(__name__)

#: 邀请码有效期（默认 7 天）。
DEFAULT_INVITE_TTL = timedelta(days=7)

#: 邀请码长度（32 字符 base32 风格 → 约 160 bit 熵，猜不到）。
_INVITE_CODE_BYTES = 20


class WorkspaceError(Exception):
    """工作区通用错误（端点层转 400/409）。"""


class InviteError(Exception):
    """邀请码错误（无效/过期/已使用）。端点层转 400。"""


class PermissionError_(Exception):
    """角色不足（端点层转 **403**）。

    命名带下划线：避免遮蔽内建 `PermissionError`（OS 错误），两者混用会让
    `except PermissionError` 意外捕获到业务异常。
    """


def _normalize_role(role: str) -> str:
    if role not in WORKSPACE_ROLES:
        raise WorkspaceError(f"未知角色：{role}（可选：{', '.join(WORKSPACE_ROLES)}）")
    return role


class WorkspaceService:
    """工作区与成员的领域服务。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # ===== 工作区 =====

    async def create(
        self,
        owner_id: uuid.UUID,
        *,
        name: str,
        slug: str,
        seat_limit: int = DEFAULT_SEAT_LIMIT,
    ) -> Workspace:
        """创建工作区，并把创建者自动加入为 `owner`（占一个席位）。"""
        if seat_limit < 1:
            raise WorkspaceError("席位上限至少为 1")
        async with self._session_factory() as session:
            exists = await session.scalar(select(Workspace).where(Workspace.slug == slug))
            if exists is not None:
                raise WorkspaceError(f"工作区标识已被占用：{slug}")

            workspace = Workspace(
                name=name.strip(),
                slug=slug.strip(),
                owner_id=owner_id,
                seat_limit=seat_limit,
            )
            session.add(workspace)
            try:
                await session.flush()
                session.add(
                    WorkspaceMember(workspace_id=workspace.id, user_id=owner_id, role="owner")
                )
                await session.commit()
            except IntegrityError as exc:  # pragma: no cover - 并发兜底
                await session.rollback()
                raise WorkspaceError(f"工作区标识已被占用：{slug}") from exc
            await session.refresh(workspace)
            logger.info("workspace_created", workspace_id=str(workspace.id), slug=slug)
            return workspace

    async def list_for_user(self, user_id: uuid.UUID) -> list[Workspace]:
        """我参与的工作区（含我是 owner 的）。"""
        async with self._session_factory() as session:
            rows = await session.execute(
                select(Workspace)
                .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                .where(WorkspaceMember.user_id == user_id)
                .order_by(Workspace.created_at.desc(), Workspace.id.desc())
            )
            return list(rows.scalars().all())

    async def count_owned(self, user_id: uuid.UUID) -> int:
        """**我拥有的**工作区数量（套餐 `max_workspaces` 的计数口径）。

        为什么不复用 `list_for_user`：那个返回「我参与的」，包含别人邀请我进的。
        若按参与数计额度，用户被拉进 10 个团队后就无法建自己的团队了 ——
        额度被别人的邀请消耗掉，这显然不对。

        额度只按**我创建的**算。
        """
        async with self._session_factory() as session:
            count = await session.scalar(
                select(func.count()).select_from(Workspace).where(Workspace.owner_id == user_id)
            )
            return int(count or 0)

    async def get_for_user(self, user_id: uuid.UUID, workspace_id: uuid.UUID) -> Workspace | None:
        """非成员返回 `None`（端点层转 404，不泄漏存在性）。"""
        async with self._session_factory() as session:
            return await session.scalar(
                select(Workspace)
                .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                .where(
                    Workspace.id == workspace_id,
                    WorkspaceMember.user_id == user_id,
                )
            )

    async def get_role(self, user_id: uuid.UUID, workspace_id: uuid.UUID) -> str | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(WorkspaceMember.role).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user_id,
                )
            )

    async def rename(self, actor_id: uuid.UUID, workspace_id: uuid.UUID, *, name: str) -> Workspace:
        """改名：**仅 owner**（admin 也不行——团队名是对外标识）。"""
        async with self._session_factory() as session:
            await self._require_role(session, actor_id, workspace_id, minimum="owner")
            workspace = await session.get(Workspace, workspace_id)
            assert workspace is not None  # _require_role 已确认存在
            workspace.name = name.strip()
            await session.commit()
            await session.refresh(workspace)
            return workspace

    async def seat_usage(self, workspace_id: uuid.UUID) -> tuple[int, int]:
        """(已用席位, 席位上限)。"""
        async with self._session_factory() as session:
            workspace = await session.get(Workspace, workspace_id)
            if workspace is None:
                raise WorkspaceError("工作区不存在")
            used = await session.scalar(
                select(func.count())
                .select_from(WorkspaceMember)
                .where(WorkspaceMember.workspace_id == workspace_id)
            )
            return int(used or 0), workspace.seat_limit

    # ===== 成员 =====

    async def list_members(
        self, actor_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> list[WorkspaceMember] | None:
        """成员列表（含角色与加入时间）。非成员返回 `None`。"""
        async with self._session_factory() as session:
            if await self.get_role(actor_id, workspace_id) is None:
                return None
            rows = await session.execute(
                select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)
                # 稳定排序：同事务写入的行时间戳相同（AGENTS #14）
                .order_by(WorkspaceMember.joined_at.asc(), WorkspaceMember.id.asc())
            )
            return list(rows.scalars().all())

    async def remove_member(
        self, actor_id: uuid.UUID, workspace_id: uuid.UUID, target_user_id: uuid.UUID
    ) -> None:
        """移除成员。约束：不能移除 owner；不能操作同级或更高角色。"""
        async with self._session_factory() as session:
            actor_role = await self._require_role(session, actor_id, workspace_id, minimum="admin")
            target_role = await self.get_role(target_user_id, workspace_id)
            if target_role is None:
                raise WorkspaceError("该用户不是工作区成员")
            # **顺序**：先判 owner（业务硬规则），再判角色等级。
            # 反过来会让「admin 移除 owner」报成「权限不足」，
            # 而真实原因是「owner 根本不可移除」——错误信息会误导排障。
            if target_role == "owner":
                raise WorkspaceError("不能移除工作区 owner（需先转让）")
            if ROLE_RANK[target_role] >= ROLE_RANK[actor_role]:
                raise PermissionError_("权限不足：不能操作同级或更高角色的成员")

            member = await session.scalar(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == target_user_id,
                )
            )
            assert member is not None
            await session.delete(member)
            await session.commit()
            logger.info(
                "workspace_member_removed",
                workspace_id=str(workspace_id),
                target=str(target_user_id),
            )

    async def change_role(
        self,
        actor_id: uuid.UUID,
        workspace_id: uuid.UUID,
        target_user_id: uuid.UUID,
        *,
        role: str,
    ) -> WorkspaceMember:
        """改角色。**仅 owner**；不能改 owner 自己（避免自降导致无主）。"""
        new_role = _normalize_role(role)
        if new_role == "owner":
            raise WorkspaceError("不能通过改角色转让 owner（需显式转让操作）")
        async with self._session_factory() as session:
            await self._require_role(session, actor_id, workspace_id, minimum="owner")
            member = await session.scalar(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == target_user_id,
                )
            )
            if member is None:
                raise WorkspaceError("该用户不是工作区成员")
            if member.role == "owner":
                raise WorkspaceError("不能修改 owner 的角色（需先转让）")
            member.role = new_role
            await session.commit()
            await session.refresh(member)
            return member

    async def leave(self, user_id: uuid.UUID, workspace_id: uuid.UUID) -> None:
        """主动退出。owner 不能退出（否则工作区无主）。"""
        async with self._session_factory() as session:
            role = await self.get_role(user_id, workspace_id)
            if role is None:
                raise WorkspaceError("你不是该工作区成员")
            if role == "owner":
                raise WorkspaceError("owner 不能退出（需先转让或解散）")
            member = await session.scalar(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user_id,
                )
            )
            assert member is not None
            await session.delete(member)
            await session.commit()

    # ===== 邀请 =====

    async def create_invite(
        self,
        actor_id: uuid.UUID,
        workspace_id: uuid.UUID,
        *,
        role: str = "member",
        ttl: timedelta = DEFAULT_INVITE_TTL,
    ) -> WorkspaceInvite:
        """生成一次性邀请码（admin+ 可生成）。"""
        invite_role = _normalize_role(role)
        if invite_role == "owner":
            raise WorkspaceError("邀请码不能直接授予 owner 角色")
        async with self._session_factory() as session:
            await self._require_role(session, actor_id, workspace_id, minimum="admin")
            invite = WorkspaceInvite(
                workspace_id=workspace_id,
                code=secrets.token_urlsafe(_INVITE_CODE_BYTES),
                role=invite_role,
                created_by=actor_id,
                expires_at=datetime.now(UTC) + ttl,
            )
            session.add(invite)
            await session.commit()
            await session.refresh(invite)
            logger.info(
                "workspace_invite_created",
                workspace_id=str(workspace_id),
                role=invite_role,
            )
            return invite

    async def accept_invite(self, user_id: uuid.UUID, code: str) -> WorkspaceMember:
        """接受邀请码加入工作区。

        **顺序很重要**：先锁工作区行 → 查席位 → 校验邀请码 → 插入成员。
        席位检查在锁内，否则并发下会超发（见模块 docstring）。
        """
        async with self._session_factory() as session:
            invite = await session.scalar(
                select(WorkspaceInvite).where(WorkspaceInvite.code == code)
            )
            if invite is None:
                raise InviteError("邀请码无效")
            if invite.used_at is not None:
                raise InviteError("邀请码已使用")
            now = datetime.now(UTC)
            # SQLite 读回的时间戳可能不带 tz，统一按 UTC 比较
            expires_at = invite.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at < now:
                raise InviteError("邀请码已过期")

            # 锁工作区行：串行化同一工作区的加入（PostgreSQL 生效）
            workspace = await session.scalar(
                select(Workspace).where(Workspace.id == invite.workspace_id).with_for_update()
            )
            if workspace is None:  # pragma: no cover - 外键保证
                raise InviteError("邀请码无效")
            # **立刻读成局部变量**：commit/rollback 之后访问 ORM 属性可能触发
            # 懒加载 IO，在并发（asyncio.gather + 多连接）下会撞 `MissingGreenlet`。
            workspace_id = workspace.id
            seat_limit = workspace.seat_limit

            existing = await session.scalar(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user_id,
                )
            )
            if existing is not None:
                raise WorkspaceError("你已在该工作区中")

            used = await session.scalar(
                select(func.count())
                .select_from(WorkspaceMember)
                .where(WorkspaceMember.workspace_id == workspace_id)
            )
            if int(used or 0) >= seat_limit:
                raise WorkspaceError(f"席位已满（{used}/{seat_limit}），请联系管理员扩容")

            member = WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=invite.role)
            session.add(member)
            invite.used_at = now
            invite.used_by = user_id
            seat_overflow = False
            try:
                await session.flush()
                # **席位复查**：`SELECT ... FOR UPDATE` 在 SQLite 下是空操作
                # （写锁是库级，无法串行化「读计数→插入」），PostgreSQL 下能挡住，
                # 但两个数据库共用这段代码。所以插入后**再数一次**：超了就回滚。
                #
                # 为什么不做成「乐观重试」：席位是计费单位，宁可让用户看到
                # 「席位已满，请联系管理员」重试一次，也不能放过一次超发。
                final_used = await session.scalar(
                    select(func.count())
                    .select_from(WorkspaceMember)
                    .where(WorkspaceMember.workspace_id == workspace_id)
                )
                if int(final_used or 0) > seat_limit:
                    seat_overflow = True
                else:
                    await session.commit()
            except IntegrityError as exc:
                # 唯一键兜底：并发下同一人重复加入
                await session.rollback()
                raise WorkspaceError("你已在该工作区中") from exc

            # 回滚 + 抛错放在 try 之外：在 try 内 raise 会让 `async with` 退出时
            # 撞上 session 的中间状态（实测 `MissingGreenlet`）。
            if seat_overflow:
                await session.rollback()
                raise WorkspaceError(f"席位已满（{seat_limit}），请联系管理员扩容")
            await session.refresh(member)
            logger.info(
                "workspace_invite_accepted",
                workspace_id=str(workspace_id),
                user_id=str(user_id),
                role=invite.role,
            )
            return member

    # ===== 内部 =====

    async def _require_role(
        self,
        session: AsyncSession,
        actor_id: uuid.UUID,
        workspace_id: uuid.UUID,
        *,
        minimum: str,
    ) -> str:
        """校验 actor 在工作区内的角色 ≥ `minimum`；返回实际角色。

        非成员 → `WorkspaceError`（端点层 404，不泄漏存在性）；
        角色不足 → `PermissionError_`（端点层 403）。
        """
        role = await session.scalar(
            select(WorkspaceMember.role).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == actor_id,
            )
        )
        if role is None:
            raise WorkspaceError("工作区不存在")
        if ROLE_RANK[role] < ROLE_RANK[minimum]:
            raise PermissionError_(f"权限不足：需要 {minimum} 及以上角色")
        return str(role)

    # ===== 成员信息聚合（端点层用） =====

    async def member_profiles(
        self, actor_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> list[dict[str, object]] | None:
        """成员列表 + 用户名/邮箱（端点层直接序列化用）。非成员返回 None。"""
        async with self._session_factory() as session:
            if await self.get_role(actor_id, workspace_id) is None:
                return None
            rows = await session.execute(
                select(WorkspaceMember, User)
                .join(User, User.id == WorkspaceMember.user_id)
                .where(WorkspaceMember.workspace_id == workspace_id)
                .order_by(WorkspaceMember.joined_at.asc(), WorkspaceMember.id.asc())
            )
            return [
                {
                    "user_id": member.user_id,
                    "username": user.username,
                    "email": user.email,
                    "role": member.role,
                    "joined_at": member.joined_at,
                }
                for member, user in rows.all()
            ]

    async def workspace_ids_for_user(self, user_id: uuid.UUID) -> Sequence[uuid.UUID]:
        """我所属的全部工作区 id（任务可见性查询用）。"""
        async with self._session_factory() as session:
            rows = await session.execute(
                select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user_id)
            )
            return [row[0] for row in rows.all()]

    # ===== 任务创建 / 批注权限 =====

    #: 能创建任务的最低角色。**viewer 不在其中**：导师常见用法是
    #: 「只看 + 批注」，给写权限会带来「导师误建任务消耗学生配额」的问题。
    TASK_CREATE_MIN_ROLE = "member"

    async def can_create_task(self, user_id: uuid.UUID, workspace_id: uuid.UUID) -> bool:
        """能否在该工作区创建任务（非成员 / viewer → False）。"""
        role = await self.get_role(user_id, workspace_id)
        if role is None:
            return False
        return ROLE_RANK[role] >= ROLE_RANK[self.TASK_CREATE_MIN_ROLE]

    async def can_annotate(self, user_id: uuid.UUID, workspace_id: uuid.UUID) -> bool:
        """能否批注。**viewer 也可以**——导师的核心用法就是只读 + 批注。"""
        return await self.get_role(user_id, workspace_id) is not None
