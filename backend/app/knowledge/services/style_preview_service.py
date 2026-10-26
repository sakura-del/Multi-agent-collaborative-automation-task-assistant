"""引用样式预览服务（2026-10-26 样式页重设计）。

## 为什么需要「预览」

实测（服务器 + 真实浏览器）：样式页 6 个样式**只有名字和元信息**
（`citation-number · citation-order · zh-CN`），页面上没有任何预览入口。

而 `gbt7714-2025-numeric` 与 `gbt7714-2015-numeric` 的 numbering / ordering /
locale **完全相同**（实测元信息只有 5 种不同组合 / 6 个样式），光看元信息
**分不出区别**。用户选样式时看不到成品长什么样。

## 为什么渲染放在后端

渲染必须走 citeproc + 真实 `.csl`。前端自写格式化器 = 第二份 CSL 实现，
必然与导出不一致 —— 用户会看到「预览是这样、下载出来不是」，比没有预览更糟。
导出走的就是同一个 `CitationRenderer`，预览复用它是唯一能保证一致的做法。

## 样例文献为什么是固定的两条

预览是**样式之间**的比较，不是用户数据的展示：
- 固定样例 → 6 个样式的输出可以直接横向对比，差异全来自样式本身；
- 中英各一条 → 能同时看出 locale 差异（GB/T 7714 的中文著录与 APA 的英文著录
  格式差异最大），以及作者名的处理方式（全大写 / 缩写 / `&`）。

**不要**改成「取用户最近任务里的文献」：那样每个用户看到的预览不同，
无法判断「这个样式长什么样」，而且空库用户会看到空预览。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.schemas.citation_style import CitationStylePreview, CitationStylePreviews
from app.tools.csl.models import CslItem, CslName
from app.tools.csl.renderer import CitationRenderer
from app.tools.csl.styles import StyleRegistry

logger = get_logger(__name__)

__all__ = ["SAMPLE_ITEMS", "StylePreviewService"]


def _sample_items() -> list[CslItem]:
    """固定样例文献：中英各一条，覆盖期刊论文与会议论文两种类型。

    字段刻意填满（卷/期/页/DOI），这样各样式在「缺字段时省略标点」的差异
    也能看出来——只给题名的话所有样式长得差不多。
    """
    return [
        CslItem(
            id="sample-zh-journal",
            type="article-journal",
            title="检索增强生成评估方法综述",
            author=[
                CslName(family="牛", given="耕田"),
                CslName(family="李", given="四"),
            ],
            container_title="计算机学报",
            issued_year=2024,
            volume="47",
            issue="3",
            page="1-20",
            language="zh-CN",
        ),
        CslItem(
            id="sample-en-conference",
            type="paper-conference",
            title="Ragas: Automated Evaluation of Retrieval Augmented Generation",
            author=[
                CslName(family="Es", given="Shahul"),
                CslName(family="James", given="Jithin"),
            ],
            container_title="Proceedings of EACL",
            issued_year=2024,
            page="150-158",
            doi="10.18653/v1/2024.eacl-demo.16",
            language="en-US",
        ),
    ]


#: 固定样例文献（模块级常量：前端可据 `sample_count` 说明预览口径）。
SAMPLE_ITEMS: list[CslItem] = _sample_items()


@dataclass
class _PreviewTarget:
    """待预览的样式（来自列表接口，已含 source / is_default）。"""

    style_id: str
    title: str
    source: str
    is_default: bool = False


@dataclass
class StylePreviewService:
    """把「样式列表」渲染成「每个样式一条真实预览」。

    `renderer` / `registry` 可注入（测试用 fake resolver，避免依赖真实 `.csl` 资产）；
    缺省时用生产实现。
    """

    registry: StyleRegistry | None = None
    renderer: CitationRenderer | None = None
    items: list[CslItem] = field(default_factory=lambda: list(SAMPLE_ITEMS))

    def __post_init__(self) -> None:
        if self.registry is None:
            self.registry = StyleRegistry()
        if self.renderer is None:
            self.renderer = CitationRenderer(resolver=self.registry)

    def build(self, targets: list[_PreviewTarget]) -> CitationStylePreviews:
        """为每个目标样式渲染预览；**单个样式失败不拖垮整批**。"""
        renderer = self.renderer
        assert renderer is not None  # __post_init__ 已保证

        previews: list[CitationStylePreview] = []
        for target in targets:
            try:
                result = renderer.render(self.items, style_id=target.style_id)
                previews.append(
                    CitationStylePreview(
                        style_id=target.style_id,
                        title=target.title,
                        source="custom" if target.source == "custom" else "preset",
                        is_default=target.is_default,
                        entries=list(result.entries),
                        markers=dict(result.marker_by_item_id),
                        warnings=list(result.warnings),
                    )
                )
            except Exception as exc:
                # 未知样式 / 自定义样式文件被删：只让**这一条**降级，
                # 其余样式照常预览（否则一个坏样式会让整页没有预览）
                logger.warning(
                    "style_preview_failed",
                    style_id=target.style_id,
                    error=str(exc),
                )
                previews.append(
                    CitationStylePreview(
                        style_id=target.style_id,
                        title=target.title,
                        source="custom" if target.source == "custom" else "preset",
                        is_default=target.is_default,
                        entries=[],
                        markers={},
                        warnings=[f"预览渲染失败：{exc}"],
                    )
                )
        return CitationStylePreviews(previews=previews, sample_count=len(self.items))
