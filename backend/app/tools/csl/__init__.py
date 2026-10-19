"""CSL 引用渲染包（Issue #4 / ADR 0001 Option C）。

模块划分：

    models.py     CSL-JSON 契约（CslItem / CslName）、映射（LiteratureRecord / bibliography 条目）、
                  StyleMeta 与 StyleResolver 协议、GB/T 7714 文献类型标识
    styles.py     预置样式注册表（StyleRegistry）+ 样式准入校验（CSL-M 扫描、显式拒绝）
    renderer.py   渲染核心：逐条 locale 分组渲染 + 应用层编号/排序 + `[[cite:ref_id]]` 替换
    exporters.py  CSL-JSON → BibTeX / RIS 文本导出
    styles/       样式资产（自研 GB/T 7714—2025 + vendored 官方样式 + 署名说明）

设计约束（不得违反）：
- 只用标准 CSL 1.0.2 渲染，不接受 CSL-M（**显式拒绝**，不静默降级，见 ADR §4.1）；
- 双语靠**应用层按条目 language 逐条切换 locale** 实现，不依赖 citeproc-js；
- 编号与排序由本包负责（引擎会忽略预设 citation-number，见 ADR §5）。
"""

from __future__ import annotations

__all__: list[str] = []
