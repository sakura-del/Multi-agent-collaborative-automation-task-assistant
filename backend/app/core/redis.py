"""Redis 异步工具集。

阶段1任务6：Redis 连接池 + 缓存装饰器 + 分布式锁 + 任务状态读写。

设计要点：
- 单例连接池：全应用共享，避免重复建连（内存优化）
- 异步：所有方法 async，不阻塞事件循环（并行优化）
- 分布式锁：SET NX EX + Lua 释放脚本，保证持锁者才能释放（线程安全）
- 缓存装饰器：JSON 序列化，key 基于函数参数哈希，避免重复计算（计算优化）
- 任务状态：用 Hash 结构存储，支持原子性多字段读写
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, TypeVar

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# Lua 脚本：释放锁时校验 token，只有持锁者能释放，避免误删他人锁
# KEYS[1]=lock_key  ARGV[1]=token
_RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""

T = TypeVar("T")


class RedisManager:
    """Redis 连接管理器：单例连接池 + 业务工具方法。"""

    _pool: aioredis.ConnectionPool | None = None
    _client: aioredis.Redis | None = None

    @classmethod
    def get_client(cls) -> aioredis.Redis:
        """获取 Redis 客户端单例。

        首次调用时创建连接池，后续复用，避免重复建连开销。
        """
        if cls._client is None:
            cls._pool = aioredis.ConnectionPool.from_url(
                settings.redis.url,
                max_connections=settings.redis.max_connections,
                decode_responses=True,  # 自动解码为 str，简化调用
            )
            cls._client = aioredis.Redis(connection_pool=cls._pool)
            logger.info("redis_pool_created", url=settings.redis.url)
        assert cls._client is not None
        return cls._client

    @classmethod
    async def close(cls) -> None:
        """关闭连接池，释放资源。应用关闭时调用。"""
        if cls._client is not None:
            await cls._client.aclose()
            cls._client = None
        if cls._pool is not None:
            await cls._pool.disconnect()
            cls._pool = None
            logger.info("redis_pool_closed")

    @classmethod
    async def ping(cls) -> bool:
        """健康检查：测试连接可用性。"""
        try:
            return bool(await cls.get_client().ping())
        except Exception:
            return False


# 模块级便捷访问：from app.core.redis import redis_client
def get_redis() -> aioredis.Redis:
    """获取 Redis 客户端（单例）。"""
    return RedisManager.get_client()


# ===== 缓存装饰器 =====


def _build_cache_key(prefix: str, func: Callable[..., Any], args: tuple, kwargs: dict) -> str:
    """基于函数参数生成缓存 key。

    用 MD5 摘要参数，避免长 key 与特殊字符问题。
    不跳过任何位置参数：方法的 self/cls 通过 repr 参与哈希，
    避免 self 不同时缓存误命中。
    """
    key_parts = [func.__module__, func.__qualname__]
    # 全部位置参数参与 key 计算
    key_parts.append(repr(args))
    key_parts.append(repr(sorted(kwargs.items())))
    digest = hashlib.md5("|".join(key_parts).encode()).hexdigest()[:16]
    return f"cache:{prefix}:{digest}"


def cached(
    key_prefix: str,
    ttl: int | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """异步函数缓存装饰器。

    用法：
        @cached("user_profile", ttl=300)
        async def get_user_profile(user_id): ...

    命中缓存时直接返回反序列化结果，避免重复计算与 DB 查询。
    ttl 为 None 时使用配置默认 TTL。
    """
    cache_ttl = ttl if ttl is not None else settings.redis.default_ttl

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            client = get_redis()
            key = _build_cache_key(key_prefix, func, args, kwargs)
            try:
                hit = await client.get(key)
                if hit is not None:
                    return json.loads(hit)
            except Exception:
                # Redis 不可用时降级为直接执行，不阻断业务
                logger.warning("cache_read_failed", key=key)
                return await func(*args, **kwargs)

            result = await func(*args, **kwargs)
            try:
                await client.setex(key, cache_ttl, json.dumps(result, default=str))
            except Exception:
                logger.warning("cache_write_failed", key=key)
            return result

        return wrapper

    return decorator


# ===== 分布式锁 =====


class LockAcquisitionError(Exception):
    """获取分布式锁失败。"""


@asynccontextmanager
async def distributed_lock(
    key: str,
    ttl: int = 30,
    *,
    retry: int = 3,
    retry_interval: float = 0.1,
) -> AsyncIterator[str]:
    """分布式锁（基于 Redis SET NX EX + Lua 释放）。

    用法：
        async with distributed_lock("task:123", ttl=30) as token:
            # 临界区操作

    保证：
    - 互斥：SET NX 确保同一 key 只有一个持有者
    - 防死锁：TTL 到期自动释放
    - 安全释放：Lua 脚本校验 token，只删自己的锁
    """
    client = get_redis()
    token = uuid.uuid4().hex
    lock_key = f"lock:{key}"

    acquired = False
    for _ in range(retry):
        if await client.set(lock_key, token, nx=True, ex=ttl):
            acquired = True
            break
        await asyncio.sleep(retry_interval)

    if not acquired:
        raise LockAcquisitionError(f"获取锁失败: {lock_key}")

    try:
        yield token
    finally:
        # Lua 脚本：只有 token 匹配才删除，避免误删他人持有的锁
        await client.eval(_RELEASE_LOCK_SCRIPT, 1, lock_key, token)


# ===== 任务状态快速读写 =====

# 任务状态 Hash 字段
_TASK_STATUS_KEY = "task:status:{task_id}"


async def set_task_status(
    task_id: str,
    status: str,
    *,
    extra: dict[str, str] | None = None,
    ttl: int = 3600,
) -> None:
    """写入任务状态到 Redis Hash，用于快速查询与实时推送。

    extra 可携带 agent_id / progress / message 等扩展字段。
    """
    client = get_redis()
    key = _TASK_STATUS_KEY.format(task_id=task_id)
    mapping: dict[str, str] = {"status": status}
    if extra:
        mapping.update({k: str(v) for k, v in extra.items()})
    # pipeline 批量执行 HSET + EXPIRE，减少网络往返
    pipe = client.pipeline()
    pipe.hset(key, mapping=mapping)
    pipe.expire(key, ttl)
    await pipe.execute()


async def get_task_status(task_id: str) -> dict[str, str]:
    """读取任务状态 Hash。不存在返回空字典。"""
    client = get_redis()
    key = _TASK_STATUS_KEY.format(task_id=task_id)
    return await client.hgetall(key)


async def delete_task_status(task_id: str) -> None:
    """删除任务状态（任务终态后清理）。"""
    client = get_redis()
    key = _TASK_STATUS_KEY.format(task_id=task_id)
    await client.delete(key)
