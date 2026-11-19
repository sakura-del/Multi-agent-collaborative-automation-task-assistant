"""Tavily 搜索 provider（默认联网搜索方案）。

契约：Tavily ``POST /search``（Bearer key）→ ``results[{url,title,content,score}]``。
失败一律抛 ``LLMException``（对外 502 可读文案），不静默返回空列表——
「搜不到」与「搜索服务挂了」在 UI 上是两种完全不同的提示。
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.core.exceptions import LLMException
from app.core.logging import get_logger
from app.tools.search.models import WebSearchResult

logger = get_logger(__name__)

_TAVILY_ENDPOINT = "https://api.tavily.com/search"


class TavilySearchProvider:
    """Tavily 实现；每次调用一个短连接（搜索频率低，不值得连接池）。"""

    name = "tavily"

    def __init__(self, api_key: str, *, timeout_s: int = 20) -> None:
        self._api_key = api_key
        self._timeout = timeout_s

    async def search(self, query: str) -> list[WebSearchResult]:
        cfg = get_settings().search
        payload = {
            "query": query,
            "max_results": cfg.max_results,
            "search_depth": cfg.search_depth,
            "include_answer": False,
            "include_raw_content": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    _TAVILY_ENDPOINT,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except httpx.HTTPError as exc:
            logger.warning("web_search_transport_error", provider=self.name, error=str(exc))
            raise LLMException("联网搜索服务连接失败，请稍后重试") from exc

        if response.status_code == 401:
            raise LLMException("联网搜索密钥无效，请联系管理员检查 SEARCH__TAVILY_API_KEY")
        if response.status_code == 429:
            raise LLMException("联网搜索额度/频率超限，请稍后重试")
        if response.status_code >= 400:
            logger.warning(
                "web_search_failed",
                provider=self.name,
                status=response.status_code,
                body=response.text[:200],
            )
            raise LLMException(f"联网搜索失败（上游 {response.status_code}）")

        data = response.json()
        results = data.get("results") or []
        logger.info("web_search_ok", provider=self.name, query=query, count=len(results))
        return [
            WebSearchResult(
                title=str(item.get("title") or item.get("url") or ""),
                url=str(item.get("url") or ""),
                snippet=str(item.get("content") or "")[: cfg.snippet_chars],
                score=float(item["score"]) if item.get("score") is not None else None,
            )
            for item in results
            if item.get("url")
        ]


def get_search_provider() -> TavilySearchProvider:
    """按配置取当前搜索 provider；未配置 key 时抛可读错误。"""
    cfg = get_settings().search
    if not cfg.enabled:
        raise LLMException("联网搜索未启用（SEARCH__ENABLED=false）")
    if cfg.provider != "tavily":
        raise LLMException(f"暂不支持的搜索 provider：{cfg.provider}")
    if not cfg.tavily_api_key:
        raise LLMException("联网搜索未配置密钥（SEARCH__TAVILY_API_KEY）")
    return TavilySearchProvider(cfg.tavily_api_key, timeout_s=cfg.timeout_s)
