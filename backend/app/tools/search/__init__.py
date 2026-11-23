"""联网搜索 provider（会话/Agent 场景，2026-11-19）。

默认 Tavily：一把 key、结构化结果、basic 深度 = 最低成本档。
接口保持最小：``search(query) -> list[WebSearchResult]``，
后续 deepseek / serper 等只实现同一协议即可切换。
"""

from app.tools.search.models import WebSearchResult
from app.tools.search.tavily import TavilySearchProvider, get_search_provider

__all__ = ["TavilySearchProvider", "WebSearchResult", "get_search_provider"]
