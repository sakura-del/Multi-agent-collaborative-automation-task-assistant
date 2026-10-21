"""批注服务测试（Phase 2 · 团队工作区 Task 4）。

## 本文件锁定

1. **段落指纹锚点**：同段落稳定、不同段落不同、首尾空白归一化；
2. **权限矩阵**：viewer 可写批注（导师核心用法）、删除他人批注需 admin+；
3. **已解决状态**：标记后不删除（导师要能看到历史意见）；
4. **越权**：非成员对工作区任务批注 → 404 语义；
5. **失配检测**：段落被改写后，批注标记为「原文已变更」而不是错挂。

## 为什么锚点测试要覆盖「首尾空白」

Markdown 渲染前后常有换行/缩进差异（列表项、引用块）。不归一化会让同一段落
算出两个指纹，批注随机失配 —— 这类 bug 在测试里最容易被忽略，因为
「用自己写死的字符串算指纹」永远一致。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.collaboration.services.annotation_service import (
    AnnotationError,
    AnnotationPermissionError,
    AnnotationService,
)
from app.collaboration.services.workspace_service import WorkspaceService
from app.db.base import Base
from app.db.models.annotation import paragraph_fingerprint
from app.db.models.task import Task
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


async def _task(factory, user_id, *, workspace_id=None, output: str = "") -> Task:
    async with factory() as session:
        task = Task(
            user_id=user_id,
            title="T",
            description="d",
            status="completed",
            workspace_id=workspace_id,
            final_output=output,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task


class TestFingerprint:
    def test_stable_for_same_text(self) -> None:
        assert paragraph_fingerprint("一段正文") == paragraph_fingerprint("一段正文")

    def test_differs_for_different_text(self) -> None:
        assert paragraph_fingerprint("段落 A") != paragraph_fingerprint("段落 B")

    def test_normalizes_surrounding_whitespace(self) -> None:
        """**关键**：Markdown 渲染前后常有空白差异，不归一化会随机失配。"""
        base = paragraph_fingerprint("一段正文")
        assert paragraph_fingerprint("  一段正文  ") == base
        assert paragraph_fingerprint("\n\n一段正文\n\n") == base
        assert paragraph_fingerprint("\t一段正文\t") == base

    def test_length_is_16_hex(self) -> None:
        fp = paragraph_fingerprint("x")
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)


class TestCreateAnnotation:
    @pytest.mark.asyncio
    async def test_task_level_annotation(self, factory) -> None:
        owner = await _user(factory, "an_owner")
        task = await _task(factory, owner.id)
        service = AnnotationService(factory, WorkspaceService(factory))

        annotation = await service.create(
            owner.id, task.id, anchor_type="task", content="整体结构可以"
        )

        assert annotation.anchor_type == "task"
        assert annotation.anchor_key is None
        assert annotation.content == "整体结构可以"

    @pytest.mark.asyncio
    async def test_paragraph_annotation_computes_fingerprint(self, factory) -> None:
        """传段落文本，服务自己算指纹（不让前端算，避免口径不一致）。"""
        owner = await _user(factory, "ap_owner")
        task = await _task(factory, owner.id)
        service = AnnotationService(factory, WorkspaceService(factory))

        annotation = await service.create(
            owner.id,
            task.id,
            anchor_type="paragraph",
            anchor_text="  这一段需要补页码  ",
            content="请补页码",
        )

        assert annotation.anchor_type == "paragraph"
        assert annotation.anchor_key == paragraph_fingerprint("这一段需要补页码")

    @pytest.mark.asyncio
    async def test_empty_content_rejected(self, factory) -> None:
        owner = await _user(factory, "ae_owner")
        task = await _task(factory, owner.id)
        service = AnnotationService(factory, WorkspaceService(factory))

        with pytest.raises(AnnotationError, match="不能为空"):
            await service.create(owner.id, task.id, anchor_type="task", content="   ")

    @pytest.mark.asyncio
    async def test_invalid_anchor_type_rejected(self, factory) -> None:
        owner = await _user(factory, "ai_owner")
        task = await _task(factory, owner.id)
        service = AnnotationService(factory, WorkspaceService(factory))

        with pytest.raises(AnnotationError, match="锚点类型"):
            await service.create(owner.id, task.id, anchor_type="nonsense", content="x")

    @pytest.mark.asyncio
    async def test_paragraph_requires_anchor_text(self, factory) -> None:
        owner = await _user(factory, "ar_owner")
        task = await _task(factory, owner.id)
        service = AnnotationService(factory, WorkspaceService(factory))

        with pytest.raises(AnnotationError, match="段落"):
            await service.create(owner.id, task.id, anchor_type="paragraph", content="x")


class TestPermissions:
    @pytest.mark.asyncio
    async def test_viewer_can_annotate(self, factory) -> None:
        """**导师核心场景**：只读角色必须能批注。"""
        owner = await _user(factory, "pv_owner")
        viewer = await _user(factory, "pv_viewer")
        ws_service = WorkspaceService(factory)
        workspace = await ws_service.create(owner.id, name="组", slug="an1", seat_limit=5)
        invite = await ws_service.create_invite(owner.id, workspace.id, role="viewer")
        await ws_service.accept_invite(viewer.id, invite.code)

        task = await _task(factory, owner.id, workspace_id=workspace.id)
        service = AnnotationService(factory, ws_service)

        annotation = await service.create(
            viewer.id, task.id, anchor_type="task", content="导师批注"
        )

        assert annotation.author_id == viewer.id

    @pytest.mark.asyncio
    async def test_non_member_cannot_annotate_workspace_task(self, factory) -> None:
        owner = await _user(factory, "pn_owner")
        outsider = await _user(factory, "pn_outsider")
        ws_service = WorkspaceService(factory)
        workspace = await ws_service.create(owner.id, name="组", slug="an2", seat_limit=5)
        task = await _task(factory, owner.id, workspace_id=workspace.id)
        service = AnnotationService(factory, ws_service)

        with pytest.raises(AnnotationError, match="不存在"):
            await service.create(outsider.id, task.id, anchor_type="task", content="x")

    @pytest.mark.asyncio
    async def test_member_can_delete_own_annotation(self, factory) -> None:
        owner = await _user(factory, "po_owner")
        member = await _user(factory, "po_member")
        ws_service = WorkspaceService(factory)
        workspace = await ws_service.create(owner.id, name="组", slug="an3", seat_limit=5)
        invite = await ws_service.create_invite(owner.id, workspace.id, role="member")
        await ws_service.accept_invite(member.id, invite.code)
        task = await _task(factory, owner.id, workspace_id=workspace.id)
        service = AnnotationService(factory, ws_service)
        annotation = await service.create(
            member.id, task.id, anchor_type="task", content="我的批注"
        )

        await service.delete(member.id, annotation.id)

        assert await service.list_for_task(member.id, task.id) == []

    @pytest.mark.asyncio
    async def test_member_cannot_delete_others_annotation(self, factory) -> None:
        owner = await _user(factory, "pd_owner")
        member = await _user(factory, "pd_member")
        ws_service = WorkspaceService(factory)
        workspace = await ws_service.create(owner.id, name="组", slug="an4", seat_limit=5)
        invite = await ws_service.create_invite(owner.id, workspace.id, role="member")
        await ws_service.accept_invite(member.id, invite.code)
        task = await _task(factory, owner.id, workspace_id=workspace.id)
        service = AnnotationService(factory, ws_service)
        owner_annotation = await service.create(
            owner.id, task.id, anchor_type="task", content="owner 的批注"
        )

        with pytest.raises(AnnotationPermissionError, match="权限"):
            await service.delete(member.id, owner_annotation.id)

    @pytest.mark.asyncio
    async def test_admin_can_delete_others_annotation(self, factory) -> None:
        owner = await _user(factory, "pa2_owner")
        admin = await _user(factory, "pa2_admin")
        member = await _user(factory, "pa2_member")
        ws_service = WorkspaceService(factory)
        workspace = await ws_service.create(owner.id, name="组", slug="an5", seat_limit=5)
        for user, role in ((admin, "admin"), (member, "member")):
            invite = await ws_service.create_invite(owner.id, workspace.id, role=role)
            await ws_service.accept_invite(user.id, invite.code)
        task = await _task(factory, owner.id, workspace_id=workspace.id)
        service = AnnotationService(factory, ws_service)
        member_annotation = await service.create(
            member.id, task.id, anchor_type="task", content="member 的批注"
        )

        await service.delete(admin.id, member_annotation.id)

        assert await service.list_for_task(admin.id, task.id) == []


class TestResolve:
    @pytest.mark.asyncio
    async def test_resolve_keeps_annotation(self, factory) -> None:
        """标记已解决**不删除**：导师要能看到历史意见。"""
        owner = await _user(factory, "rs_owner")
        task = await _task(factory, owner.id)
        service = AnnotationService(factory, WorkspaceService(factory))
        annotation = await service.create(
            owner.id, task.id, anchor_type="task", content="请补充局限性"
        )

        resolved = await service.resolve(owner.id, annotation.id)

        assert resolved.resolved_at is not None
        items = await service.list_for_task(owner.id, task.id)
        assert len(items) == 1, "已解决的批注仍应出现在列表里"

    @pytest.mark.asyncio
    async def test_resolve_is_idempotent(self, factory) -> None:
        owner = await _user(factory, "ri_owner")
        task = await _task(factory, owner.id)
        service = AnnotationService(factory, WorkspaceService(factory))
        annotation = await service.create(owner.id, task.id, anchor_type="task", content="x")

        first = await service.resolve(owner.id, annotation.id)
        second = await service.resolve(owner.id, annotation.id)

        assert first.resolved_at == second.resolved_at, "重复标记不应改变首次时间"

    @pytest.mark.asyncio
    async def test_reopen_clears_resolved_at(self, factory) -> None:
        owner = await _user(factory, "ro_owner2")
        task = await _task(factory, owner.id)
        service = AnnotationService(factory, WorkspaceService(factory))
        annotation = await service.create(owner.id, task.id, anchor_type="task", content="x")
        await service.resolve(owner.id, annotation.id)

        reopened = await service.reopen(owner.id, annotation.id)

        assert reopened.resolved_at is None


class TestAnchorMatching:
    @pytest.mark.asyncio
    async def test_matches_paragraphs_in_output(self, factory) -> None:
        """段落指纹能在产出里定位到对应段落。"""
        owner = await _user(factory, "am2_owner")
        output = "# 标题\n\n第一段正文。\n\n第二段正文。\n"
        task = await _task(factory, owner.id, output=output)
        service = AnnotationService(factory, WorkspaceService(factory))
        await service.create(
            owner.id,
            task.id,
            anchor_type="paragraph",
            anchor_text="第二段正文。",
            content="这段要改",
        )

        matched = await service.match_paragraphs(owner.id, task.id)

        assert matched, "应至少匹配到一个段落"
        target = paragraph_fingerprint("第二段正文。")
        hit = [m for m in matched if m["fingerprint"] == target]
        assert hit, "第二段必须被匹配上"
        assert hit[0]["matched"] is True

    @pytest.mark.asyncio
    async def test_reports_stale_when_paragraph_rewritten(self, factory) -> None:
        """**失配检测**：段落被改写后标记 `matched=False`，不静默错挂。"""
        owner = await _user(factory, "as_owner")
        task = await _task(factory, owner.id, output="原始段落内容。\n")
        service = AnnotationService(factory, WorkspaceService(factory))
        await service.create(
            owner.id,
            task.id,
            anchor_type="paragraph",
            anchor_text="原始段落内容。",
            content="改一下",
        )

        # 重跑：产出整体变了
        async with factory() as session:
            stored = await session.get(Task, task.id)
            assert stored is not None
            stored.final_output = "完全不同的新段落。\n"
            await session.commit()

        matched = await service.match_paragraphs(owner.id, task.id)

        stale = [m for m in matched if m["matched"] is False]
        assert stale, "段落被改写后必须报告失配"
