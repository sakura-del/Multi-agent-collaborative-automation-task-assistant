"""Redis 工具集测试：缓存装饰器 + 分布式锁 + 任务状态。

阶段1任务6：用 fakeredis 替代真实 Redis，保证测试隔离。
"""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from app.core import redis as redis_module


@pytest.fixture
def fake_redis(monkeypatch):
    """用 fakeredis 替换 RedisManager 的单例客户端。"""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)

    # 强制 RedisManager 返回 fake 客户端
    monkeypatch.setattr(redis_module.RedisManager, "_client", fake)
    monkeypatch.setattr(redis_module.RedisManager, "_pool", None)
    return fake


class TestRedisManager:
    """连接管理器。"""

    async def test_ping_success(self, fake_redis) -> None:
        """ping 应返回 True。"""
        assert await redis_module.RedisManager.ping() is True

    async def test_get_client_singleton(self, fake_redis) -> None:
        """get_client 应返回同一实例。"""
        c1 = redis_module.get_redis()
        c2 = redis_module.get_redis()
        assert c1 is c2


class TestCacheDecorator:
    """缓存装饰器。"""

    async def test_cache_miss_then_hit(self, fake_redis) -> None:
        """首次执行函数，第二次命中缓存。"""
        call_count = 0

        @redis_module.cached("test", ttl=60)
        async def expensive(x: int) -> dict:
            nonlocal call_count
            call_count += 1
            return {"result": x * 2}

        r1 = await expensive(5)
        r2 = await expensive(5)

        assert r1 == {"result": 10}
        assert r2 == {"result": 10}
        assert call_count == 1  # 第二次命中缓存，不执行函数

    async def test_cache_different_args_different_keys(self, fake_redis) -> None:
        """不同参数应分别缓存。"""
        calls = []

        @redis_module.cached("args", ttl=60)
        async def func(x: int) -> int:
            calls.append(x)
            return x

        await func(1)
        await func(2)
        await func(1)  # 命中缓存

        assert len(calls) == 2  # 1 和 2 各执行一次

    async def test_cache_redis_down_degrades_gracefully(self, monkeypatch) -> None:
        """Redis 异常时降级为直接执行函数，不阻断业务。"""

        async def failing_get(*_a, **_kw):
            raise ConnectionError("redis down")

        # 构造一个会抛异常的 fake client
        bad_fake = fakeredis.aioredis.FakeRedis()
        monkeypatch.setattr(redis_module.RedisManager, "_client", bad_fake)
        monkeypatch.setattr(bad_fake, "get", failing_get)

        @redis_module.cached("down", ttl=10)
        async def func() -> str:
            return "computed"

        # 应正常返回，不抛异常
        assert await func() == "computed"


class TestDistributedLock:
    """分布式锁。"""

    async def test_lock_acquire_and_release(self, fake_redis) -> None:
        """获取锁后应能正常释放。"""
        async with redis_module.distributed_lock("resource1", ttl=5) as token:
            assert token  # 非空 token
            # 持锁期间 key 存在
            assert await fake_redis.get("lock:resource1") == token
        # 释放后 key 不存在
        assert await fake_redis.get("lock:resource1") is None

    async def test_lock_mutual_exclusion(self, fake_redis) -> None:
        """同一 key 第二个获取应失败（retry=1 快速失败）。"""
        async with redis_module.distributed_lock("mutex", ttl=5, retry=1):
            with pytest.raises(redis_module.LockAcquisitionError):
                async with redis_module.distributed_lock("mutex", ttl=5, retry=1):
                    pass

    async def test_lock_release_only_by_owner(self, fake_redis) -> None:
        """释放锁时校验 token，非持锁者不能释放。"""
        # 手动设置一个不同 token 的锁
        await fake_redis.set("lock:manual", "other-token", ex=5)
        # 用正确 token 的上下文管理器获取（会失败因为已被占用）
        with pytest.raises(redis_module.LockAcquisitionError):
            async with redis_module.distributed_lock("manual", ttl=5, retry=1):
                pass
        # 锁仍在（未被误删）
        assert await fake_redis.get("lock:manual") == "other-token"


class TestTaskStatus:
    """任务状态读写。"""

    async def test_set_and_get_task_status(self, fake_redis) -> None:
        """写入后应能读回。"""
        await redis_module.set_task_status("task-123", "running")
        status = await redis_module.get_task_status("task-123")
        assert status["status"] == "running"

    async def test_set_task_status_with_extra(self, fake_redis) -> None:
        """extra 扩展字段应一并存储。"""
        await redis_module.set_task_status(
            "task-456",
            "completed",
            extra={"agent_id": "agent-1", "progress": "100"},
        )
        status = await redis_module.get_task_status("task-456")
        assert status["status"] == "completed"
        assert status["agent_id"] == "agent-1"
        assert status["progress"] == "100"

    async def test_get_nonexistent_returns_empty(self, fake_redis) -> None:
        """查询不存在的任务返回空字典。"""
        assert await redis_module.get_task_status("nonexistent") == {}

    async def test_delete_task_status(self, fake_redis) -> None:
        """删除后应查不到。"""
        await redis_module.set_task_status("task-del", "done")
        await redis_module.delete_task_status("task-del")
        assert await redis_module.get_task_status("task-del") == {}
