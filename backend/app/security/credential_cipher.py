"""用户模型凭据的加解密（Issue #15 · BYOK P0）。

## 为什么是「加密」而不是「哈希」

平台自己发的 API Key 只存哈希（`api_key` 表），因为校验时只需比对；
但用户自带的模型 Key **必须还原成明文**才能发给厂商 —— 所以只能加密，
不能哈希。这是本模块与 `api_key_service` 最根本的区别。

## 算法与密钥派生

- 主密钥：`SECRETS__ENCRYPTION_KEY`（32 字节，base64 或 hex）；
- HKDF-SHA256 派生两把**用途隔离**的子密钥：
  | 子密钥 | info | 用途 |
  | --- | --- | --- |
  | 加密 | `llm-credential-aead` | AES-256-GCM |
  | 指纹 | `llm-credential-fingerprint` | HMAC-SHA256（去重/审计） |

  为什么不直接用主密钥做两件事：GCM 与 HMAC 共用一把密钥在密码学上是
  「同一密钥两种用途」，属于教科书级反模式。HKDF 的成本可以忽略。

## 密文格式

```
v1:<base64(nonce ‖ ciphertext ‖ tag)>
```

- nonce 12 字节随机，**每次加密都重新取**（同一明文两次加密结果不同）；
- AAD 绑定 `user_id`：把 A 的密文搬到 B 的行上，解密必然失败（`InvalidTag`）；
- 版本前缀留给未来轮换算法（`v2:`），解密端不认识就明确报错，而不是瞎猜。

## 失败语义

| 情况 | 异常 | HTTP |
| --- | --- | --- |
| 未配置主密钥 / 格式不对 | `CredentialCipherUnavailable` | 503 |
| 密文被篡改 / AAD 不匹配 / 版本未知 | `CredentialDecryptError` | 500 |

**绝不**在异常信息或日志里带明文或密文片段 —— 调用方只需要知道「解不开」。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.exceptions import AppException
from app.core.logging import get_logger

logger = get_logger(__name__)

_VERSION_PREFIX = "v1:"
_NONCE_BYTES = 12
_KEY_BYTES = 32
_INFO_AEAD = b"llm-credential-aead"
_INFO_FINGERPRINT = b"llm-credential-fingerprint"


class CredentialCipherUnavailable(AppException):
    """加密主密钥未配置或格式不合法（503）。

    用 503 而不是 500：这是**部署配置问题**，不是代码缺陷；运维补上环境变量
    并重启即可恢复，不需要改代码。
    """

    status_code = 503
    default_message = "凭据加密服务未配置，请联系管理员"
    error_code = "ENCRYPTION_KEY_UNAVAILABLE"


class CredentialDecryptError(AppException):
    """密文无法还原（500）。

    可能原因：主密钥被换过、密文被篡改、AAD 不匹配（跨用户搬运）。
    对用户统一表述为「凭据不可用，请重新填写」—— 不暴露具体密码学原因。
    """

    status_code = 500
    default_message = "凭据无法解密，请重新填写"
    error_code = "CREDENTIAL_DECRYPT_FAILED"


def _decode_master_key(raw: str) -> bytes:
    """把配置里的字符串解成 32 字节主密钥（base64 或 hex）。"""
    value = (raw or "").strip()
    if not value:
        raise CredentialCipherUnavailable(
            "未配置凭据加密主密钥：请在环境变量设置 SECRETS__ENCRYPTION_KEY"
            "（32 字节，base64 或 hex）"
        )

    candidates: list[bytes] = []
    try:
        candidates.append(base64.b64decode(value, validate=True))
    except (binascii.Error, ValueError):
        pass
    try:
        candidates.append(bytes.fromhex(value))
    except ValueError:
        pass

    for candidate in candidates:
        if len(candidate) == _KEY_BYTES:
            return candidate

    raise CredentialCipherUnavailable(
        "凭据加密主密钥格式不合法：需要 32 字节（base64 编码 44 字符，或 hex 64 字符）"
    )


def mask_secret(value: str) -> str:
    """生成可回显的脱敏串（**永远不返回完整值**）。

    - 长度 ≥ 12：保留前 3 位与后 4 位（`sk-…abcd`），便于用户辨认是哪把 key；
    - 长度 5~11：只保留后 4 位（`…5678`）；
    - 长度 ≤ 4：只给一个省略号，避免「脱敏后等于明文」。
    """
    if not value:
        return "…"
    if len(value) >= 12:
        return f"{value[:3]}…{value[-4:]}"
    if len(value) > 4:
        return f"…{value[-4:]}"
    return "…"


class CredentialCipher:
    """AES-256-GCM 加解密器；无状态，可安全复用。"""

    def __init__(self, master_key: str) -> None:
        material = _decode_master_key(master_key)
        self._aead = AESGCM(self._derive(material, _INFO_AEAD))
        self._fingerprint_key = self._derive(material, _INFO_FINGERPRINT)

    @classmethod
    def from_settings(cls) -> CredentialCipher:
        """按当前配置构造。

        **必须读模块级 `settings`**（不是 `get_settings()`）：测试会 patch
        `app.core.config.settings`，而 `get_settings()` 在 `cache_clear()` 后
        返回另一个对象 —— 两者分叉会导致「单跑绿、全量红」（AGENTS §5 #62）。
        """
        from app.core.config import settings

        return cls(settings.secrets.encryption_key)

    @staticmethod
    def _derive(master: bytes, info: bytes) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=_KEY_BYTES, salt=None, info=info).derive(
            master
        )

    @staticmethod
    def _aad(user_id: UUID) -> bytes:
        return f"llm-credential:{user_id}".encode()

    def encrypt(self, plaintext: str, *, user_id: UUID) -> str:
        """加密并返回 `v1:<base64>` 密文。"""
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self._aead.encrypt(nonce, plaintext.encode("utf-8"), self._aad(user_id))
        return _VERSION_PREFIX + base64.b64encode(nonce + sealed).decode("ascii")

    def decrypt(self, token: str, *, user_id: UUID) -> str:
        """还原明文；任何异常路径都不把密文/明文写进消息。"""
        if not token or not token.startswith(_VERSION_PREFIX):
            raise CredentialDecryptError("凭据格式无法识别，请重新填写")
        try:
            raw = base64.b64decode(token[len(_VERSION_PREFIX) :], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CredentialDecryptError("凭据格式无法识别，请重新填写") from exc

        if len(raw) <= _NONCE_BYTES:
            raise CredentialDecryptError("凭据内容不完整，请重新填写")

        nonce, sealed = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
        try:
            plain = self._aead.decrypt(nonce, sealed, self._aad(user_id))
        except InvalidTag as exc:
            # 主密钥换过 / 密文被篡改 / 跨用户搬运，三者都落到这里
            raise CredentialDecryptError() from exc

        try:
            return plain.decode("utf-8")
        except UnicodeDecodeError as exc:  # pragma: no cover - GCM 已保证完整性
            raise CredentialDecryptError("凭据内容编码异常，请重新填写") from exc

    def fingerprint(self, plaintext: str) -> str:
        """不可逆指纹（128 bit）。

        用途：同一用户重复添加同一把 key 时可提示「已存在」，以及审计日志里
        标记「哪一把 key」——**日志里不能出现明文，也不能出现密文**（密文可被
        解密，等同敏感数据）。
        """
        return hmac.new(
            self._fingerprint_key, plaintext.encode("utf-8"), hashlib.sha256
        ).hexdigest()[:32]
