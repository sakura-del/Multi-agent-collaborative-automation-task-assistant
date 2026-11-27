"""文献综述真实统计图表生成（matplotlib 300 DPI，PNG 输出）。"""

from __future__ import annotations

import io
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

from app.tools.templates.rich_blocks import FigureAsset

#: 「2.2 技术路线」章节标题（允许 "## 2.2 技术路线" / "## 技术路线" 等写法）
_ROADMAP_HEADING_RE = re.compile(r"^(?P<hashes>#{2,4})\s*(?:\d+(?:\.\d+)*[.、．]?\s*)?技术路线\s*$")
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


def _char_width(char: str) -> int:
    """中文/全角标点按 2 个视觉宽度，其余按 1 个宽度。"""
    return 2 if "\u4e00" <= char <= "\u9fff" or "\u3000" <= char <= "\u303f" else 1


def _wrap_cjk(text: str, width: int = 24, *, max_lines: int = 3) -> list[str]:
    """按视觉字符宽折行（中文按 2 宽、ASCII 按 1 宽），确保绝不溢出框边界。"""
    lines: list[str] = []
    buf: list[str] = []
    acc = 0
    for char in text.strip():
        char_width = _char_width(char)
        if acc + char_width > width and buf:
            lines.append("".join(buf))
            buf = [char]
            acc = char_width
        else:
            buf.append(char)
            acc += char_width
    if buf:
        lines.append("".join(buf))
    if len(lines) <= max_lines:
        return lines
    # 截断时补省略号，保证读者能看出内容被折叠（同时把末行收窄一个字符宽度）
    visible = lines[:max_lines]
    last = visible[-1]
    while last and _visual_width(last) + 1 > width:
        last = last[:-1]
    visible[-1] = f"{last}…"
    return visible


def _visual_width(text: str) -> int:
    """计算一行文本的视觉宽度（中文/全角按 2，其余按 1）。"""
    return sum(_char_width(char) for char in text)


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
    steps: Sequence[tuple[str, str] | tuple[str, str, str]],
    *,
    asset_id: str = "tech_roadmap",
) -> FigureAsset | None:
    """绘制三栏泳道式学术技术路线图（300 DPI PNG，严格折行零溢出）。

    - 左栏：阶段编号 + 阶段名（深蓝底白字）；
    - 中栏：核心技术与实施内容（按中文字符宽严格折行）；
    - 右栏：阶段交付/目标（浅灰虚线框）；
    - 右侧虚线闭环箭头表示阶段间迭代优化，黑白打印依然清晰。
    """
    if len(steps) < _MIN_ROADMAP_STEPS:
        return None

    normalized: list[tuple[str, str, str]] = []
    for item in list(steps)[:_MAX_ROADMAP_STEPS]:
        if len(item) >= 3:  # type: ignore[arg-type]
            title, core, deliverable = str(item[0]), str(item[1]), str(item[2])
        else:
            title, detail = str(item[0]), str(item[1])
            parts = [part.strip() for part in re.split(r"[。；;]", detail) if part.strip()]
            core = parts[0] if parts else detail
            deliverable = parts[1] if len(parts) > 1 else "阶段验证与里程碑评审"
        title = re.sub(r"[（(][^）)]*[）)]", "", title).strip() or title.strip()
        normalized.append((title, core, deliverable))
    if len(normalized) < _MIN_ROADMAP_STEPS:
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

    count = len(normalized)
    row_h = 1.35
    gap = 0.45
    total_h = count * (row_h + gap) + 0.6

    fig, ax = plt.subplots(figsize=(7.2, max(4.2, count * 1.25)), dpi=300)
    ax.set_xlim(0, 12.2)
    ax.set_ylim(0, total_h)
    ax.axis("off")

    # 顶部三栏表头引导
    ax.text(
        1.35,
        total_h - 0.25,
        "研究阶段",
        ha="center",
        va="center",
        fontsize=9.5,
        fontweight="bold",
        color="#1F4E78",
    )
    ax.text(
        5.35,
        total_h - 0.25,
        "核心技术与实施内容",
        ha="center",
        va="center",
        fontsize=9.5,
        fontweight="bold",
        color="#1F4E78",
    )
    ax.text(
        10.05,
        total_h - 0.25,
        "阶段交付与目标",
        ha="center",
        va="center",
        fontsize=9.5,
        fontweight="bold",
        color="#1F4E78",
    )

    top_y = total_h - 0.65
    for idx, (title, core, deliverable) in enumerate(normalized):
        y = top_y - (idx + 1) * row_h - idx * gap

        # 左栏：阶段编号 + 阶段名（深蓝底白字）
        left_box = FancyBboxPatch(
            (0.25, y),
            2.2,
            row_h,
            boxstyle="round,pad=0.04,rounding_size=0.12",
            linewidth=0,
            facecolor="#1F4E78",
        )
        ax.add_patch(left_box)
        ax.text(
            1.35,
            y + row_h * 0.68,
            f"阶段 {idx + 1}",
            ha="center",
            va="center",
            fontsize=8.5,
            color="#D9E2EC",
            fontweight="bold",
        )
        title_wrapped = "\n".join(_wrap_cjk(title, width=12, max_lines=2))
        ax.text(
            1.35,
            y + row_h * 0.34,
            title_wrapped,
            ha="center",
            va="center",
            fontsize=9.0,
            color="#FFFFFF",
            fontweight="bold",
            linespacing=1.2,
        )

        # 中栏：核心技术内容（严格折行）
        mid_box = FancyBboxPatch(
            (2.75, y),
            5.2,
            row_h,
            boxstyle="round,pad=0.04,rounding_size=0.12",
            linewidth=1.0,
            edgecolor="#1F4E78",
            facecolor="#F7FAFC",
        )
        ax.add_patch(mid_box)
        core_lines = "\n".join(_wrap_cjk(core, width=30, max_lines=3))
        ax.text(
            2.98,
            y + row_h / 2,
            core_lines,
            ha="left",
            va="center",
            fontsize=8.4,
            color="#102A43",
            linespacing=1.3,
        )

        # 右栏：阶段交付/验证指标（浅灰虚线框）
        right_box = FancyBboxPatch(
            (8.35, y),
            3.3,
            row_h,
            boxstyle="round,pad=0.04,rounding_size=0.12",
            linewidth=0.9,
            linestyle="--",
            edgecolor="#627D98",
            facecolor="#FFFFFF",
        )
        ax.add_patch(right_box)
        deliv_lines = "\n".join(_wrap_cjk(deliverable, width=18, max_lines=3))
        ax.text(
            8.55,
            y + row_h / 2,
            deliv_lines,
            ha="left",
            va="center",
            fontsize=8.2,
            color="#334E68",
            linespacing=1.3,
        )

        # 横向连接虚线（中栏 → 右栏）
        ax.add_patch(
            FancyArrowPatch(
                (7.98, y + row_h / 2),
                (8.32, y + row_h / 2),
                arrowstyle="-|>,head_width=3,head_length=4",
                color="#627D98",
                linewidth=0.9,
            )
        )

        # 纵向主流程箭头（左栏向下）
        if idx < count - 1:
            ax.add_patch(
                FancyArrowPatch(
                    (1.35, y - 0.02),
                    (1.35, y - gap + 0.04),
                    arrowstyle="-|>,head_width=4,head_length=5",
                    color="#1F4E78",
                    linewidth=1.3,
                )
            )

    # 最右侧反馈闭环箭头（末阶段 → 第 2 阶段迭代优化）
    if count >= 3:
        y_bottom = top_y - count * row_h - (count - 1) * gap + row_h / 2
        y_target = top_y - 2 * row_h - gap + row_h / 2
        ax.plot(
            [11.7, 12.0, 12.0],
            [y_bottom, y_bottom, y_target],
            linestyle=":",
            color="#486581",
            linewidth=1.0,
        )
        ax.add_patch(
            FancyArrowPatch(
                (12.0, y_target),
                (11.68, y_target),
                arrowstyle="-|>,head_width=3,head_length=4",
                color="#486581",
                linewidth=1.0,
            )
        )
        ax.text(
            12.08,
            (y_bottom + y_target) / 2,
            "迭代优化",
            rotation=90,
            ha="center",
            va="center",
            fontsize=7.5,
            color="#486581",
        )

    fig.tight_layout(pad=0.15)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=300)
    plt.close(fig)

    return FigureAsset(
        asset_id=asset_id,
        image_bytes=buf.getvalue(),
        caption_zh="本文研究技术路线图",
        caption_en="Technical Roadmap of the Proposed Research",
        width_cm=14.2,
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
