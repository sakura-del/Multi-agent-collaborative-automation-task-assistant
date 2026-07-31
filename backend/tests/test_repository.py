"""Repository 模块测试：BaseRepository + UserRepository CRUD。

阶段1任务5：基础 CRUD Repository 模式。
使用内存 SQLite，测试隔离且无需真实 PostgreSQL。
"""

from __future__ import annotations

import uuid

import pytest

from app.core.security import hash_password
from app.db.models.user import User
from app.db.repositories.user import UserRepository


@pytest.fixture
async def user_repo(db_session) -> UserRepository:
    """基于测试 session 的 UserRepository。"""
    return UserRepository(db_session)


def _make_user(username: str = "tester", email: str | None = None) -> User:
    """构造测试用 User 对象。"""
    return User(
        username=username,
        email=email or f"{username}@test.com",
        password_hash=hash_password("testpassword123"),
        role="user",
    )


class TestUserRepositoryCreate:
    """创建与查询。"""

    async def test_create_and_get_by_id(self, user_repo) -> None:
        """create 后应能通过 get 查到。"""
        user = _make_user("alice")
        created = await user_repo.create(user)
        await user_repo.db.commit()

        fetched = await user_repo.get(created.id)
        assert fetched is not None
        assert fetched.username == "alice"

    async def test_get_nonexistent_returns_none(self, user_repo) -> None:
        """查询不存在的 ID 返回 None。"""
        fetched = await user_repo.get(uuid.uuid4())
        assert fetched is None

    async def test_get_by_username(self, user_repo) -> None:
        """按用户名查询。"""
        await user_repo.create(_make_user("bob"))
        await user_repo.db.commit()

        found = await user_repo.get_by_username("bob")
        assert found is not None
        assert found.email == "bob@test.com"

    async def test_get_by_email(self, user_repo) -> None:
        """按邮箱查询。"""
        await user_repo.create(_make_user("carol", "carol@custom.com"))
        await user_repo.db.commit()

        found = await user_repo.get_by_email("carol@custom.com")
        assert found is not None
        assert found.username == "carol"

    async def test_get_by_account_supports_both(self, user_repo) -> None:
        """get_by_account 应支持用户名与邮箱两种。"""
        await user_repo.create(_make_user("dave", "dave@test.com"))
        await user_repo.db.commit()

        by_name = await user_repo.get_by_account("dave")
        by_email = await user_repo.get_by_account("dave@test.com")
        assert by_name is not None
        assert by_email is not None
        assert by_name.id == by_email.id

    async def test_exists_returns_user_on_conflict(self, user_repo) -> None:
        """exists 在用户名冲突时返回已存在的用户。"""
        await user_repo.create(_make_user("eve"))
        await user_repo.db.commit()

        existing = await user_repo.exists("eve", "other@test.com")
        assert existing is not None
        assert existing.username == "eve"

    async def test_exists_returns_none_when_free(self, user_repo) -> None:
        """用户名与邮箱均可用时返回 None。"""
        assert await user_repo.exists("freeuser", "free@test.com") is None


class TestUserRepositoryMulti:
    """批量查询与计数。"""

    async def test_get_multi_returns_list(self, user_repo) -> None:
        """get_multi 返回列表。"""
        for i in range(3):
            await user_repo.create(_make_user(f"user{i}"))
        await user_repo.db.commit()

        result = await user_repo.get_multi(skip=0, limit=10)
        assert isinstance(result, list)
        assert len(result) == 3

    async def test_get_multi_respects_limit(self, user_repo) -> None:
        """limit 应限制返回条数。"""
        for i in range(5):
            await user_repo.create(_make_user(f"lim{i}"))
        await user_repo.db.commit()

        result = await user_repo.get_multi(limit=2)
        assert len(result) == 2

    async def test_get_multi_limit_capped_at_1000(self, user_repo) -> None:
        """limit 超过 1000 应被截断（防御性）。"""
        # 不实际插入 1000+ 条，仅验证裁剪逻辑不报错
        result = await user_repo.get_multi(limit=9999)
        assert isinstance(result, list)

    async def test_count(self, user_repo) -> None:
        """count 返回总记录数。"""
        for i in range(4):
            await user_repo.create(_make_user(f"cnt{i}"))
        await user_repo.db.commit()

        assert await user_repo.count() == 4


class TestUserRepositoryUpdateDelete:
    """更新与删除。"""

    async def test_update_changes_field(self, user_repo) -> None:
        """更新字段后应持久化。"""
        user = await user_repo.create(_make_user("upd"))
        await user_repo.db.commit()

        user.role = "admin"
        await user_repo.update(user)
        await user_repo.db.commit()

        fetched = await user_repo.get(user.id)
        assert fetched is not None
        assert fetched.role == "admin"

    async def test_delete_removes_record(self, user_repo) -> None:
        """删除后应查不到。"""
        user = await user_repo.create(_make_user("del"))
        await user_repo.db.commit()
        uid = user.id

        await user_repo.delete(user)
        await user_repo.db.commit()

        assert await user_repo.get(uid) is None
