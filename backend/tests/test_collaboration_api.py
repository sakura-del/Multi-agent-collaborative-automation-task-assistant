"""工作区与批注端点测试（Phase 2 · 团队工作区 Task 4/5）。

## 本文件锁定 HTTP 契约

1. 状态码映射（404 越权 / 403 角色不足 / 409 席位满 / 400 参数错）；
2. 响应字段（前端依赖，改字段名会直接破坏看板）；
3. 邀请码**只在创建时返回一次**（列表不返回）；
4. 段落批注的 `anchor_text` → 服务端算指纹。

## 为什么不复用 service 测试

service 测试验证业务规则；端点测试验证**HTTP 契约**。两者会分别演化：
service 改内部实现不该影响端点测试，端点改状态码不该影响 service 测试。
混在一起会让「改一处、红一片」，测试失去定位价值。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.db.models  # noqa: F401
from app.api.v1.endpoints import annotations as annotations_endpoint
from app.api.v1.endpoints import workspaces as workspaces_endpoint
from app.db.base import Base
from app.db.models.task import Task
from app.db.models.user import User
from app.db.session import get_db


@pytest.fixture
def api() -> Any:
    """独立 app：内存 SQLite + 可切换当前用户。

    `current_user_holder` 是一个可变容器：测试通过改 `holder["user"]`
    切换身份，避免为每个身份重建 app（重建会丢内存库数据）。
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _create_all() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())

    holder: dict[str, User | None] = {"user": None}

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    async def _current_user() -> User:
        user = holder["user"]
        assert user is not None, "测试必须先调用 api.set_user()"
        return user

    application = FastAPI()
    application.include_router(workspaces_endpoint.router, prefix="/api/v1")
    application.include_router(annotations_endpoint.router, prefix="/api/v1")
    application.dependency_overrides[get_db] = _get_db
    for module in (workspaces_endpoint, annotations_endpoint):
        application.dependency_overrides[module.get_current_active_user] = _current_user

    application.state.factory = factory
    application.state.holder = holder

    async def _make_user(name: str) -> User:
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

    application.state.make_user = _make_user

    def _set_user(user: User) -> None:
        holder["user"] = user

    application.state.set_user = _set_user
    application.state.client = TestClient(application)
    return application


@pytest.fixture
def owner(api) -> User:
    user = asyncio.run(api.state.make_user("api_owner"))
    api.state.set_user(user)
    return user


@pytest.fixture
def other(api) -> User:
    return asyncio.run(api.state.make_user("api_other"))


def _client(api) -> TestClient:
    return api.state.client


class TestWorkspaceEndpoints:
    def test_create_and_list(self, api, owner) -> None:
        client = _client(api)
        resp = client.post(
            "/api/v1/workspaces",
            json={"name": "研究组", "slug": "api-ws1", "seat_limit": 5},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "研究组"
        assert body["seat_used"] == 1, "owner 自动占一个席位"
        assert body["my_role"] == "owner"
        assert set(body) >= {
            "workspace_id",
            "name",
            "slug",
            "owner_id",
            "seat_limit",
            "seat_used",
            "my_role",
        }

        listed = client.get("/api/v1/workspaces").json()
        assert len(listed) == 1
        assert listed[0]["slug"] == "api-ws1"

    def test_slug_conflict_returns_409(self, api, owner) -> None:
        client = _client(api)
        client.post("/api/v1/workspaces", json={"name": "A", "slug": "dup-slug", "seat_limit": 5})
        resp = client.post(
            "/api/v1/workspaces", json={"name": "B", "slug": "dup-slug", "seat_limit": 5}
        )
        assert resp.status_code == 409, resp.text

    def test_invalid_slug_returns_422(self, api, owner) -> None:
        """slug 格式由 Pydantic 校验（大写/下划线都不允许）。"""
        client = _client(api)
        resp = client.post(
            "/api/v1/workspaces", json={"name": "A", "slug": "Bad_Slug", "seat_limit": 5}
        )
        assert resp.status_code == 422

    def test_non_member_gets_404(self, api, owner, other) -> None:
        client = _client(api)
        workspace_id = client.post(
            "/api/v1/workspaces", json={"name": "A", "slug": "api-ws2", "seat_limit": 5}
        ).json()["workspace_id"]

        api.state.set_user(other)
        assert client.get(f"/api/v1/workspaces/{workspace_id}").status_code == 404
        assert client.get(f"/api/v1/workspaces/{workspace_id}/members").status_code == 404

    def test_member_cannot_rename_returns_403(self, api, owner, other) -> None:
        client = _client(api)
        created = client.post(
            "/api/v1/workspaces", json={"name": "A", "slug": "api-ws3", "seat_limit": 5}
        ).json()
        invite = client.post(
            f"/api/v1/workspaces/{created['workspace_id']}/invites", json={"role": "member"}
        ).json()

        api.state.set_user(other)
        client.post("/api/v1/workspaces/accept", json={"code": invite["code"]})
        resp = client.patch(f"/api/v1/workspaces/{created['workspace_id']}", json={"name": "改名"})
        assert resp.status_code == 403, resp.text


class TestInviteEndpoints:
    def test_invite_returns_code_once(self, api, owner, other) -> None:
        client = _client(api)
        created = client.post(
            "/api/v1/workspaces", json={"name": "A", "slug": "api-inv1", "seat_limit": 5}
        ).json()
        resp = client.post(
            f"/api/v1/workspaces/{created['workspace_id']}/invites", json={"role": "viewer"}
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["code"], "创建时必须返回码"
        assert body["role"] == "viewer"
        assert set(body) >= {"invite_id", "code", "role", "expires_at", "workspace_id"}

        # 成员列表不返回邀请码（一次性凭证不能重复读取）
        members = client.get(f"/api/v1/workspaces/{created['workspace_id']}/members").json()
        assert all("code" not in member for member in members)

    def test_accept_invite(self, api, owner, other) -> None:
        client = _client(api)
        created = client.post(
            "/api/v1/workspaces", json={"name": "A", "slug": "api-inv2", "seat_limit": 5}
        ).json()
        invite = client.post(
            f"/api/v1/workspaces/{created['workspace_id']}/invites", json={"role": "member"}
        ).json()

        api.state.set_user(other)
        resp = client.post("/api/v1/workspaces/accept", json={"code": invite["code"]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["my_role"] == "member"

    def test_seat_full_returns_409(self, api, owner, other) -> None:
        """席位满 → 409（需要扩容，不是改参数能解决）。"""
        client = _client(api)
        created = client.post(
            "/api/v1/workspaces", json={"name": "A", "slug": "api-inv3", "seat_limit": 1}
        ).json()
        invite = client.post(
            f"/api/v1/workspaces/{created['workspace_id']}/invites", json={"role": "member"}
        ).json()

        api.state.set_user(other)
        resp = client.post("/api/v1/workspaces/accept", json={"code": invite["code"]})
        assert resp.status_code == 409, resp.text

    def test_unknown_code_returns_400(self, api, owner) -> None:
        client = _client(api)
        resp = client.post("/api/v1/workspaces/accept", json={"code": "unknown-code-123"})
        assert resp.status_code == 400, resp.text

    def test_reused_code_returns_400(self, api, owner, other) -> None:
        client = _client(api)
        created = client.post(
            "/api/v1/workspaces", json={"name": "A", "slug": "api-inv4", "seat_limit": 5}
        ).json()
        invite = client.post(
            f"/api/v1/workspaces/{created['workspace_id']}/invites", json={"role": "member"}
        ).json()

        api.state.set_user(other)
        assert (
            client.post("/api/v1/workspaces/accept", json={"code": invite["code"]}).status_code
            == 200
        )
        third = asyncio.run(api.state.make_user("api_third"))
        api.state.set_user(third)
        resp = client.post("/api/v1/workspaces/accept", json={"code": invite["code"]})
        assert resp.status_code == 400, "一次性邀请码第二次使用必须被拒"


class TestAnnotationEndpoints:
    def _task(self, api, user: User, *, workspace_id=None, output: str = "") -> uuid.UUID:
        factory = api.state.factory

        async def _create() -> uuid.UUID:
            async with factory() as session:
                task = Task(
                    user_id=user.id,
                    title="T",
                    description="d",
                    status="completed",
                    workspace_id=workspace_id,
                    final_output=output,
                )
                session.add(task)
                await session.commit()
                await session.refresh(task)
                return task.id

        return asyncio.run(_create())

    def test_create_and_list_task_level(self, api, owner) -> None:
        client = _client(api)
        task_id = self._task(api, owner)

        resp = client.post(
            f"/api/v1/tasks/{task_id}/annotations",
            json={"anchor_type": "task", "content": "整体结构可以"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["anchor_type"] == "task"
        assert body["anchor_key"] is None
        assert set(body) >= {
            "annotation_id",
            "task_id",
            "author_id",
            "anchor_type",
            "anchor_key",
            "content",
            "resolved_at",
            "created_at",
        }

        listed = client.get(f"/api/v1/tasks/{task_id}/annotations").json()
        assert len(listed) == 1

    def test_paragraph_annotation_uses_anchor_text(self, api, owner) -> None:
        """前端只传段落原文，指纹由服务端算。"""
        client = _client(api)
        task_id = self._task(api, owner, output="第一段。\n\n第二段。\n")

        resp = client.post(
            f"/api/v1/tasks/{task_id}/annotations",
            json={
                "anchor_type": "paragraph",
                "anchor_text": "  第二段。  ",
                "content": "这段要改",
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["anchor_key"], "服务端必须算出指纹"

    def test_empty_content_returns_422(self, api, owner) -> None:
        """Pydantic 的 min_length 拦在前面（比服务层 400 更早）。"""
        client = _client(api)
        task_id = self._task(api, owner)
        resp = client.post(
            f"/api/v1/tasks/{task_id}/annotations",
            json={"anchor_type": "task", "content": ""},
        )
        assert resp.status_code == 422

    def test_paragraph_without_text_returns_400(self, api, owner) -> None:
        client = _client(api)
        task_id = self._task(api, owner)
        resp = client.post(
            f"/api/v1/tasks/{task_id}/annotations",
            json={"anchor_type": "paragraph", "content": "x"},
        )
        assert resp.status_code == 400, resp.text

    def test_resolve_and_reopen(self, api, owner) -> None:
        client = _client(api)
        task_id = self._task(api, owner)
        annotation_id = client.post(
            f"/api/v1/tasks/{task_id}/annotations",
            json={"anchor_type": "task", "content": "x"},
        ).json()["annotation_id"]

        resolved = client.post(f"/api/v1/tasks/{task_id}/annotations/{annotation_id}/resolve")
        assert resolved.status_code == 200
        assert resolved.json()["resolved_at"] is not None

        reopened = client.post(f"/api/v1/tasks/{task_id}/annotations/{annotation_id}/reopen")
        assert reopened.json()["resolved_at"] is None

    def test_paragraph_matches_endpoint(self, api, owner) -> None:
        client = _client(api)
        task_id = self._task(api, owner, output="第一段。\n\n第二段。\n")
        client.post(
            f"/api/v1/tasks/{task_id}/annotations",
            json={
                "anchor_type": "paragraph",
                "anchor_text": "第二段。",
                "content": "改这里",
            },
        )

        resp = client.get(f"/api/v1/tasks/{task_id}/annotations/paragraph-matches")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["matched"] is True
        assert rows[0]["paragraph_index"] == 1
        assert set(rows[0]) >= {
            "annotation_id",
            "fingerprint",
            "content",
            "author_id",
            "matched",
            "paragraph_index",
            "resolved",
        }

    def test_delete_own_annotation(self, api, owner) -> None:
        client = _client(api)
        task_id = self._task(api, owner)
        annotation_id = client.post(
            f"/api/v1/tasks/{task_id}/annotations",
            json={"anchor_type": "task", "content": "x"},
        ).json()["annotation_id"]

        resp = client.delete(f"/api/v1/tasks/{task_id}/annotations/{annotation_id}")
        assert resp.status_code == 204
        assert client.get(f"/api/v1/tasks/{task_id}/annotations").json() == []

    def test_outsider_cannot_see_annotations(self, api, owner, other) -> None:
        client = _client(api)
        task_id = self._task(api, owner)
        client.post(
            f"/api/v1/tasks/{task_id}/annotations",
            json={"anchor_type": "task", "content": "x"},
        )

        api.state.set_user(other)
        resp = client.get(f"/api/v1/tasks/{task_id}/annotations")
        assert resp.status_code == 404, "越权必须是 404（不泄漏任务存在性）"

    def test_workspace_member_can_annotate(self, api, owner, other) -> None:
        """工作区成员（含 viewer）能对共享任务批注。"""
        client = _client(api)
        created = client.post(
            "/api/v1/workspaces", json={"name": "A", "slug": "api-an1", "seat_limit": 5}
        ).json()
        invite = client.post(
            f"/api/v1/workspaces/{created['workspace_id']}/invites", json={"role": "viewer"}
        ).json()
        task_id = self._task(api, owner, workspace_id=uuid.UUID(created["workspace_id"]))

        api.state.set_user(other)
        client.post("/api/v1/workspaces/accept", json={"code": invite["code"]})
        resp = client.post(
            f"/api/v1/tasks/{task_id}/annotations",
            json={"anchor_type": "task", "content": "导师批注"},
        )
        assert resp.status_code == 201, resp.text
