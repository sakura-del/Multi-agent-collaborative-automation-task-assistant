"""邮件发送器（Phase 1 P1-C / 计划 2026-10-20-00 §1.3）。

设计要点：
- ``EmailSender`` 是**可替换协议**：测试注入 fake，生产按配置选实现，业务代码不感知 SMTP；
- ``LoggingEmailSender``（默认）：只把邮件写进日志，保证**未配置 SMTP 时注册/重置不被阻塞**，
  演示环境可从日志里取验证/重置链接；
- ``SmtpEmailSender``：用标准库 ``smtplib``（不引入新依赖，`asyncio.to_thread` 避免阻塞事件循环），
  **未配置或投递失败一律只记 warning 并返回 False，绝不向上抛错**——邮件失败不得拖垮账号流程；
- ``build_email_sender()``：配置缺省/不全时回退日志发送器，同样不抛错。

安全说明：日志里默认对 token 做脱敏（生产环境 `app.env=production` 时时始终脱敏；
非生产环境保留原文，便于演示/联调时手工点链接）。
"""

from __future__ import annotations

import asyncio
import re
import smtplib
from email.message import EmailMessage
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 用于日志脱敏的一次性令牌（链接形态 `token=xxxx`）
_TOKEN_IN_URL = re.compile(r"(token=)([A-Za-z0-9_\-]{8,})")


def mask_tokens(text: str) -> str:
    """日志脱敏：把 `token=xxx` 的明文令牌替换为 `token=***`。"""
    return _TOKEN_IN_URL.sub(r"\1***", text)


@runtime_checkable
class EmailSender(Protocol):
    """邮件发送协议：实现方负责投递，失败返回 ``False``（不抛错）。"""

    async def send(self, *, to: str, subject: str, body: str) -> bool:
        """发送一封纯文本邮件；成功返回 ``True``，失败返回 ``False``。"""
        ...


class LoggingEmailSender:
    """默认发送器：不联网，只把邮件内容写入日志（演示环境可用）。"""

    def __init__(self) -> None:
        #: 最近一封邮件（便于测试/诊断；生产下无状态语义）
        self.last_message: dict[str, str] | None = None

    async def send(self, *, to: str, subject: str, body: str) -> bool:
        self.last_message = {"to": to, "subject": subject, "body": body}
        body_for_log = body
        if getattr(get_settings().app, "env", "development") == "production":
            body_for_log = mask_tokens(body)
        logger.info("email_logged", to=to, subject=subject, body=body_for_log)
        return True


class SmtpEmailSender:
    """SMTP 发送器：配置驱动；未配置时 ``send()`` 返回 ``False`` 且不建立任何连接。"""

    def __init__(
        self,
        *,
        host: str = "",
        port: int = 587,
        username: str = "",
        password: str = "",
        from_address: str = "",
        use_tls: bool = True,
        timeout: float = 10.0,
    ) -> None:
        self._host = (host or "").strip()
        self._port = int(port or 587)
        self._username = username or ""
        self._password = password or ""
        self._from_address = (from_address or "").strip()
        self._use_tls = bool(use_tls)
        self._timeout = float(timeout)

    @property
    def is_configured(self) -> bool:
        """是否具备投递条件（host + from 齐全）；``False`` 时调用方应走日志回退。"""
        return bool(self._host and self._from_address)

    async def send(self, *, to: str, subject: str, body: str) -> bool:
        if not self.is_configured:
            logger.warning("smtp_not_configured", to=to, subject=subject)
            return False
        if not to:
            logger.warning("smtp_missing_recipient", subject=subject)
            return False
        try:
            # smtplib 是同步库：放到线程池，避免阻塞事件循环
            await asyncio.to_thread(self._send_sync, to=to, subject=subject, body=body)
        except Exception as exc:  # 邮件失败绝不拖垮账号流程（第三方实现可能抛错）
            logger.warning("smtp_send_failed", to=to, error=str(exc))
            return False
        return True

    def _send_sync(self, *, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self._from_address
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as client:
            if self._use_tls:
                client.starttls()
            if self._username:
                client.login(self._username, self._password)
            client.send_message(message)


def build_email_sender(config: Any | None = None) -> EmailSender:
    """按配置构造发送器；配置缺省或不全时回退 ``LoggingEmailSender``（绝不抛错）。"""
    email_config = config if config is not None else getattr(get_settings(), "email", None)
    if email_config is None:
        return LoggingEmailSender()

    sender_kind = str(getattr(email_config, "sender", "logging") or "logging").lower()
    host = str(getattr(email_config, "smtp_host", "") or "").strip()
    from_address = str(getattr(email_config, "from_address", "") or "").strip()
    if sender_kind != "smtp":
        return LoggingEmailSender()
    if not host or not from_address:
        # 配了 smtp 但配置不全：回退日志发送器，保证演示/开发环境可用
        logger.warning(
            "smtp_incomplete_config_fallback_logging",
            smtp_host=host,
            from_address=from_address,
        )
        return LoggingEmailSender()
    return SmtpEmailSender(
        host=host,
        port=int(getattr(email_config, "smtp_port", 587) or 587),
        username=str(getattr(email_config, "smtp_username", "") or ""),
        password=str(getattr(email_config, "smtp_password", "") or ""),
        from_address=from_address,
        use_tls=bool(getattr(email_config, "smtp_use_tls", True)),
    )


@lru_cache(maxsize=1)
def get_cached_email_sender() -> EmailSender:
    """进程级发送器单例（按配置构造一次，请求复用，避免每请求重复读配置）。"""
    return build_email_sender()


def reset_cached_email_sender() -> None:
    """清空单例缓存（测试/配置热更新用）。"""
    get_cached_email_sender.cache_clear()
