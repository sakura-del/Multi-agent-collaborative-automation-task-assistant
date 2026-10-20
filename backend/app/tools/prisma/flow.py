"""PRISMA 2020 流程图生成（纯函数，无 IO、无网络）。

输入是 `ScreeningService.prisma_counts()` 的派生计数；输出两种形态：

- `render_prisma_markdown`：可直接放进综述「方法」章节的 Markdown 表格
  （PRISMA 2020 要求报告各库命中数、去重数、各阶段筛查/排除数、纳入数、排除理由）；
- `render_prisma_mermaid`：前端渲染用的 Mermaid 流程图。

**为什么计数不落库、每次现算**：汇总数字与明细一旦分开存储就会漂移
（改了决策却忘了更新汇总），而 PRISMA 数字是要写进论文的，必须永远与明细一致。
"""

from __future__ import annotations

from typing import Any

#: 缺失/非法嵌套值一律按 0——流程图必须永远能出，不能因为某个字段没算出来就崩。
_EMPTY: dict[str, Any] = {}


def _as_int(value: Any) -> int:
    """宽松取整：非数字/None 一律按 0（防御不完整载荷）。"""
    if isinstance(value, bool):  # bool 是 int 子类，显式排除避免 True→1
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _get(counts: dict[str, Any], *path: str) -> int:
    """按路径取整数；任一层缺失或不是 dict 时返回 0。"""
    node: Any = counts
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return 0
        node = node[key]
    return _as_int(node)


def _mapping(counts: dict[str, Any], key: str) -> dict[str, int]:
    """取出 `dict[str, int]` 字段并归一为 int（缺失时返回空 dict）。"""
    value = counts.get(key)
    if not isinstance(value, dict):
        return {}
    return {str(item_key): _as_int(item_value) for item_key, item_value in value.items()}


#: 流程图文字（中/英）。系统契约 `output_language` 是 `zh | en`，
#: 生成的 Markdown 要能直接进论文，所以语言必须可切。
_LABELS: dict[str, dict[str, str]] = {
    "zh": {
        "title": "PRISMA 2020 流程图数据",
        "identification": "识别（Identification）",
        "screening": "筛选（Screening）",
        "included_section": "纳入（Included）",
        "source_col": "来源",
        "hits_col": "命中记录数",
        "reason_col": "理由",
        "count_col": "记录数",
        "none": "（无）",
        "identified_total": "各源命中合计",
        "duplicates": "去除重复记录",
        "screened": "进入筛选的记录数",
        "title_screened": "标题/摘要阶段筛查",
        "title_excluded": "标题/摘要阶段排除",
        "fulltext_assessed": "全文阶段评估",
        "fulltext_excluded": "全文阶段排除",
        "exclusion_reasons": "排除理由",
        "included": "最终纳入综述的研究",
        "node_identified": "识别：各源命中",
        "node_duplicates": "去除重复",
        "node_screened": "标题/摘要筛查",
        "node_title_excluded": "标题/摘要阶段排除",
        "node_fulltext": "全文评估",
        "node_fulltext_excluded": "全文阶段排除",
        "node_included": "纳入综述",
        "edge_excluded": "排除",
        "source_header": "| 来源 | 命中记录数 |",
    },
    "en": {
        "title": "PRISMA 2020 flow data",
        "identification": "Identification",
        "screening": "Screening",
        "included_section": "Included",
        "source_col": "Source",
        "hits_col": "Records identified",
        "reason_col": "Reason",
        "count_col": "Records",
        "none": "(none)",
        "identified_total": "Total records identified",
        "duplicates": "Duplicate records removed",
        "screened": "Records screened",
        "title_screened": "Records screened at title/abstract",
        "title_excluded": "Records excluded at title/abstract",
        "fulltext_assessed": "Full-text assessed",
        "fulltext_excluded": "Full-text excluded",
        "exclusion_reasons": "Exclusion reasons",
        "included": "Studies included in review",
        "node_identified": "Identified",
        "node_duplicates": "Duplicates removed",
        "node_screened": "Screened",
        "node_title_excluded": "Title/abstract excluded",
        "node_fulltext": "Full-text assessed",
        "node_fulltext_excluded": "Full-text excluded",
        "node_included": "Included",
        "edge_excluded": "excluded",
        "source_header": "| Source | Records identified |",
    },
}


def _labels(language: str) -> dict[str, str]:
    """取语言标签；未知语言回退中文（系统默认）。"""
    return _LABELS.get(language, _LABELS["zh"])


def render_prisma_markdown(counts: dict[str, Any], *, language: str = "zh") -> str:
    """渲染 PRISMA 2020 计数表（Markdown）。

    结构按 PRISMA 2020 的三个阶段组织（识别 / 筛选 / 纳入），
    排除理由单独成表——审稿人对照流程图时会逐项核查。

    `language` 对应系统契约 `output_language`（`zh | en`）；未知值回退中文。
    """
    labels = _labels(language)
    by_source = _mapping(counts, "identified_by_source")
    # 排序稳定：按来源名升序，保证同一输入多次渲染/golden 完全一致
    source_rows = (
        "\n".join(f"| {source} | {hits} |" for source, hits in sorted(by_source.items()))
        or f"| {labels['none']} | 0 |"
    )

    reasons = _mapping(counts, "exclusion_reasons")
    reason_rows = (
        "\n".join(f"| {reason} | {hits} |" for reason, hits in sorted(reasons.items()))
        or f"| {labels['none']} | 0 |"
    )

    identified_total = _get(counts, "identified_total")
    duplicates = _get(counts, "duplicates_removed")
    screened = _get(counts, "records_screened")
    title_screened = _get(counts, "title_abstract", "screened")
    title_excluded = _get(counts, "title_abstract", "excluded")
    fulltext_assessed = _get(counts, "fulltext", "assessed")
    fulltext_excluded = _get(counts, "fulltext", "excluded")
    included = _get(counts, "included")

    return f"""# {labels["title"]}

## {labels["identification"]}

{labels["source_header"]}
| --- | --- |
{source_rows}

- {labels["identified_total"]}: **{identified_total}**
- {labels["duplicates"]}: **{duplicates}**
- {labels["screened"]}: **{screened}**

## {labels["screening"]}

- {labels["title_screened"]}: **{title_screened}**
- {labels["title_excluded"]}: **{title_excluded}**
- {labels["fulltext_assessed"]}: **{fulltext_assessed}**
- {labels["fulltext_excluded"]}: **{fulltext_excluded}**

### {labels["exclusion_reasons"]}

| {labels["reason_col"]} | {labels["count_col"]} |
| --- | --- |
{reason_rows}

## {labels["included_section"]}

- {labels["included"]}: **{included}**
"""


def render_prisma_mermaid(counts: dict[str, Any], *, language: str = "zh") -> str:
    """渲染 PRISMA 2020 流程图（Mermaid `flowchart TD`）。

    节点 id 用固定短名（`A`/`B`/…）：节点标签里含中文与数字，
    用固定 id 可避免转义问题，也让 diff 更稳定。

    标签一律用双引号包裹——Mermaid 对未加引号的括号/冒号很敏感，
    加引号是官方推荐的「特殊字符安全写法」（已用真实解析器验证）。
    """
    labels = _labels(language)
    identified = _get(counts, "identified_total")
    duplicates = _get(counts, "duplicates_removed")
    screened = _get(counts, "records_screened")
    title_excluded = _get(counts, "title_abstract", "excluded")
    fulltext_assessed = _get(counts, "fulltext", "assessed")
    fulltext_excluded = _get(counts, "fulltext", "excluded")
    included = _get(counts, "included")

    return f"""flowchart TD
    A["{labels["node_identified"]} n={identified}"] --> B["{labels["node_duplicates"]} n={duplicates}"]
    B --> C["{labels["node_screened"]} n={screened}"]
    C -->|"{labels["edge_excluded"]} n={title_excluded}"| X1["{labels["node_title_excluded"]}"]
    C --> D["{labels["node_fulltext"]} n={fulltext_assessed}"]
    D -->|"{labels["edge_excluded"]} n={fulltext_excluded}"| X2["{labels["node_fulltext_excluded"]}"]
    D --> E["{labels["node_included"]} n={included}"]
"""


__all__ = ["render_prisma_markdown", "render_prisma_mermaid"]
