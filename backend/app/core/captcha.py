"""注册人机验证：ALTCHA 自托管 PoW（2026-11-13）。

## 选型结论（为什么不是 Turnstile / reCAPTCHA）

| 方案 | 大陆可用性 | 结论 |
| --- | --- | --- |
| Cloudflare Turnstile | **官方文档明确不支持中国大陆**；大陆探针实测 60s 加载不完 | ❌ 会把国内用户锁在注册页外 |
| reCAPTCHA | Google 域名被墙 | ❌ |
| hCaptcha | 免费版国内不稳；Enterprise 才有区域端点 | ❌ |
| **ALTCHA** | 挑战在**本后端**生成/校验，零第三方请求 | ✅ 采用 |

## 防的是谁、防不住谁（写清楚，不夸大）

- **防住**：无头脚本直接打 `/auth/register`、低成本的批量建号、简单重放；
- **防不住**：愿意为每次注册付算力/真人的定向攻击（PoW 只是把成本从「一次 HTTP」
  抬到「数万次 KDF」，配合 IP 限流 + 账号锁定 + 邮箱验证门禁四层叠加）；
- **不做人类判断**：没有行为指纹（那也是隐私问题），也不该做——本产品的注册
  只需挡住「无限免费建号」，不需要风控画像。

## 关键机制

1. **无状态挑战**：HMAC-SHA256 签名，不落库。服务端只存「已用 nonce」。
2. **IP 指纹绑定**：`sha256(ip)[:16]` 嵌进挑战的 `data.ip`。同一 payload 换 IP 即失效，
   防「一个人解一次、批量注册」。
3. **一次性**：库的 `verify_solution` 只验数学正确性（同一 payload 可反复通过），
   所以本模块按 `nonce` 做 `SET NX`：**第一次成功即占用**，TTL = 挑战有效期。
4. **fail-open**：nonce 存储故障时放行并告警——缓存挂了不能没人能注册
   （与限流/登录退避同一原则）。
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Protocol

from altcha import Payload, create_challenge, verify_solution

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 单机实现最多跟踪的 nonce 数（防内存被刷爆）
_MAX_TRACKED_NONCES = 20_000


def ip_fingerprint(ip: str) -> str:
    """IP 指纹：只存哈希前缀，不把原始 IP 写进挑战/日志。

    取 16 位十六进制（64 bit）——碰撞概率对「注册挑战」足够低，
    且比完整哈希短，payload 更小。
    """
    return hashlib.sha256((ip or "unknown").encode("utf-8")).hexdigest()[:16]


def client_ip_of(request: Any) -> str:
    """真实客户端 IP：复用限流中间件的解析（`X-Real-IP` 优先，非法值退回 socket）。

    抓包自己写一份的下场：生产在 nginx 后 `request.client.host` 全是容器内网 IP，
    所有用户的挑战绑到同一个指纹 → 「一处解、全站用」，等于没绑。
    """
    from app.api.rate_limit_middleware import _client_identity

    identity = _client_identity(request, trust_proxy=settings.rate_limit.trust_proxy)
    return identity.split(":", 1)[1] if ":" in identity else identity


class NonceStore(Protocol):
    """「一次性 nonce」存储。`consume` 返回 True 表示首次使用（可放行）。"""

    async def consume(self, nonce: str, *, ttl_seconds: int) -> bool: ...


class InMemoryNonceStore:
    """单进程实现（测试 / Redis 不可用时的降级）。

    进程重启丢记录 → 挑战在 TTL 内可被重放一次；生产用 Redis 实现。
    """

    def __init__(self, clock: Any = None) -> None:
        self._clock = clock or time.monotonic
        self._entries: dict[str, float] = {}

    def _evict(self, now: float) -> None:
        if len(self._entries) < _MAX_TRACKED_NONCES:
            return
        self._entries = {k: v for k, v in self._entries.items() if v > now}

    async def consume(self, nonce: str, *, ttl_seconds: int) -> bool:
        now = self._clock()
        self._evict(now)
        if self._entries.get(nonce, 0) > now:
            return False
        self._entries[nonce] = now + max(1, ttl_seconds)
        return True


class RedisNonceStore:
    """Redis 实现：`SET key 1 NX EX ttl` —— 原子占用，多副本共享。"""

    def __init__(self, *, redis_client: Any, key_prefix: str = "captcha:used") -> None:
        self._redis = redis_client
        self._prefix = key_prefix

    async def consume(self, nonce: str, *, ttl_seconds: int) -> bool:
        stored = await self._redis.set(
            f"{self._prefix}:{nonce}", "1", nx=True, ex=max(1, int(ttl_seconds))
        )
        return bool(stored)


class CaptchaService:
    """挑战签发与校验。挂在 `app.state.captcha`（与 login_throttle 同样的惰性初始化）。"""

    def __init__(self, *, nonces: NonceStore) -> None:
        self._nonces = nonces

    @property
    def enabled(self) -> bool:
        return bool(settings.captcha.enabled)

    def create_challenge(self, client_ip: str) -> dict[str, Any]:
        """签发挑战（ALTCHA v2 协议：`{parameters, signature}`）。

        未启用时返回 `{"enabled": False}`——前端据此决定是否渲染验证组件。
        """
        if not self.enabled:
            return {"enabled": False}

        data: dict[str, Any] = {}
        if settings.captcha.bind_ip:
            data["ip"] = ip_fingerprint(client_ip)

        challenge = create_challenge(
            settings.captcha.algorithm,
            int(settings.captcha.cost),
            hmac_secret=settings.captcha.hmac_secret,
            expires_at=int(time.time()) + int(settings.captcha.ttl_seconds),
            data=data or None,
        )
        payload = challenge.to_dict()
        payload["enabled"] = True
        return payload

    async def verify(self, payload: str | None, client_ip: str) -> tuple[bool, str]:
        """校验注册提交的 ALTCHA payload。

        返回 `(ok, reason)`；reason 用于日志与前端提示（不含敏感信息）：
        `disabled` / `missing` / `malformed` / `invalid_signature` /
        `invalid_solution` / `expired` / `ip_mismatch` / `nonce_used` / `ok`。
        """
        if not self.enabled:
            return True, "disabled"
        if not payload:
            return False, "missing"

        try:
            parsed = Payload.from_base64(payload)
            payload_dict = parsed.to_dict()
        except Exception:
            return False, "malformed"

        result = verify_solution(payload, settings.captcha.hmac_secret)
        if not result.verified:
            if result.expired:
                return False, "expired"
            if result.invalid_signature:
                return False, "invalid_signature"
            return False, "invalid_solution"

        parameters = (payload_dict.get("challenge") or {}).get("parameters") or {}

        if settings.captcha.bind_ip:
            expected = ip_fingerprint(client_ip)
            actual = (parameters.get("data") or {}).get("ip")
            if actual != expected:
                return False, "ip_mismatch"

        nonce = parameters.get("nonce")
        if not nonce:
            return False, "malformed"

        try:
            first_use = await self._nonces.consume(
                nonce, ttl_seconds=int(settings.captcha.ttl_seconds)
            )
        except Exception as exc:  # 存储故障 → fail-open（记告警，不挡注册）
            logger.warning("captcha_nonce_store_failed", error=str(exc))
            return True, "ok_fail_open"

        if not first_use:
            return False, "nonce_used"
        return True, "ok"


def _build_redis_nonce_store() -> NonceStore | None:
    """构造专用 Redis 客户端（短超时，与限流中间件同款）。

    注意：`from_url` 不建立连接（惰性），所以这里成功不代表 Redis 可用；
    真正不可用时由 `consume()` 抛错走 fail-open。
    """
    if settings.rate_limit.backend == "memory":
        return None
    import redis.asyncio as aioredis

    from app.core.rate_limit import InMemoryRateLimiter  # noqa: F401  确保模块已加载

    client = aioredis.from_url(
        settings.redis.url,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
        decode_responses=False,
    )
    return RedisNonceStore(redis_client=client)


def build_captcha_service() -> CaptchaService:
    """按当前配置构造服务：能用 Redis 用 Redis，否则退回内存（记 warning）。

    返回值应缓存在 `app.state.captcha`（见 auth 端点的 lazy resolve），
    否则每个请求都会新建一个 Redis 客户端。
    """
    store: NonceStore | None = None

    if settings.captcha.enabled:
        try:
            store = _build_redis_nonce_store()
        except Exception as exc:
            logger.warning("captcha_redis_unavailable", error=str(exc))

    if store is None:
        store = InMemoryNonceStore()
        if settings.captcha.enabled:
            logger.warning("captcha_using_in_memory_nonce_store")

    return CaptchaService(nonces=store)
