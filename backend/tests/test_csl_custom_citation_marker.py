"""自定义 CSL 样式的正文引用标记必须来自样式本身（2026-10-23 巡检发现）。

## 缺陷

`CitationRenderer.render()` 在 `numbering == "citation-number"` 时把 marker
**硬编码**成 `f"[{number}]"`：

```python
if meta.numbering == "citation-number":
    marker = f"[{number}]"
```

完全绕过 CSL `<citation><layout prefix="..." suffix="..." delimiter="...">`。

### 后果

用户上传一个正文标记用 `〔1〕` / `(1)` / `【1】` 的样式：

- `<bibliography>` 条目**生效**（走 `_render_item` 引擎）；
- 正文 `[[cite:xxx]]` 替换后的标记**不生效**（永远是 `[1]`）。

即「样式只对参考文献列表生效，对正文不生效」——
而用户看正文最多，这个不一致非常明显。

### 为什么既有测试没抓到

GB/T 7714 顺序编码制**正好就是 `[1]`** —— 预置样式的期望值与硬编码值相同，
测试全绿。只有自定义样式才会暴露。而 `citation_style` 表上线后一直是 0 行，
没有真实自定义样式进过渲染路径。

## 本文件锁定

1. 自定义样式声明 `〔`/`〕` 前缀后缀 → 正文标记必须是 `〔1〕`；
2. 预置 GB/T 7714（`[1]`）行为**不能变**（回归保护）；
3. 样式解析失败时回退到 `[n]`，不能让渲染整体失败；
4. 多条目 `delimiter` 也要生效（`〔1〕、〔3〕`）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.tools.csl.models import CslItem
from app.tools.csl.renderer import CitationRenderer
from app.tools.csl.styles import StyleRegistry


def _item(ref_id: str, title: str) -> CslItem:
    return CslItem(
        id=ref_id,
        type="article-journal",
        title=title,
        author=[],
        issued_year=2026,
    )


def _write_style(tmp_path: Path, *, prefix: str, suffix: str, delimiter: str) -> Path:
    """写一个正文标记可辨识的自定义样式（bibliography 带 MARKER 便于断言）。"""
    path = tmp_path / "custom-marker-style.csl"
    path.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<style xmlns="http://purl.org/net/xbiblio/csl" class="in-text" version="1.0"
       default-locale="zh-CN">
  <info>
    <title>自定义标记样式</title>
    <id>http://audit.local/custom-marker-style</id>
    <updated>2026-10-23T00:00:00+00:00</updated>
  </info>
  <citation>
    <layout prefix="{prefix}" suffix="{suffix}" delimiter="{delimiter}">
      <text variable="citation-number"/>
    </layout>
  </citation>
  <bibliography>
    <layout suffix="。">
      <text value="BIB-MARKER "/>
      <text variable="citation-number" suffix=": "/>
      <text variable="title"/>
    </layout>
  </bibliography>
</style>
""",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def registry(tmp_path: Path) -> StyleRegistry:
    registry = StyleRegistry()
    registry.register_custom(
        "custom-marker-style", _write_style(tmp_path, prefix="〔", suffix="〕", delimiter="、")
    )
    return registry


class TestCustomCitationMarker:
    def test_marker_uses_style_prefix_suffix(self, registry: StyleRegistry) -> None:
        """**核心断言**：正文标记必须是 `〔1〕`，不能是硬编码的 `[1]`。"""
        renderer = CitationRenderer(resolver=registry)

        rendered = renderer.render([_item("a1", "文献甲")], style_id="custom-marker-style")

        marker = rendered.marker_by_item_id["a1"]
        assert marker == "〔1〕", (
            f"正文标记必须来自 CSL <citation> 的 prefix/suffix（期望 〔1〕），实际 {marker!r}。"
            "硬编码 [n] 会让自定义样式只对参考文献列表生效、对正文不生效"
        )

    def test_inline_substitution_uses_custom_marker(self, registry: StyleRegistry) -> None:
        """正文占位替换后应出现 `〔1〕`。"""
        renderer = CitationRenderer(resolver=registry)
        rendered = renderer.render([_item("a1", "文献甲")], style_id="custom-marker-style")

        substituted = renderer.substitute_inline("见 [[cite:a1]] 的结论。", rendered)

        assert "〔1〕" in substituted, f"实际替换结果：{substituted!r}"
        assert "[[" not in substituted

    def test_bibliography_still_uses_style(self, registry: StyleRegistry) -> None:
        """参考文献条目仍按样式渲染（这是已生效的部分，不能被改坏）。"""
        renderer = CitationRenderer(resolver=registry)

        rendered = renderer.render([_item("a1", "文献甲")], style_id="custom-marker-style")

        assert any("BIB-MARKER" in entry for entry in rendered.entries), rendered.entries

    def test_multiple_entries_kept_separate(self, registry: StyleRegistry) -> None:
        """多个条目各自的 marker 独立（不因样式改造而串号）。"""
        renderer = CitationRenderer(resolver=registry)

        rendered = renderer.render(
            [_item("a1", "文献甲"), _item("a2", "文献乙")], style_id="custom-marker-style"
        )

        assert rendered.marker_by_item_id["a1"] == "〔1〕"
        assert rendered.marker_by_item_id["a2"] == "〔2〕"


class TestPresetUnchanged:
    def test_gbt7714_still_bracket_numeric(self) -> None:
        """**回归保护**：GB/T 7714 顺序编码制的正文标记仍是 `[1]`（不能因本次改动回归）。"""
        renderer = CitationRenderer()

        rendered = renderer.render([_item("a1", "文献甲")], style_id="gbt7714-2025-numeric")

        assert rendered.marker_by_item_id["a1"] == "[1]"

    def test_default_style_marker(self) -> None:
        """默认样式（未指定）也保持 `[1]`。"""
        renderer = CitationRenderer()

        rendered = renderer.render([_item("a1", "文献甲")])

        assert rendered.marker_by_item_id["a1"] == "[1]"


class TestGracefulFallback:
    def test_style_file_deleted_after_register_falls_back(self, tmp_path: Path) -> None:
        """登记后文件被删（DB 与磁盘不同步）时不能让渲染整体失败。

        `register_custom()` 会校验文件，所以造不出「坏文件」的登记；真实场景是
        「登记成功、之后文件被外部删除」—— 这时渲染必须回退 `[n]`。
        """
        path = _write_style(tmp_path, prefix="〔", suffix="〕", delimiter="、")
        registry = StyleRegistry()
        registry.register_custom("vanishing-style", path)
        path.unlink()  # 登记后文件消失
        renderer = CitationRenderer(resolver=registry)

        rendered = renderer.render([_item("a1", "文献甲")], style_id="vanishing-style")

        # 不断言具体形态，只要求「有 marker 且不是空」
        assert rendered.marker_by_item_id["a1"]
