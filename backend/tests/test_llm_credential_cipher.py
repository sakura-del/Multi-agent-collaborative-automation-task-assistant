"""凭据加密层测试（Issue #15 · BYOK P0）。

## 锁定的不变量

1. 密文与明文**双向可逆**，但密文里不含明文；
2. 同一明文两次加密**结果不同**（随机 nonce），不能靠比对密文判断「是不是同一个 key」；
3. 密文绑定 `user_id`（AAD）：把 A 的密文换给 B 用会解密失败；
4. 任何篡改（改一个 base64 字符）都会失败，不返回垃圾明文；
5. 密钥缺失/格式错误时给**可读的配置错误**，不是 500 空指针；
6. `mask_secret` 对短 key 不能泄露超过 4 位。
"""

from __future__ import annotations

import base64
import uuid

import pytest

from app.core import config
from app.security.credential_cipher import (
    CredentialCipher,
    CredentialCipherUnavailable,
    CredentialDecryptError,
    mask_secret,
)

#: 32 字节测试主密钥（base64）。与生产无关，仅用于单测。
TEST_KEY_B64 = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
TEST_KEY_HEX = b"0123456789abcdef0123456789abcdef".hex()


def _cipher(key: str = TEST_KEY_B64) -> CredentialCipher:
    return CredentialCipher(key)


class TestKeyDecoding:
    def test_accepts_base64(self) -> None:
        assert _cipher().encrypt("sk-test", user_id=uuid.uuid4()).startswith("v1:")

    def test_accepts_hex(self) -> None:
        user_id = uuid.uuid4()
        token = _cipher(TEST_KEY_HEX).encrypt("sk-test", user_id=user_id)
        assert _cipher(TEST_KEY_HEX).decrypt(token, user_id=user_id) == "sk-test"

    def test_rejects_short_key(self) -> None:
        with pytest.raises(CredentialCipherUnavailable) as exc:
            _cipher("dG9vLXNob3J0")
        assert "32" in exc.value.message

    def test_rejects_garbage(self) -> None:
        with pytest.raises(CredentialCipherUnavailable):
            _cipher("not-base64-and-not-hex-!!!-but-long-enough-to-look-real")

    def test_missing_setting_is_readable_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """生产未配密钥时要报「配置缺失」，而不是抛 TypeError/AttributeError。"""
        monkeypatch.setattr(config.settings.secrets, "encryption_key", "")
        with pytest.raises(CredentialCipherUnavailable) as exc:
            CredentialCipher.from_settings()
        assert "SECRETS__ENCRYPTION_KEY" in exc.value.message


class TestRoundTrip:
    def test_roundtrip(self) -> None:
        cipher = _cipher()
        user_id = uuid.uuid4()
        token = cipher.encrypt("sk-1234567890abcdef", user_id=user_id)
        assert cipher.decrypt(token, user_id=user_id) == "sk-1234567890abcdef"

    def test_nonce_is_randomized(self) -> None:
        """两次加密同一明文必须不同 —— 否则密文本身成了可比对的指纹。"""
        cipher = _cipher()
        user_id = uuid.uuid4()
        first = cipher.encrypt("sk-same-key", user_id=user_id)
        second = cipher.encrypt("sk-same-key", user_id=user_id)
        assert first != second
        assert cipher.decrypt(first, user_id=user_id) == cipher.decrypt(second, user_id=user_id)

    def test_ciphertext_contains_no_plaintext(self) -> None:
        cipher = _cipher()
        token = cipher.encrypt("sk-very-distinctive-key-value", user_id=uuid.uuid4())
        assert "sk-very-distinctive-key-value" not in token
        assert "distinctive" not in token

    def test_aad_binds_user(self) -> None:
        """A 的密文换给 B → 解密失败（防跨用户密文搬运）。"""
        cipher = _cipher()
        token = cipher.encrypt("sk-owner-a", user_id=uuid.UUID(int=1))
        with pytest.raises(CredentialDecryptError):
            cipher.decrypt(token, user_id=uuid.UUID(int=2))

    def test_tampering_detected(self) -> None:
        cipher = _cipher()
        user_id = uuid.uuid4()
        token = cipher.encrypt("sk-tamper-me", user_id=user_id)
        body = token[3:]
        flipped = ("A" if body[0] != "A" else "B") + body[1:]
        with pytest.raises(CredentialDecryptError):
            cipher.decrypt("v1:" + flipped, user_id=user_id)

    def test_unknown_version_rejected(self) -> None:
        with pytest.raises(CredentialDecryptError):
            _cipher().decrypt("v9:abcd", user_id=uuid.uuid4())

    def test_error_message_has_no_secret(self) -> None:
        cipher = _cipher()
        token = cipher.encrypt("sk-do-not-leak-this", user_id=uuid.uuid4())
        with pytest.raises(CredentialDecryptError) as exc:
            cipher.decrypt(token, user_id=uuid.uuid4())
        assert "sk-do-not-leak-this" not in str(exc.value)


class TestFingerprint:
    def test_stable_for_same_key(self) -> None:
        cipher = _cipher()
        assert cipher.fingerprint("sk-abc") == cipher.fingerprint("sk-abc")

    def test_differs_for_different_keys(self) -> None:
        cipher = _cipher()
        assert cipher.fingerprint("sk-abc") != cipher.fingerprint("sk-abd")

    def test_not_derived_from_plaintext(self) -> None:
        cipher = _cipher()
        digest = cipher.fingerprint("sk-abc")
        assert "sk-abc" not in digest
        assert len(digest) == 32  # 128 bit 足够做去重，又不便于离线爆破

    def test_depends_on_master_key(self) -> None:
        other = base64.b64encode(b"abcdef0123456789abcdef0123456789").decode()
        assert _cipher().fingerprint("sk-abc") != _cipher(other).fingerprint("sk-abc")


class TestMask:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("sk-1234567890abcd", "sk-…abcd"),
            ("12345678", "…5678"),
            ("abc", "…"),
        ],
    )
    def test_mask(self, value: str, expected: str) -> None:
        assert mask_secret(value) == expected

    def test_mask_never_returns_full_value(self) -> None:
        for value in ("a" * 8, "b" * 12, "c" * 40):
            assert mask_secret(value) != value
