"""搜索结果的统一形状（provider 无关）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WebSearchResult(BaseModel):
    """单条网页搜索结果。"""

    title: str
    url: str
    snippet: str = Field(description="正文摘要（已按 snippet_chars 截断）")
    score: float | None = Field(default=None, description="相关性分数（provider 提供时）")
