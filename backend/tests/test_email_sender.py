"""P1-C 邮件发送器测试（计划 2026-10-20-00 §1.3）。

覆盖：
- 默认 ``LoggingEmailSender``：只记日志、不联网、返回成功（保证演示环境注册不被阻塞）；
- ``SmtpEmailSender`` **未配置**：``send()`` 返回 False 且绝不抛错、不建立 SMTP 连接；
- ``SmtpEmailSender`` 已配置：走完 SMTP 流程返回 True；传输故障时返回 False 不抛错；
- ``build_email_sender()``：无配置 → Logging；sender=smtp 且 host/from 齐全 → Smtp；
  配置不全 → 回退 Logging（不抛错）。

铁律：**零真实 SMTP/网络** —— 所有 SMTP 交互都由 monkeypatch 的替身接管。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from app.services import email_sender as email_sender_module
from app.services.email_sender import (
    EmailSender,
    LoggingEmailSender,
    SmtpEmailSender,
    build_email_sender,
)

# ===== 测试替身 =====


class _StubSmtp:
    """最小 SMTP 替身：记录构造参数与调用；可切换为「发送时抛错」。"""

    instances: ClassVar[list[_StubSmtp]] = []
    raise_on_send: ClassVar[bool] = False

    def __init__(self, host: str = "", port: int = 0, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.received_login: tuple[str, str] | None = None
        self.messages: list[Any] = []
        _StubSmtp.instances.append(self)

    def __enter__(self) -> _StubSmtp:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.received_login = (username, password)

    def send_message(self, message: Any) -> None:
        if _StubSmtp.raise_on_send:
            raise OSError("SMTP 连接被重置")
        self.messages.append(message)


@pytest.fixture(autouse=True)
def _reset_stub() -> None:
    _StubSmtp.instances.clear()
    _StubSmtp.raise_on_send = False


def _config(**overrides: Any) -> SimpleNamespace:
    """构造 email 配置替身（字段名与建议的 yaml 段一致）。"""
    payload: dict[str, Any] = {
        "sender": "logging",
        "from_address": "no-reply@example.com",
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_username": "",
        "smtp_password": "",
        "smtp_use_tls": True,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _configured() -> SmtpEmailSender:
    return SmtpEmailSender(
        host="mail.example.com",
        port=2525,
        username="robot",
        password="secret",
        from_address="no-reply@example.com",
        use_tls=True,
    )


# ===== LoggingEmailSender =====


class TestLoggingEmailSender:
    async def test_send_succeeds_and_records_message(self) -> None:
        sender = LoggingEmailSender()

        ok = await sender.send(to="user@example.com", subject="验证邮箱", body="点击链接")

        assert isinstance(sender, EmailSender)
        assert ok is True
        assert sender.last_message == {
            "to": "user@example.com",
            "subject": "验证邮箱",
            "body": "点击链接",
        }

    async def test_send_never_raises_on_empty_recipient(self) -> None:
        sender = LoggingEmailSender()

        # 缺地址也只记日志，不抛错（演示环境绝不因邮件阻塞注册）
        assert await sender.send(to="", subject="x", body="y") is True


# ===== SmtpEmailSender =====


class TestSmtpEmailSender:
    def test_unconfigured_sender_reports_not_configured(self) -> None:
        sender = SmtpEmailSender(host="", from_address="")

        assert sender.is_configured is False

        configured = _configured()
        assert configured.is_configured is True

    async def test_unconfigured_send_returns_false_without_connecting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _forbidden(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("未配置 SMTP 时不得建立任何 SMTP 连接")

        monkeypatch.setattr(email_sender_module, "smtplib", _ExplodingSmtplib(_forbidden))
        sender = SmtpEmailSender(host="", from_address="")

        assert await sender.send(to="user@example.com", subject="s", body="b") is False

    async def test_configured_send_uses_smtp_and_returns_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(email_sender_module, "smtplib", _StubSmtplib(_StubSmtp))
        sender = _configured()

        ok = await sender.send(to="user@example.com", subject="验证邮箱", body="点击链接")

        assert ok is True
        assert len(_StubSmtp.instances) == 1
        client = _StubSmtp.instances[0]
        assert (client.host, client.port) == ("mail.example.com", 2525)
        assert client.started_tls is True
        assert client.received_login == ("robot", "secret")
        assert len(client.messages) == 1
        message = client.messages[0]
        assert message["To"] == "user@example.com"
        assert message["Subject"] == "验证邮箱"
        assert message["From"] == "no-reply@example.com"

    async def test_transport_failure_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(email_sender_module, "smtplib", _StubSmtplib(_StubSmtp))
        _StubSmtp.raise_on_send = True
        sender = _configured()

        # 传输故障只记 warning，绝不向上抛错（否则会拖垮调用方任务）
        assert await sender.send(to="user@example.com", subject="s", body="b") is False


class _ExplodingSmtplib:
    """一旦被构造/使用就抛错的 smtplib 替身（用于证明「未配置时不连接」）。"""

    def __init__(self, explode: Any) -> None:
        self._explode = explode

    def SMTP(self, *args: Any, **kwargs: Any) -> Any:
        self._explode(*args, **kwargs)

    def SMTP_SSL(self, *args: Any, **kwargs: Any) -> Any:
        self._explode(*args, **kwargs)


class _StubSmtplib:
    """把 smtplib.SMTP / SMTP_SSL 指向替身类。"""

    def __init__(self, stub: type[_StubSmtp]) -> None:
        self.SMTP = stub
        self.SMTP_SSL = stub


# ===== 工厂 =====


class TestBuildEmailSender:
    def test_empty_config_falls_back_to_logging(self) -> None:
        sender = build_email_sender(SimpleNamespace())

        assert isinstance(sender, LoggingEmailSender)

    def test_smtp_sender_selected_when_fully_configured(self) -> None:
        sender = build_email_sender(
            _config(sender="smtp", smtp_host="mail.example.com", smtp_username="robot")
        )

        assert isinstance(sender, SmtpEmailSender)
        assert sender.is_configured is True

    def test_incomplete_smtp_config_falls_back_to_logging_without_raising(self) -> None:
        # sender=smtp 但 host/from 缺失：不得抛错，回退日志发送器（演示环境可用）
        sender = build_email_sender(_config(sender="smtp", smtp_host="", from_address=""))

        assert isinstance(sender, LoggingEmailSender)

    def test_explicit_none_reads_settings_defensively(self) -> None:
        # 配置尚未加进 Settings 时也必须可构造（返回任一 EmailSender，不抛错）
        sender = build_email_sender(None)

        assert isinstance(sender, EmailSender)
