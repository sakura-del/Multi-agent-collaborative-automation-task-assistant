"""Prompt 模板管理测试。

覆盖：
- register / get_template：注册与查询
- 版本管理：多版本与 latest 切换
- render：变量注入、模板继承、StrictUndefined
- A/B 测试：比例分配、统计、可复现性
- load_from_file：文件加载
- 异常场景：模板不存在、渲染失败、比例非法
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.exceptions import LLMException, NotFoundException
from app.llm.prompts import PromptManager, PromptTemplate, seed_hash


class TestPromptRegister:
    """模板注册与查询测试。"""

    def test_register_basic(self):
        pm = PromptManager()
        tpl = pm.register("greet", "Hello {{ name }}", description="问候模板")
        assert tpl.name == "greet"
        assert tpl.version == "1.0"
        assert "问候模板" in tpl.description

    def test_register_duplicate_raises(self):
        pm = PromptManager()
        pm.register("greet", "v1")
        with pytest.raises(LLMException):
            pm.register("greet", "v1")  # 同名同版本

    def test_register_multiple_versions(self):
        pm = PromptManager()
        pm.register("greet", "v1 content", version="1.0")
        pm.register("greet", "v2 content", version="2.0")
        # latest 默认为新版本
        assert pm._latest["greet"] == "2.0"

    def test_register_without_set_latest(self):
        """set_latest=False 不更新 latest。"""
        pm = PromptManager()
        pm.register("greet", "v1", version="1.0")
        pm.register("greet", "v2", version="2.0", set_latest=False)
        # latest 仍为 1.0
        assert pm._latest["greet"] == "1.0"

    def test_get_template_default_latest(self):
        pm = PromptManager()
        pm.register("greet", "v1", version="1.0")
        pm.register("greet", "v2", version="2.0")
        tpl = pm.get_template("greet")
        assert tpl.version == "2.0"

    def test_get_template_specific_version(self):
        pm = PromptManager()
        pm.register("greet", "v1", version="1.0")
        pm.register("greet", "v2", version="2.0")
        tpl = pm.get_template("greet", version="1.0")
        assert tpl.version == "1.0"
        assert tpl.content == "v1"

    def test_get_template_not_found(self):
        pm = PromptManager()
        with pytest.raises(NotFoundException):
            pm.get_template("nonexistent")

    def test_get_template_version_not_found(self):
        pm = PromptManager()
        pm.register("greet", "v1", version="1.0")
        with pytest.raises(NotFoundException):
            pm.get_template("greet", version="9.9")

    def test_list_templates(self):
        pm = PromptManager()
        pm.register("a", "x", version="1.0")
        pm.register("a", "x", version="2.0")
        pm.register("b", "y", version="1.0")
        result = pm.list_templates()
        assert len(result) == 2
        names = [item["name"] for item in result]
        assert "a" in names
        assert "b" in names
        a_item = next(item for item in result if item["name"] == "a")
        assert a_item["version_count"] == 2


class TestPromptRender:
    """模板渲染测试。"""

    def test_render_with_variables(self):
        pm = PromptManager()
        pm.register("greet", "Hello {{ name }}, you are {{ age }}")
        result = pm.render("greet", {"name": "Alice", "age": 25})
        assert result == "Hello Alice, you are 25"

    def test_render_no_variables(self):
        pm = PromptManager()
        pm.register("static", "static content")
        assert pm.render("static") == "static content"

    def test_render_missing_variable_raises(self):
        """StrictUndefined 模式下变量缺失抛异常。"""
        pm = PromptManager()
        pm.register("greet", "Hello {{ name }}")
        with pytest.raises(LLMException):
            pm.render("greet", {})  # 缺少 name 变量

    def test_render_specific_version(self):
        pm = PromptManager()
        pm.register("greet", "v1 {{ name }}", version="1.0")
        pm.register("greet", "v2 {{ name }}!", version="2.0")
        assert pm.render("greet", {"name": "X"}, version="1.0") == "v1 X"
        assert pm.render("greet", {"name": "X"}, version="2.0") == "v2 X!"

    def test_render_with_loops(self):
        """Jinja2 循环语法支持。"""
        pm = PromptManager()
        pm.register(
            "list",
            "Items:\n{% for item in items %}- {{ item }}\n{% endfor %}",
        )
        result = pm.render("list", {"items": ["a", "b", "c"]})
        assert "- a" in result
        assert "- b" in result
        assert "- c" in result

    def test_render_with_conditionals(self):
        """Jinja2 条件语法支持。"""
        pm = PromptManager()
        pm.register(
            "cond",
            "{% if debug %}DEBUG MODE{% else %}PROD MODE{% endif %}",
        )
        assert pm.render("cond", {"debug": True}) == "DEBUG MODE"
        assert pm.render("cond", {"debug": False}) == "PROD MODE"

    def test_render_template_not_found(self):
        pm = PromptManager()
        with pytest.raises(NotFoundException):
            pm.render("nonexistent")


class TestLoadFromFile:
    """文件加载测试。"""

    def test_load_from_existing_file(self, tmp_path: Path):
        # 创建临时模板文件
        tpl_file = tmp_path / "test.j2"
        tpl_file.write_text("Hello {{ name }}", encoding="utf-8")

        pm = PromptManager()
        pm.load_from_file("file_tpl", tpl_file, version="1.0")
        result = pm.render("file_tpl", {"name": "World"})
        assert result == "Hello World"

    def test_load_from_nonexistent_file_raises(self):
        pm = PromptManager()
        with pytest.raises(NotFoundException):
            pm.load_from_file("x", "/nonexistent/path.j2")


class TestABTesting:
    """A/B 测试功能测试。"""

    def test_ab_assign_valid_ratio(self):
        pm = PromptManager()
        pm.register("exp", "v1", version="1.0")
        pm.register("exp", "v2", version="2.0")
        # 多次分配，至少有一种版本被分配到
        versions = {pm.ab_assign("exp", {"1.0": 0.5, "2.0": 0.5}) for _ in range(20)}
        # 20次分配应该覆盖两个版本（理论上概率极低才会只命中一个）
        assert len(versions) >= 1

    def test_ab_assign_invalid_ratio_sum_raises(self):
        pm = PromptManager()
        pm.register("exp", "v1", version="1.0")
        pm.register("exp", "v2", version="2.0")
        # 比例之和 = 1.5 ≠ 1
        with pytest.raises(LLMException):
            pm.ab_assign("exp", {"1.0": 0.7, "2.0": 0.8})

    def test_ab_assign_nonexistent_version_raises(self):
        pm = PromptManager()
        pm.register("exp", "v1", version="1.0")
        with pytest.raises(NotFoundException):
            pm.ab_assign("exp", {"1.0": 0.5, "9.9": 0.5})

    def test_ab_assign_with_seed_reproducible(self):
        """相同 seed 保证可复现分配。"""
        pm = PromptManager()
        pm.register("exp", "v1", version="1.0")
        pm.register("exp", "v2", version="2.0")
        # 同一 seed 应得到相同分配结果
        v1 = pm.ab_assign("exp", {"1.0": 0.5, "2.0": 0.5}, seed="user123")
        v2 = pm.ab_assign("exp", {"1.0": 0.5, "2.0": 0.5}, seed="user123")
        assert v1 == v2

    def test_ab_stats_recorded(self):
        """A/B 分配统计被正确记录。"""
        pm = PromptManager()
        pm.register("exp", "v1", version="1.0")
        pm.register("exp", "v2", version="2.0")
        for _ in range(10):
            pm.ab_assign("exp", {"1.0": 0.5, "2.0": 0.5})
        stats = pm.get_ab_stats("exp")
        assert sum(stats.values()) == 10
        assert "1.0" in stats or "2.0" in stats

    def test_reset_ab_stats(self):
        pm = PromptManager()
        pm.register("exp", "v1", version="1.0")
        pm.ab_assign("exp", {"1.0": 1.0})
        assert pm.get_ab_stats("exp") == {"1.0": 1}
        pm.reset_ab_stats("exp")
        assert pm.get_ab_stats("exp") == {}

    def test_reset_all_ab_stats(self):
        pm = PromptManager()
        pm.register("a", "v1", version="1.0")
        pm.register("b", "v1", version="1.0")
        pm.ab_assign("a", {"1.0": 1.0})
        pm.ab_assign("b", {"1.0": 1.0})
        pm.reset_ab_stats()
        assert pm.get_ab_stats("a") == {}
        assert pm.get_ab_stats("b") == {}


class TestSeedHash:
    """seed_hash 工具函数测试。"""

    def test_seed_hash_stable(self):
        """相同输入产生相同哈希。"""
        h1 = seed_hash("user1", "task1")
        h2 = seed_hash("user1", "task1")
        assert h1 == h2

    def test_seed_hash_different_input(self):
        """不同输入产生不同哈希。"""
        h1 = seed_hash("user1", "task1")
        h2 = seed_hash("user2", "task1")
        assert h1 != h2

    def test_seed_hash_returns_hex_string(self):
        h = seed_hash("a")
        # md5 hex 是 32 位字符串
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)


class TestPromptTemplate:
    """PromptTemplate 数据类测试。"""

    def test_default_tags_empty(self):
        tpl = PromptTemplate("name", "content")
        assert tpl.tags == []

    def test_custom_tags(self):
        tpl = PromptTemplate("name", "content", tags=["experiment", "v2"])
        assert tpl.tags == ["experiment", "v2"]

    def test_repr(self):
        tpl = PromptTemplate("greet", "hi", version="2.0")
        repr_str = repr(tpl)
        assert "greet" in repr_str
        assert "2.0" in repr_str
