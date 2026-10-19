"""自定义样式注册（Issue #4 M2 / 计划 §1.4）：上传的 `.csl` 必须能被渲染器解析。

M1 的 `StyleRegistry` 只会从 `styles/` 资产目录发现样式。M2 起用户上传的样式
落在**每用户独立目录**，注册表需要能在进程内登记它们，使
`resolve()` / `meta()` / `get()` 对自定义样式与预置样式**行为完全一致**。

本文件锁定：
1. `register_custom(style_id, path)` 后可 resolve/meta/get，且 meta 走同一套准入推断；
2. 自定义样式与预置样式**同 ID 冲突**时不得覆盖预置（预置优先，显式拒绝）；
3. `unregister_custom` 幂等；
4. 注册的自定义样式能被 `CitationRenderer` 真正用于渲染（端到端）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.exceptions import ValidationException
from app.tools.csl.styles import StyleRegistry, StyleRejected

MINI_STYLE = """<?xml version="1.0" encoding="utf-8"?>
<style xmlns="http://purl.org/net/xbiblio/csl" class="in-text" version="1.0"
       default-locale="en-US">
  <info>
    <title>实验室自定义样式</title>
    <id>https://example.org/styles/lab-custom</id>
  </info>
  <citation>
    <layout>
      <text variable="title"/>
    </layout>
  </citation>
  <bibliography>
    <layout>
      <text variable="title"/>
    </layout>
  </bibliography>
</style>
"""


def _write_style(tmp_path: Path, name: str = "lab-custom.csl", content: str = MINI_STYLE) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


class TestRegisterCustom:
    def test_registered_style_resolves_and_exposes_meta(self, tmp_path: Path) -> None:
        registry = StyleRegistry()
        path = _write_style(tmp_path)

        registry.register_custom("lab-custom", path)

        assert registry.resolve("lab-custom") == "lab-custom"
        meta = registry.meta("lab-custom")
        assert meta.style_id == "lab-custom"
        assert meta.title == "实验室自定义样式"
        assert meta.path == path
        assert meta.is_default is False
        assert registry.get("lab-custom") == path

    def test_registered_style_is_not_listed_as_preset(self, tmp_path: Path) -> None:
        """`list_styles()` 仍是预置样式（协议不变），自定义由 API 层合并。"""
        registry = StyleRegistry()
        registry.register_custom("lab-custom", _write_style(tmp_path))

        ids = [meta.style_id for meta in registry.list_styles()]

        assert "lab-custom" not in ids
        assert len(ids) == 5

    def test_register_is_idempotent(self, tmp_path: Path) -> None:
        registry = StyleRegistry()
        path = _write_style(tmp_path)

        registry.register_custom("lab-custom", path)
        registry.register_custom("lab-custom", path)

        assert registry.resolve("lab-custom") == "lab-custom"

    def test_register_rejects_preset_collision(self, tmp_path: Path) -> None:
        """预置样式优先：不得用上传文件覆盖 `apa`。"""
        registry = StyleRegistry()

        with pytest.raises(ValidationException):
            registry.register_custom("apa", _write_style(tmp_path))

        assert registry.meta("apa").title.startswith("APA")

    def test_register_rejects_invalid_style(self, tmp_path: Path) -> None:
        """准入校验不通过的文件不得被注册。"""
        registry = StyleRegistry()
        bad = _write_style(tmp_path, "bad.csl", "<style><info></style>")

        with pytest.raises(StyleRejected):
            registry.register_custom("bad", bad)

        with pytest.raises(ValidationException):
            registry.resolve("bad")

    def test_register_rejects_csl_m_style(self, tmp_path: Path) -> None:
        csl_m = MINI_STYLE.replace("<citation>", "<institution/><citation>")
        registry = StyleRegistry()

        with pytest.raises(StyleRejected):
            registry.register_custom("lab-custom", _write_style(tmp_path, content=csl_m))

    def test_register_rejects_missing_file(self, tmp_path: Path) -> None:
        registry = StyleRegistry()

        with pytest.raises(StyleRejected):
            registry.register_custom("lab-custom", tmp_path / "missing.csl")

    def test_unregister_removes_style_and_is_idempotent(self, tmp_path: Path) -> None:
        registry = StyleRegistry()
        registry.register_custom("lab-custom", _write_style(tmp_path))

        registry.unregister_custom("lab-custom")
        registry.unregister_custom("lab-custom")  # 幂等

        with pytest.raises(ValidationException):
            registry.resolve("lab-custom")


class TestCustomStyleRendering:
    def test_custom_style_is_used_by_renderer(self, tmp_path: Path) -> None:
        """端到端：注册后的自定义样式能被渲染器真正使用。"""
        from app.tools.csl.models import CslItem
        from app.tools.csl.renderer import CitationRenderer

        registry = StyleRegistry()
        registry.register_custom("lab-custom", _write_style(tmp_path))

        item = CslItem(
            id="src:1",
            type="article-journal",
            title="自定义样式渲染验证",
            issued_year=2024,
            language="zh-CN",
        )

        rendered = CitationRenderer(resolver=registry).render([item], style_id="lab-custom")

        assert rendered.entries
        assert "自定义样式渲染验证" in rendered.entries[0]
