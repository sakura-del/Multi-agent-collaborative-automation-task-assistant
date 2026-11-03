"""DSN 处理：驱动转换、选项补全与脱敏。

为什么单独成模块：
- **驱动转换**：业务库配置是 `postgresql+asyncpg://…`（SQLAlchemy 异步方言），
  同步的 psycopg 不认这个 scheme，必须去掉 `+asyncpg`；
- **选项补全**：psycopg 的 `connect_timeout` / `sslmode` / `options`
  既可能已经写在 DSN 里，也可能由配置追加，重复追加会报错；
- **脱敏**：日志与异常里绝不能出现明文密码（项目铁律：密钥只进 `.env`）。
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# `postgresql+asyncpg://` / `postgresql+psycopg://` 等 SQLAlchemy 方言前缀
_DRIVER_RE = re.compile(r"^(postgresql|postgres)\+[A-Za-z0-9_]+://", re.IGNORECASE)
# URI 形式的密码段：scheme://user:password@host
_URI_PASSWORD_RE = re.compile(r"(://[^:/?#@]+:)([^@]*)@")
# keyword 形式（psycopg conninfo）的密码段
_KV_PASSWORD_RE = re.compile(r"(password\s*=\s*)('[^']*'|\"[^\"]*\"|[^\s;]+)", re.IGNORECASE)


def to_sync_dsn(url: str) -> str:
    """把 SQLAlchemy 风格 DSN 转成 psycopg 可用的同步 DSN。

    `postgresql+asyncpg://…` → `postgresql://…`；已经是同步形式则原样返回。
    """
    return _DRIVER_RE.sub(lambda m: f"{m.group(1)}://", url.strip())


def redact_dsn(dsn: str) -> str:
    """把 DSN 里的密码替换成 `***`，用于日志与错误消息。"""
    if not dsn:
        return ""
    masked = _URI_PASSWORD_RE.sub(r"\1***@", dsn)
    return _KV_PASSWORD_RE.sub(r"\1***", masked)


def describe_dsn(dsn: str) -> dict[str, str]:
    """只提取可安全记录的连接信息（host/port/dbname），不含用户名密码。"""
    if not dsn:
        return {}
    if "://" in dsn:
        parts = urlsplit(dsn)
        return {
            "host": parts.hostname or "",
            "port": str(parts.port or ""),
            "dbname": (parts.path or "").lstrip("/"),
        }
    info: dict[str, str] = {}
    for token in dsn.split():
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        if key.strip().lower() in {"host", "port", "dbname"}:
            info[key.strip().lower()] = value.strip().strip("'\"")
    return info


def with_options(dsn: str, **options: object) -> str:
    """把选项追加到 DSN（已存在同名键则保留原值）。

    支持 URI 与 keyword 两种形式；值为 None 的选项被忽略。
    """
    clean = {k: str(v) for k, v in options.items() if v is not None and v != ""}
    if not clean:
        return dsn

    if "://" in dsn:
        parts = urlsplit(dsn)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        for key, value in clean.items():
            query.setdefault(key, value)
        return urlunsplit(parts._replace(query=urlencode(query)))

    existing = {token.partition("=")[0].strip().lower() for token in dsn.split() if "=" in token}
    appended = " ".join(f"{k}={v}" for k, v in clean.items() if k.lower() not in existing)
    return f"{dsn} {appended}".strip()
