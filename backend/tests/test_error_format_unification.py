"""全局错误响应格式兼容统一测试（M0-2）。

锁定行为：
1. 原生 `HTTPException` 保留 `"detail"`（字符串或字典）与原始响应头（如 `WWW-Authenticate`、`Retry-After`），
   同时补充 `"error_code"`、中文友好 `"message"` 与 `"details"`；
2. Starlette 路由级 404（Not Found）/ 405（Method Not Allowed）保留原 `"detail"` 与 `"Allow"` 头，
   同时提供中文 `"message"` 与标准 `"error_code"`；
3. `RequestValidationError`（422）保留 `"detail"` 错误列表与 `"details.errors"`，
   经 `jsonable_encoder` 安全编码（含自定义 `@field_validator` 抛出的 `ValueError` 不崩溃为 500），
   并提取首条中文友好 `"message"`；
4. `AppException` 与 `RateLimitMiddleware` 既有契约保持不变。
"""

from __future__ import annotations

import re

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.exceptions import RateLimitedException, ValidationException
from app.main import create_app


class _ProbeValidationPayload(BaseModel):
    """用于覆盖 Pydantic v2 多种 422 校验错误与自定义 ValueError。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3)
    keys: list[str] = Field(default_factory=list)

    @field_validator("keys")
    @classmethod
    def _validate_unique_keys(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        for item in value:
            if item in seen:
                raise ValueError(f"列标识重复：{item}")
            seen.add(item)
        return value


def _build_probe_client() -> TestClient:
    app = create_app()

    @app.get("/_probe/http-str/{code}")
    async def _http_str(code: int) -> None:
        messages = {
            400: "请求参数异常",
            404: "资源不存在",
            409: "资源状态冲突",
            503: "实时通道暂不可用",
        }
        raise HTTPException(status_code=code, detail=messages[code])

    @app.get("/_probe/http-dict-401")
    async def _http_dict_401() -> None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "凭证已失效", "hint": "relogin"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.get("/_probe/http-429")
    async def _http_429() -> None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="触发保护限流",
            headers={"Retry-After": "12"},
        )

    @app.post("/_probe/validate")
    async def _validate(payload: _ProbeValidationPayload) -> dict[str, object]:
        return {"ok": True, "name": payload.name}

    @app.get("/_probe/app-validation")
    async def _app_validation() -> None:
        raise ValidationException("业务校验失败", details={"field": "topic"})

    @app.get("/_probe/app-ratelimit")
    async def _app_ratelimit() -> None:
        raise RateLimitedException("请求过多", retry_after=9)

    return TestClient(app)


class TestHttpExceptionUnification:
    @pytest.mark.parametrize(
        ("code", "expected_error_code", "detail_text"),
        [
            (400, "BAD_REQUEST", "请求参数异常"),
            (404, "NOT_FOUND", "资源不存在"),
            (409, "CONFLICT", "资源状态冲突"),
            (503, "SERVICE_UNAVAILABLE", "实时通道暂不可用"),
        ],
    )
    def test_http_exception_string_detail_preserves_detail_and_adds_error_code_message(
        self, code: int, expected_error_code: str, detail_text: str
    ) -> None:
        client = _build_probe_client()
        resp = client.get(f"/_probe/http-str/{code}")
        assert resp.status_code == code
        body = resp.json()
        assert body["detail"] == detail_text
        assert body["error_code"] == expected_error_code
        assert body["message"] == detail_text
        assert body["details"] == {}

    def test_http_exception_dict_detail_extracts_message_and_preserves_headers(self) -> None:
        client = _build_probe_client()

        resp_401 = client.get("/_probe/http-dict-401")
        assert resp_401.status_code == 401
        assert resp_401.headers.get("WWW-Authenticate") == "Bearer"
        body_401 = resp_401.json()
        assert body_401["detail"] == {"message": "凭证已失效", "hint": "relogin"}
        assert body_401["error_code"] == "UNAUTHORIZED"
        assert body_401["message"] == "凭证已失效"
        assert body_401["details"] == {"message": "凭证已失效", "hint": "relogin"}

        resp_429 = client.get("/_probe/http-429")
        assert resp_429.status_code == 429
        assert resp_429.headers.get("Retry-After") == "12"
        body_429 = resp_429.json()
        assert body_429["detail"] == "触发保护限流"
        assert body_429["error_code"] == "RATE_LIMITED"
        assert body_429["message"] == "触发保护限流"

    def test_unmatched_route_404_and_405_return_chinese_message_and_detail(self) -> None:
        client = _build_probe_client()

        not_found = client.get("/api/v1/__definitely_nonexistent_route__")
        assert not_found.status_code == 404
        body_404 = not_found.json()
        assert body_404["detail"] == "Not Found"
        assert body_404["error_code"] == "NOT_FOUND"
        assert re.search(r"[\u4e00-\u9fff]", body_404["message"])
        assert "Not Found" not in body_404["message"]

        method_not_allowed = client.post("/api/v1/health")
        assert method_not_allowed.status_code == 405
        assert "Allow" in method_not_allowed.headers
        body_405 = method_not_allowed.json()
        assert body_405["detail"] == "Method Not Allowed"
        assert body_405["error_code"] == "METHOD_NOT_ALLOWED"
        assert re.search(r"[\u4e00-\u9fff]", body_405["message"])


class TestValidationErrorUnification:
    def test_422_validation_error_preserves_errors_list_and_provides_chinese_message(self) -> None:
        client = _build_probe_client()

        missing_resp = client.post("/_probe/validate", json={})
        assert missing_resp.status_code == 422
        missing_body = missing_resp.json()
        assert isinstance(missing_body["detail"], list)
        assert missing_body["error_code"] == "VALIDATION_ERROR"
        assert missing_body["details"] == {"errors": missing_body["detail"]}
        assert "name" in missing_body["message"]
        assert re.search(r"[\u4e00-\u9fff]", missing_body["message"])

        short_resp = client.post("/_probe/validate", json={"name": "ab"})
        assert short_resp.status_code == 422
        short_body = short_resp.json()
        assert short_body["error_code"] == "VALIDATION_ERROR"
        assert "name" in short_body["message"]
        assert re.search(r"[\u4e00-\u9fff]", short_body["message"])

        extra_resp = client.post("/_probe/validate", json={"name": "valid", "bogus": 1})
        assert extra_resp.status_code == 422
        extra_body = extra_resp.json()
        assert extra_body["error_code"] == "VALIDATION_ERROR"
        assert "bogus" in extra_body["message"]
        assert re.search(r"[\u4e00-\u9fff]", extra_body["message"])

    def test_422_custom_value_error_does_not_crash_and_strips_prefix(self) -> None:
        """Pydantic v2 的 ValueError 实例放在 ctx['error']，必须经 jsonable_encoder 编码且不 500。"""
        client = _build_probe_client()
        resp = client.post(
            "/_probe/validate",
            json={"name": "valid", "keys": ["sample_size", "sample_size"]},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["error_code"] == "VALIDATION_ERROR"
        assert isinstance(body["detail"], list)
        assert "列标识重复：sample_size" in body["message"]
        assert not body["message"].startswith("Value error, ")


class TestLegacyContractsUnchanged:
    def test_app_exception_and_rate_limit_remain_unchanged(self) -> None:
        client = _build_probe_client()

        val_resp = client.get("/_probe/app-validation")
        assert val_resp.status_code == 422
        assert val_resp.json() == {
            "error_code": "VALIDATION_ERROR",
            "message": "业务校验失败",
            "details": {"field": "topic"},
        }

        rate_resp = client.get("/_probe/app-ratelimit")
        assert rate_resp.status_code == 429
        assert rate_resp.headers.get("Retry-After") == "9"
        assert rate_resp.json() == {
            "error_code": "RATE_LIMITED",
            "message": "请求过多",
            "details": {"retry_after": 9},
        }
