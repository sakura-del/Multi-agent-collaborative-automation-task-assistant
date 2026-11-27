"""文献综述真实统计图表生成（matplotlib 300 DPI，PNG 输出）。"""

from __future__ import annotations

import io
from collections import Counter
from collections.abc import Sequence
from typing import Any

from app.tools.templates.rich_blocks import FigureAsset


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
