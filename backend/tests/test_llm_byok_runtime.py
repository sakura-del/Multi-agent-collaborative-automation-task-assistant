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
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from openai import AuthenticationError

from app.core import config
from app.core.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.db.models.user import User
from app.llm.byok import CredentialLLMResolver
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
        ),
    )


def _resolver() -> CredentialLLMResolver:
    return CredentialLLMResolver(cipher=CredentialCipher.from_settings())


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
