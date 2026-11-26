"""万方数据（Wanfang Data）检索源（Issue #67，2026-11-26 · BYOK）。

接入万方开放检索 API（`http://api.wfdata.com`，POST JSON + `X-App-Key`），
把中文期刊 / 学位论文 / 会议论文归一化为既有 `LiteratureRecord`，
并补齐串行节流与短 TTL 缓存（与 OpenAlex / S2 源同一套原语）。

## 这是「用户自带 Key」的数据源

App Key 由用户在设置页填写，AES-256-GCM 加密存在 `data_source_credential`，
任务启动时解密后经 `resolve_literature_source(wfdata_app_key=...)` 传进来。
本模块**不读环境变量**：没有平台级万方 Key，也不该有。

## 费用护栏：单任务最多 5 次基础检索

万方 AI HUB 按次计费（基础检索约 ¥0.01/次），且一次任务可能因为补检索被
多次调用同一个 source 实例。因此本实例内置 `max_calls` 计数器：超过上限后
**不再外呼**，直接返回空列表（配 warning 日志）——把「失控烧钱」变成
「这次补检索没结果」，任务其余部分照常推进。缓存命中不计费、不计数。

## 无 Key 时

`search()` 抛 `ValidationException`（正常路径上不会发生：解析层已按
「用户没配 Key 就不选万方」过滤）。保留这个防御是为了让「绕过解析层直接
构造」时立刻暴露，而不是悄悄返回空结果假装成功。

## 归一化要点

- 万方返回 `{documents: [{resource_type, fields: {...}}], numFound}`，
  fields 是 CamelCase、repeated 字段是数组；
- 有 DOI 时把 `source_url` 归一成 `https://doi.org/{doi}`，让
  `MultiSourceLiteratureSource` 的三级去重（DOI → source_id → 归一化题名）
  能跨源合并同一篇中文文献；
- 没有开放全文下载，`pdf_url` 用详情页兜底（`LiteratureRecord` 要求必填）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import HttpUrl
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import UpstreamError, UpstreamTemporaryError, ValidationException
from app.core.logging import get_logger
from app.tools.literature.models import LiteratureRecord, LiteratureSearch
from app.tools.literature.openalex import normalize_doi
from app.tools.literature.source_registry import (
    RequestThrottle,
    SearchCache,
    search_cache_key,
)

logger = get_logger(__name__)

#: 万方开放 API 默认地址（测试用 MockTransport 注入 client，不需要改这里）
DEFAULT_BASE_URL = "http://api.wfdata.com"
#: 检索接口路径（官方脚本唯一使用的检索入口）
QUERY_PATH = "/openwanfang/getQuery"

#: 单任务最多外呼 5 次基础检索（按 ¥0.01/次 ≈ ¥0.05 封顶）
MAX_CALLS_PER_TASK = 5

#: 万方对调用频率未公开硬性 QPS；取 1s 间隔保守行事，靠缓存避免重复外呼
WANFANG_MIN_INTERVAL_SECONDS = 1.0
WANFANG_CACHE_TTL_SECONDS = 60.0
_DEFAULT_TIMEOUT = httpx.Timeout(20.0)
_MAX_RETRY_AFTER_SECONDS = 2.0
_DEFAULT_RETRY_AFTER_SECONDS = 1.0
_MAX_TITLE_LENGTH = 500
_MAX_SOURCE_ID_LENGTH = 64
_MAX_RESULTS = 50
_MAX_CATEGORIES = 10

#: 中文文献的主力库：中文期刊 + 学位论文 + 会议论文
DEFAULT_COLLECTIONS: tuple[str, ...] = (
    "OpenPeriodicalChi",
    "OpenThesis",
    "OpenConference",
)

#: 详情页路径按资源类型区分（万方公开详情页 URL 形如 d.wanfangdata.com.cn/periodical/<id>）
_DETAIL_PATH_BY_RESOURCE_TYPE: dict[str, str] = {
    "Periodical": "periodical",
    "Thesis": "thesis",
    "Conference": "conference",
    "Patent": "patent",
    "Standard": "standard",
}

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_COLLECTIONS",
    "MAX_CALLS_PER_TASK",
    "QUERY_PATH",
    "WanfangSource",
]


class WanfangSource:
    """万方开放检索 API 适配器（惰性建连、可注入 client 便于 MockTransport 测试）。"""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        app_key: str = "",
        base_url: str | None = None,
        timeout: httpx.Timeout | None = None,
        max_retries: int = 1,
        max_calls: int = MAX_CALLS_PER_TASK,
        throttle: RequestThrottle | None = None,
        cache: SearchCache | None = None,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._app_key = (app_key or "").strip()
        self._base_url = ((base_url or "").strip() or DEFAULT_BASE_URL).rstrip("/")
        self._timeout = timeout or _DEFAULT_TIMEOUT
        self._max_retries = max(0, max_retries)
        self._max_calls = max(1, max_calls)
        self._calls = 0
        self._throttle = throttle or RequestThrottle(WANFANG_MIN_INTERVAL_SECONDS)
        self._cache = cache or SearchCache(WANFANG_CACHE_TTL_SECONDS)

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
        return self._client

    @property
    def calls(self) -> int:
        """本实例已产生的计费调用次数（供测试与日志）。"""
        return self._calls

    async def aclose(self) -> None:
        """只关自己建的 client；注入的 client 由调用方管理。"""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(self, search: LiteratureSearch) -> list[LiteratureRecord]:
        """检索中文期刊 / 学位论文 / 会议论文，返回归一化后的 `LiteratureRecord` 列表。"""
        if not self._app_key:
            raise ValidationException(
                "未配置万方检索 Key（用户在设置页填写万方 App Key 后，中文综述会自动启用万方）"
            )

        key = search_cache_key(search)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        if self._calls >= self._max_calls:
            logger.warning(
                "wanfang_call_budget_exhausted",
                max_calls=self._max_calls,
                query=search.query,
            )
            return []

        await self._throttle.wait()
        response = await self._post(
            QUERY_PATH,
            self._build_payload(search),
            operation="search",
        )
        payload = _parse_json_object(response)
        documents = payload.get("documents")
        if not isinstance(documents, list):
            raise ValidationException("万方检索响应缺少 documents 列表")

        records = [_parse_document(item) for item in documents]
        records = _filter_by_date_range(records, search)[: search.max_results]
        self._cache.set(key, records)
        return list(records)

    # ===== 内部实现 =====

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "X-App-Key": self._app_key}

    def _build_payload(self, search: LiteratureSearch) -> dict[str, Any]:
        return {
            "collections": list(DEFAULT_COLLECTIONS),
            "query": search.query,
            # 万方默认按相关度排序（OfflineScore 为官方脚本使用的 sort_name）
            "sort": {"sort_name": "OfflineScore"},
            "rows": max(1, min(search.max_results, _MAX_RESULTS)),
            "start": 0,
        }

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        operation: str,
    ) -> httpx.Response:
        """POST JSON：429 按 Retry-After 有限重试；5xx/429 归临时故障；401/402/403 归鉴权/余额。"""
        attempt = 0
        while True:
            if self._calls >= self._max_calls:
                raise UpstreamTemporaryError(
                    f"万方检索调用次数已达上限（{self._max_calls} 次，operation={operation}）"
                )
            self._calls += 1
            try:
                response = await self.client.post(
                    f"{self._base_url}{path}", json=payload, headers=self._headers()
                )
            except httpx.TimeoutException as exc:
                raise UpstreamTemporaryError(
                    f"万方检索请求超时（operation={operation}），请稍后重试"
                ) from exc
            except httpx.HTTPError as exc:
                raise UpstreamTemporaryError(
                    f"万方检索请求失败（operation={operation}）：{exc.__class__.__name__}"
                ) from exc

            status = response.status_code
            if status == 429:
                retry_after = _retry_after_seconds(response)
                if attempt < self._max_retries and retry_after <= _MAX_RETRY_AFTER_SECONDS:
                    attempt += 1
                    await _sleep(retry_after)
                    continue
                raise UpstreamTemporaryError(
                    f"万方接口限流（HTTP 429，operation={operation}），请稍后重试"
                )
            if status >= 500:
                raise UpstreamTemporaryError(
                    f"万方接口暂时不可用（HTTP {status}，operation={operation}）"
                )
            if status in (401, 402, 403):
                raise UpstreamError(
                    "万方 Key 无效或余额不足"
                    f"（HTTP {status}）：请在设置页更新万方 App Key，或取消勾选万方检索源"
                )
            if status >= 400:
                raise UpstreamError(f"万方接口请求失败（HTTP {status}，operation={operation}）")
            return response


# ===== 归一化辅助 =====


async def _sleep(seconds: float) -> None:
    """等待入口：独立函数便于测试替换，测试不产生真实 sleep。"""
    import asyncio

    await asyncio.sleep(seconds)


def _retry_after_seconds(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After", "").strip()
    if not raw:
        return _DEFAULT_RETRY_AFTER_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_RETRY_AFTER_SECONDS
    return max(0.0, value)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _first_text(value: Any) -> str:
    """万方的 repeated 字段可能是数组，也可能是单个字符串（不同接口版本不一致）。"""
    if isinstance(value, list):
        for item in value:
            text = _text(item).strip()
            if text:
                return text
        return ""
    return _text(value).strip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    single = _text(value).strip()
    return [single] if single else []


def _parse_document(item: Any) -> LiteratureRecord:
    """单条万方 document → `LiteratureRecord`；缺必需字段 → ValidationException。"""
    if not isinstance(item, dict):
        raise ValidationException("万方检索结果条目必须是对象")
    fields = item.get("fields")
    if not isinstance(fields, dict):
        raise ValidationException("万方检索结果缺少 fields 对象")

    document_id = _first_text(fields.get("Id"))
    if not document_id:
        raise ValidationException("万方检索结果缺少 Id")
    title = _first_text(fields.get("Title"))
    if not title:
        raise ValidationException("万方检索结果缺少标题")

    doi = normalize_doi(_text(fields.get("DOI")).strip())
    source_url = (
        f"https://doi.org/{doi}" if doi.startswith("10.") else _detail_url(item, document_id)
    )

    try:
        return LiteratureRecord(
            source_id=f"wanfang:{document_id}"[:_MAX_SOURCE_ID_LENGTH],
            title=title[:_MAX_TITLE_LENGTH],
            authors=_string_list(fields.get("Creator")),
            abstract=_first_text(fields.get("Abstract")),
            published_at=_published_at(fields),
            source_url=HttpUrl(source_url),
            # 万方未开放全文下载，详情页兜底（LiteratureRecord.pdf_url 必填）
            pdf_url=HttpUrl(source_url),
            categories=_string_list(fields.get("Keywords"))[:_MAX_CATEGORIES],
        )
    except PydanticValidationError as exc:
        raise ValidationException("万方检索结果字段不符合 LiteratureRecord 契约") from exc


def _detail_url(item: dict[str, Any], document_id: str) -> str:
    """无 DOI 时用万方公开详情页作为来源链接。"""
    resource_type = _text(item.get("resource_type")).strip()
    path = _DETAIL_PATH_BY_RESOURCE_TYPE.get(resource_type, "periodical")
    return f"https://d.wanfangdata.com.cn/{path}/{document_id}"


def _published_at(fields: dict[str, Any]) -> datetime:
    """`PublishDate`（YYYY-MM-DD / YYYY-MM）优先，缺失时用 `PublishYear` 的 1 月 1 日。"""
    raw_date = _text(fields.get("PublishDate")).strip()
    if raw_date:
        try:
            return datetime.fromisoformat(raw_date).replace(tzinfo=UTC)
        except ValueError:
            pass  # 有的记录只给年份，走下面的 PublishYear 分支
    year = fields.get("PublishYear")
    if isinstance(year, int) and not isinstance(year, bool) and 1000 <= year <= 2999:
        return datetime(year, 1, 1, tzinfo=UTC)
    if isinstance(year, str) and year.strip().isdigit():
        parsed = int(year.strip())
        if 1000 <= parsed <= 2999:
            return datetime(parsed, 1, 1, tzinfo=UTC)
    raise ValidationException("万方检索结果缺少可解析的出版年份")


def _filter_by_date_range(
    records: list[LiteratureRecord], search: LiteratureSearch
) -> list[LiteratureRecord]:
    """本地日期兜底过滤（万方的 filters 语义按库不同，统一在本地过滤）。"""
    filtered: list[LiteratureRecord] = []
    for record in records:
        published = record.published_at.date()
        if search.date_from is not None and published < search.date_from:
            continue
        if search.date_to is not None and published > search.date_to:
            continue
        filtered.append(record)
    return filtered


def _parse_json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise ValidationException("万方检索响应不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationException("万方检索响应不是 JSON 对象")
    return payload
