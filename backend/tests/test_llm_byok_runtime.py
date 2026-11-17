"""BYOK 运行时解析测试（Issue #15 · P0-2）。

## 本文件锁定

1. **用户 key 真的被用上**：本地假 OpenAI 服务收到的 `Authorization` 必须是
   凭据里的 key，且平台 key 一次都不能出现；
2. **失败不静默回退**：用户 key 401 时只发一次请求就抛错，**不能**偷偷换平台模型
   （否则平台替用户烧钱，而用户以为在用自己那把）；
3. **缓存按 `key_version` 失效**：换 key 后必须拿到新适配器；
4. **自定义端点解析到内网要拒绝**：创建期只能拦字面量，域名解析必须运行时再查一次；
5. **作用域优先于全局单例**：`RuntimeContainer.create_llm()` 在作用域内返回用户
   客户端，作用域外仍走平台客户端。
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest
from openai import AuthenticationError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import config
from app.core.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.db.models.llm_credential import LLMCredential
from app.db.models.user import User
from app.llm.byok import BYOKClient, CredentialLLMResolver
from app.llm.models import Message, MessageRole
from app.llm.scope import get_scoped_llm, use_llm_client
from app.orchestration.runtime import RuntimeContainer
from app.schemas.llm_credential import LLMCredentialCreate, LLMCredentialUpdate
from app.security.credential_cipher import CredentialCipher
from app.security.llm_credential_service import LLMCredentialService

USER_KEY = "sk-user-own-key-1234567890"
PLATFORM_KEY = "sk-platform-must-never-be-used"


@dataclass
class FakeOpenAI:
    """记录请求的假 OpenAI 服务。"""

    base_url: str = ""
    status: int = 200
    requests: list[dict[str, Any]] = field(default_factory=list)

    @property
    def auth_headers(self) -> list[str]:
        return [str(item["auth"]) for item in self.requests]


@pytest.fixture
def fake_openai() -> Iterator[FakeOpenAI]:
    state = FakeOpenAI()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            state.requests.append(
                {
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "headers": dict(self.headers),
                    "body": json.loads(raw or b"{}"),
                }
            )
            if state.status == 200:
                payload = json.dumps(
                    {
                        "id": "chatcmpl-test",
                        "object": "chat.completion",
                        "created": 0,
                        "model": "test-model",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "pong"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                    }
                ).encode()
                self.send_response(200)
            else:
                payload = json.dumps(
                    {"error": {"message": "invalid api key", "type": "invalid_request_error"}}
                ).encode()
                self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:  # 测试不要刷屏
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.base_url = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.fixture
def allow_local_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试用：放开 http 与私网（生产两项都是 false）。"""
    monkeypatch.setattr(config.settings.llm, "byok_allow_insecure_base_url", True)
    monkeypatch.setattr(config.settings.llm, "byok_allow_private_base_url", True)


async def _make_user(session: Any, name: str) -> User:
    user = User(username=name, email=f"{name}@example.com", password_hash="x", is_active=True)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _make_credential(
    session: Any,
    user: User,
    *,
    base_url: str,
    api_key: str = USER_KEY,
    model: str = "test-model",
    provider: str = "custom",
    context_window: int | None = None,
    max_output_tokens: int | None = None,
) -> Any:
    service = LLMCredentialService(cipher=CredentialCipher.from_settings())
    return await service.create(
        session,
        user.id,
        LLMCredentialCreate(
            name=f"cred-{uuid.uuid4().hex[:8]}",
            provider=provider,  # type: ignore[arg-type]
            model=model,
            api_key=api_key,
            base_url=base_url,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
        ),
    )


def _resolver(session_factory: Any | None = None) -> CredentialLLMResolver:
    return CredentialLLMResolver(
        cipher=CredentialCipher.from_settings(), session_factory=session_factory
    )


class TestResolve:
    async def test_uses_user_key_and_endpoint(
        self, db_session: Any, fake_openai: FakeOpenAI, allow_local_endpoints: None
    ) -> None:
        user = await _make_user(db_session, "byok_owner")
        record = await _make_credential(db_session, user, base_url=fake_openai.base_url)

        client = await _resolver().resolve(db_session, user.id, record.id)
        reply = await client.chat([Message(role=MessageRole.USER, content="ping")])

        assert reply.content == "pong"
        assert client.provider == "custom"
        assert client.model == "test-model"
        assert client.is_byok is True
        assert len(fake_openai.requests) == 1
        request = fake_openai.requests[0]
        assert request["auth"] == f"Bearer {USER_KEY}"
        assert request["path"] == "/v1/chat/completions"
        assert request["body"]["model"] == "test-model"
        assert request["body"]["messages"][0]["content"] == "ping"

    async def test_platform_key_never_sent(
        self,
        db_session: Any,
        fake_openai: FakeOpenAI,
        allow_local_endpoints: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(config.settings.llm, "openai_api_key", PLATFORM_KEY)
        monkeypatch.setattr(config.settings.llm, "deepseek_api_key", PLATFORM_KEY)
        user = await _make_user(db_session, "byok_platform_key")
        record = await _make_credential(db_session, user, base_url=fake_openai.base_url)

        client = await _resolver().resolve(db_session, user.id, record.id)
        await client.chat([Message(role=MessageRole.USER, content="hi")])

        serialized = json.dumps(fake_openai.requests, default=str)
        assert USER_KEY in serialized
        assert PLATFORM_KEY not in serialized

    async def test_auth_failure_does_not_fallback(
        self, db_session: Any, fake_openai: FakeOpenAI, allow_local_endpoints: None
    ) -> None:
        """401 只发一次、直接抛错：**绝不能**换成平台模型继续跑。"""
        fake_openai.status = 401
        user = await _make_user(db_session, "byok_401")
        record = await _make_credential(db_session, user, base_url=fake_openai.base_url)

        client = await _resolver().resolve(db_session, user.id, record.id)
        with pytest.raises(AuthenticationError):
            await client.chat([Message(role=MessageRole.USER, content="hi")])

        assert len(fake_openai.requests) == 1

    async def test_cross_user_is_404(
        self, db_session: Any, fake_openai: FakeOpenAI, allow_local_endpoints: None
    ) -> None:
        alice = await _make_user(db_session, "byok_alice")
        bob = await _make_user(db_session, "byok_bob")
        record = await _make_credential(db_session, bob, base_url=fake_openai.base_url)

        with pytest.raises(NotFoundException):
            await _resolver().resolve(db_session, alice.id, record.id)

    async def test_disabled_credential_rejected(
        self, db_session: Any, fake_openai: FakeOpenAI, allow_local_endpoints: None
    ) -> None:
        user = await _make_user(db_session, "byok_disabled")
        record = await _make_credential(db_session, user, base_url=fake_openai.base_url)
        record.status = "disabled"
        await db_session.commit()

        with pytest.raises(ForbiddenException):
            await _resolver().resolve(db_session, user.id, record.id)

    async def test_cache_reuses_until_key_rotation(
        self, db_session: Any, fake_openai: FakeOpenAI, allow_local_endpoints: None
    ) -> None:
        user = await _make_user(db_session, "byok_cache")
        record = await _make_credential(db_session, user, base_url=fake_openai.base_url)
        resolver = _resolver()

        first = await resolver.resolve(db_session, user.id, record.id)
        second = await resolver.resolve(db_session, user.id, record.id)
        assert first is second  # 未变化时复用，避免每次任务重建 SDK 客户端

        service = LLMCredentialService(cipher=CredentialCipher.from_settings())
        await service.update(
            db_session,
            user.id,
            record.id,
            LLMCredentialUpdate(api_key="sk-rotated-abcdefgh"),
        )
        third = await resolver.resolve(db_session, user.id, record.id)
        assert third is not first
        assert third.model == first.model

    async def test_max_output_tokens_sent_as_max_tokens(
        self, db_session: Any, fake_openai: FakeOpenAI, allow_local_endpoints: None
    ) -> None:
        """用户声明的「最大输出」必须真正下发：chat 不传 max_tokens 时用声明的默认值。"""
        user = await _make_user(db_session, "byok_max_tokens")
        record = await _make_credential(
            db_session,
            user,
            base_url=fake_openai.base_url,
            context_window=1_000_000,
            max_output_tokens=128_000,
        )

        client = await _resolver().resolve(db_session, user.id, record.id)
        await client.chat([Message(role=MessageRole.USER, content="hi")])

        assert fake_openai.requests[0]["body"]["max_tokens"] == 128_000

    async def test_no_max_output_means_no_max_tokens(
        self, db_session: Any, fake_openai: FakeOpenAI, allow_local_endpoints: None
    ) -> None:
        """没声明就不发 max_tokens：输出上限沿用厂商默认，平台不擅自截断。"""
        user = await _make_user(db_session, "byok_no_max_tokens")
        record = await _make_credential(db_session, user, base_url=fake_openai.base_url)

        client = await _resolver().resolve(db_session, user.id, record.id)
        await client.chat([Message(role=MessageRole.USER, content="hi")])

        assert "max_tokens" not in fake_openai.requests[0]["body"]

    async def test_cache_rebuilds_when_max_output_changes(
        self, db_session: Any, fake_openai: FakeOpenAI, allow_local_endpoints: None
    ) -> None:
        """容量进了缓存键：改完 max_output 立即生效，不吃 5 分钟缓存的旧值。"""
        user = await _make_user(db_session, "byok_cache_limits")
        record = await _make_credential(
            db_session, user, base_url=fake_openai.base_url, max_output_tokens=8_192
        )
        resolver = _resolver()

        first = await resolver.resolve(db_session, user.id, record.id)
        again = await resolver.resolve(db_session, user.id, record.id)
        assert first is again

        service = LLMCredentialService(cipher=CredentialCipher.from_settings())
        await service.update(
            db_session,
            user.id,
            record.id,
            LLMCredentialUpdate(max_output_tokens=32_768),
        )
        rebuilt = await resolver.resolve(db_session, user.id, record.id)
        assert rebuilt is not first
        await rebuilt.chat([Message(role=MessageRole.USER, content="hi")])
        assert fake_openai.requests[0]["body"]["max_tokens"] == 32_768

    async def test_custom_host_resolving_to_private_is_rejected(
        self,
        db_session: Any,
        allow_local_endpoints: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """创建期拦不住 `localhost` 解析：运行时必须再查一次 DNS。"""
        user = await _make_user(db_session, "byok_dns")
        record = await _make_credential(
            db_session, user, base_url="http://localhost/v1", provider="custom"
        )
        # 模拟生产配置：创建时允许（测试环境），解析时禁止
        monkeypatch.setattr(config.settings.llm, "byok_allow_private_base_url", False)

        with pytest.raises(ValidationException) as exc:
            await _resolver().resolve(db_session, user.id, record.id)
        assert "内网" in exc.value.message

    async def test_preset_provider_does_not_resolve_dns(
        self, db_session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """预置厂商的端点由平台写死，不走自定义端点的 DNS 复核。"""
        user = await _make_user(db_session, "byok_preset")
        service = LLMCredentialService(cipher=CredentialCipher.from_settings())
        record = await service.create(
            db_session,
            user.id,
            LLMCredentialCreate(
                name="preset",
                provider="deepseek",
                model="deepseek-chat",
                api_key=USER_KEY,
            ),
        )

        called = False

        async def _boom(_: str) -> None:
            nonlocal called
            called = True

        monkeypatch.setattr("app.llm.byok.ensure_public_endpoint", _boom)
        client = await _resolver().resolve(db_session, user.id, record.id)

        assert client.base_url == "https://api.deepseek.com/v1"
        assert called is False

    async def test_missing_credential_is_404(self, db_session: Any) -> None:
        user = await _make_user(db_session, "byok_missing")
        with pytest.raises(NotFoundException):
            await _resolver().resolve(db_session, user.id, uuid.uuid4())

    async def test_auth_failures_count_and_auto_disable(
        self,
        db_session: Any,
        fake_openai: FakeOpenAI,
        allow_local_endpoints: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """运行时 401 也要计数：连续两次就把凭据打为 invalid，下次直接拒绝。"""
        monkeypatch.setattr(config.settings.llm, "byok_failure_threshold", 2)
        fake_openai.status = 401
        user = await _make_user(db_session, "byok_auth_fail")
        record = await _make_credential(db_session, user, base_url=fake_openai.base_url)
        factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
        resolver = _resolver(factory)
        client = await resolver.resolve(db_session, user.id, record.id)

        with pytest.raises(AuthenticationError):
            await client.chat([Message(role=MessageRole.USER, content="hi")])
        async with factory() as check:
            row = await check.get(LLMCredential, record.id)
            assert row is not None
            assert row.consecutive_failures == 1
            assert row.status == "active"

        with pytest.raises(AuthenticationError):
            await client.chat([Message(role=MessageRole.USER, content="hi")])
        async with factory() as check:
            row = await check.get(LLMCredential, record.id)
            assert row is not None
            assert row.consecutive_failures == 2
            assert row.status == "invalid"

        # 用新会话（生产就是这样）：缓存里的客户端不能绕过状态检查
        async with factory() as fresh:
            with pytest.raises(ForbiddenException):
                await resolver.resolve(fresh, user.id, record.id)


class TestScope:
    def test_set_and_reset(self) -> None:
        sentinel = object()
        assert get_scoped_llm() is None
        with use_llm_client(sentinel):
            assert get_scoped_llm() is sentinel
        assert get_scoped_llm() is None

    def test_nested_scope_restores_outer(self) -> None:
        outer, inner = object(), object()
        with use_llm_client(outer):
            with use_llm_client(inner):
                assert get_scoped_llm() is inner
            assert get_scoped_llm() is outer

    def test_scope_resets_on_exception(self) -> None:
        with pytest.raises(RuntimeError):
            with use_llm_client(object()):
                raise RuntimeError("boom")
        assert get_scoped_llm() is None

    def test_container_prefers_scoped_client(self) -> None:
        platform = object()
        byok = object()
        container = RuntimeContainer(llm=platform)
        assert container.create_llm() is platform

        with use_llm_client(byok):
            assert container.create_llm() is byok
        assert container.create_llm() is platform

    def test_planner_uses_scoped_client(self) -> None:
        captured: dict[str, Any] = {}

        def factory(**kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return kwargs

        container = RuntimeContainer(planner_factory=factory)
        scoped = object()
        with use_llm_client(scoped):
            container.create_planner()

        assert captured["llm"] is scoped


def _auth_error() -> AuthenticationError:
    """构造真实的 openai 401 异常（不是 mock 类名）。"""
    request = httpx.Request("POST", "https://vendor.test/v1/chat/completions")
    response = httpx.Response(401, request=request)
    return AuthenticationError("invalid api key", response=response, body=None)


class _StubStreamLLM:
    """只实现 stream_chat 的替身：用于验证包装层的异常路径。"""

    def __init__(self, *, error: Exception | None = None, chunks: list[str] | None = None):
        self._error = error
        self._chunks = chunks or []

    async def stream_chat(self, messages: Any, **kwargs: Any) -> AsyncIterator[str]:
        if self._error is not None:
            raise self._error
        for chunk in self._chunks:
            yield chunk


class TestStreamAuthFailure:
    """流式调用也要累计鉴权失败（Issue #15 P1 缺口）。

    `stream_chat` 是异步生成器：异常在**迭代时**才抛，包一层 `async for`
    才能捕获。否则任务走流式撞 401 时不计数，凭据永远不会被自动停用。
    """

    def _client(self, stub: Any, recorder: list[str]) -> BYOKClient:
        async def record(error: str) -> None:
            recorder.append(error)

        return BYOKClient(
            credential_id=uuid.uuid4(),
            provider="custom",
            model="stub-model",
            base_url="https://vendor.test/v1",
            llm=stub,
            on_auth_failure=record,
        )

    async def test_stream_auth_error_is_counted_and_reraised(self) -> None:
        recorder: list[str] = []
        client = self._client(_StubStreamLLM(error=_auth_error()), recorder)

        with pytest.raises(AuthenticationError):
            async for _ in client.stream_chat([Message(role=MessageRole.USER, content="hi")]):
                pass

        assert len(recorder) == 1
        assert recorder[0].startswith("AuthenticationError")

    async def test_stream_success_passes_chunks_without_counting(self) -> None:
        recorder: list[str] = []
        client = self._client(_StubStreamLLM(chunks=["a", "b"]), recorder)

        chunks = [
            chunk
            async for chunk in client.stream_chat([Message(role=MessageRole.USER, content="hi")])
        ]

        assert chunks == ["a", "b"]
        assert recorder == []

    async def test_stream_non_auth_error_is_not_counted(self) -> None:
        """超时/5xx 等上游抖动不该把好 key 打成无效。"""
        recorder: list[str] = []
        client = self._client(_StubStreamLLM(error=RuntimeError("upstream 502")), recorder)

        with pytest.raises(RuntimeError):
            async for _ in client.stream_chat([Message(role=MessageRole.USER, content="hi")]):
                pass

        assert recorder == []

    async def test_stream_counting_failure_does_not_mask_original_error(self) -> None:
        """记账失败（数据库抖了）不能让用户看到「记账错误」而不是「key 被拒」。"""

        async def broken(error: str) -> None:
            raise RuntimeError("db down")

        client = BYOKClient(
            credential_id=uuid.uuid4(),
            provider="custom",
            model="stub-model",
            base_url="https://vendor.test/v1",
            llm=_StubStreamLLM(error=_auth_error()),
            on_auth_failure=broken,
        )

        with pytest.raises(AuthenticationError):
            async for _ in client.stream_chat([Message(role=MessageRole.USER, content="hi")]):
                pass
