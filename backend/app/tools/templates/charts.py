"""文献综述真实统计图表生成（matplotlib 300 DPI，PNG 输出）。"""

from __future__ import annotations

import io
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

from app.tools.templates.rich_blocks import FigureAsset

#: 「2.2 技术路线」章节标题（允许 "## 2.2 技术路线" / "## 技术路线" 等写法）
_ROADMAP_HEADING_RE = re.compile(r"^(?P<hashes>#{2,4})\s*(?:2\.2[\s、.．]*)?技术路线\s*$")
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+")
#: 阶段条目：1. / 1、/ （1） / (1) / 阶段一： / 第一步： 等编号写法，
#: 也兼容真实模型常见的「第一阶段为……」「**第二阶段：……**」等叙述式写法（Phase 2 生产实测）
_ROADMAP_STEP_RE = re.compile(
    r"^\s*(?:\*\*)?(?:"
    r"(?P<num>\d{1,2})\s*[.、)．]"
    r"|（(?P<cn_num>\d{1,2})）"
    r"|\((?P<en_num>\d{1,2})\)"
    r"|第(?P<ord_stage>[一二三四五六七八九十\d]{1,3})阶段\s*[为是]?\s*[:：]?"
    r"|阶段(?P<stage>[一二三四五六七八九十\d]{1,3})\s*[为是]?\s*[:：]?"
    r"|第(?P<ord>[一二三四五六七八九十\d]{1,3})步\s*[:：]"
    r")\s*(?P<body>.+)$"
)
_MAX_ROADMAP_STEPS = 6
_MIN_ROADMAP_STEPS = 2


def _wrap_detail(detail: str, *, max_units: int = 82, max_lines: int = 2) -> list[str]:
    """按显示宽度折行（CJK 记 2 个单位），最多 max_lines 行，超出部分用省略号。"""
    lines: list[str] = []
    current: list[str] = []
    width = 0
    consumed = 0
    for index, char in enumerate(detail):
        char_width = 2 if ord(char) > 127 else 1
        if width + char_width > max_units and current:
            lines.append("".join(current))
            current = []
            width = 0
            if len(lines) == max_lines:
                consumed = index
                break
        current.append(char)
        width += char_width
        consumed = index + 1
    if current and len(lines) < max_lines:
        lines.append("".join(current))
    if consumed < len(detail) and lines:
        stripped = lines[-1].rstrip("，。；、,.;:： ")
        while stripped and sum(2 if ord(ch) > 127 else 1 for ch in stripped) > max_units - 2:
            stripped = stripped[:-1].rstrip("，。；、,.;:： ")
        lines[-1] = f"{stripped}…" if stripped else "…"
    return lines or [detail[: max_units // 2]]


def extract_roadmap_steps(markdown: str) -> list[tuple[str, str]]:
    """从开题报告「2.2 技术路线」章节抽取编号阶段（返回 [(阶段名, 要点)]）。

    - 仅在标题命中「技术路线」的二级/三级章节内解析；
    - 少于 2 个阶段时返回空列表，调用方据此跳过插图（绝不画空框图）；
    - 最多取 6 步，避免版面溢出。
    """
    if not markdown:
        return []
    lines = markdown.splitlines()
    section_level: int | None = None
    steps: list[tuple[str, str]] = []
    for line in lines:
        stripped = line.strip()
        heading = _HEADING_RE.match(stripped)
        if heading:
            if section_level is not None and len(heading.group("hashes")) <= section_level:
                break  # 技术路线章节结束
            if section_level is None and _ROADMAP_HEADING_RE.match(
                f"{heading.group('hashes')} {stripped.lstrip('#').strip()}"
            ):
                section_level = len(heading.group("hashes"))
            continue
        if section_level is None:
            continue
        match = _ROADMAP_STEP_RE.match(stripped)
        if not match:
            continue
        body = match.group("body").strip().strip("*").strip()
        if not body:
            continue
        # 阶段名：取冒号 / 破折号 / 逗号 / 括号前的短标题；要点保留全文
        label = re.split(r"[:：,，。;；—–\-（(]", body, maxsplit=1)[0].strip()
        stages = [label or body[:24], body[:180]]
        if len(stages[0]) < 4:
            stages[0] = body[:24]
        steps.append((stages[0], stages[1]))
        if len(steps) >= _MAX_ROADMAP_STEPS:
            break
    if len(steps) < _MIN_ROADMAP_STEPS:
        return []
    return steps


def build_tech_roadmap_figure(
    steps: Sequence[tuple[str, str]],
    *,
    asset_id: str = "tech_roadmap",
) -> FigureAsset | None:
    """根据技术路线阶段绘制纵向流程框图（300 DPI PNG）；阶段不足 2 个返回 None。"""
    cleaned = [(str(name).strip(), str(detail).strip()) for name, detail in steps]
    cleaned = [(name, detail) for name, detail in cleaned if name or detail]
    if len(cleaned) < _MIN_ROADMAP_STEPS:
        return None

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    plt.rcParams["font.sans-serif"] = [
        "Noto Sans CJK SC",
        "WenQuanYi Zen Hei",
        "SimSun",
        "FangSong",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False

    count = len(cleaned)
    box_h = 1.05
    gap = 0.5
    fig_h = count * box_h + (count - 1) * gap + 0.6
    fig, ax = plt.subplots(figsize=(7.0, fig_h), dpi=300)
    ax.set_axis_off()
    ax.set_xlim(0, 10)
    ax.set_ylim(0, count * (box_h + gap) + 0.4)

    for idx, (name, detail) in enumerate(cleaned):
        y = (count - 1 - idx) * (box_h + gap) + 0.2
        box = FancyBboxPatch(
            (0.9, y),
            8.2,
            box_h,
            boxstyle="round,pad=0.02,rounding_size=0.12",
            linewidth=1.1,
            edgecolor="#1F4E78",
            facecolor="#EAF1F8",
        )
        ax.add_patch(box)
        ax.text(
            1.25,
            y + box_h * 0.72,
            f"阶段 {idx + 1}｜{name[:34]}",
            fontsize=9.5,
            fontweight="bold",
            color="#163A59",
            va="center",
        )
        detail_lines = _wrap_detail(detail)
        for line_index, line_text in enumerate(detail_lines):
            ax.text(
                1.25,
                y + box_h * (0.42 - 0.18 * line_index),
                line_text,
                fontsize=8,
                color="#33475B",
                va="center",
            )
        if idx < count - 1:
            arrow = FancyArrowPatch(
                (5.0, y),
                (5.0, y - gap),
                arrowstyle="-|>",
                mutation_scale=12,
                linewidth=1.2,
                color="#1F4E78",
            )
            ax.add_patch(arrow)

    fig.tight_layout(pad=0.2)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=300)
    plt.close(fig)

    return FigureAsset(
        asset_id=asset_id,
        image_bytes=buffer.getvalue(),
        caption_zh="本文研究技术路线图",
        caption_en="Technical Roadmap of the Thesis",
        width_cm=13.5,
    )


def build_year_distribution_figure(
    bibliography: Sequence[dict[str, Any]],
    *,
    asset_id: str = "year_distribution",
) -> FigureAsset | None:
    """根据参考文献真实年份分布绘制学术风格柱状图；有效年份不足 3 条时返回 None。"""
    years: list[int] = []
    for entry in bibliography:
        raw = entry.get("year") or entry.get("issued_year")
        if isinstance(raw, int) and 1950 <= raw <= 2100:
            years.append(raw)
        elif isinstance(raw, str) and raw.strip()[:4].isdigit():
            y = int(raw.strip()[:4])
            if 1950 <= y <= 2100:
                years.append(y)
    if len(years) < 3:
        return None

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = [
        "Noto Sans CJK SC",
        "WenQuanYi Zen Hei",
        "SimSun",
        "FangSong",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False

    counts = sorted(Counter(years).items())
    xs = [str(y) for y, _ in counts]
    ys = [c for _, c in counts]

    fig, ax = plt.subplots(figsize=(6.2, 3.2), dpi=300)
    bars = ax.bar(xs, ys, color="#1F4E78", width=0.55, edgecolor="#163A59", linewidth=0.6)
    ax.set_xlabel("发表年份", fontsize=10)
    ax.set_ylabel("文献数量（篇）", fontsize=10)
    ax.set_title("纳入参考文献发表年份分布", fontsize=11, pad=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.yaxis.grid(True, linestyle="--", alpha=0.35)
    ax.set_axisbelow(True)
    for bar, val in zip(bars, ys, strict=True):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            val + 0.08,
            str(val),
            ha="center",
            va="bottom",
            fontsize=8.5,
        )
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=300)
    plt.close(fig)

    return FigureAsset(
        asset_id=asset_id,
        image_bytes=buf.getvalue(),
        caption_zh="纳入参考文献发表年份分布统计",
        caption_en="Distribution of Included Literature by Publication Year",
        width_cm=13.5,
    )
