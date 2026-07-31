"""Prompt 模板管理系统。

基于 Jinja2 实现，提供：
1. 模板加载：从文件系统或字符串加载
2. 变量注入：Jinja2 渲染时注入上下文变量
3. 模板继承：支持 Jinja2 extends/block 机制
4. 版本管理：每个模板名维护多版本，默认使用 latest
5. A/B 测试：按比例分配不同版本，记录分配结果

设计原则：
- 模板与代码解耦：模板文件放 templates/ 目录，便于非开发人员调整
- 不可变版本：模板版本一旦发布不可修改，保证实验可复现
- 缓存渲染：相同模板+相同变量复用渲染结果（可选）
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.core.exceptions import LLMException, NotFoundException
from app.core.logging import get_logger

logger = get_logger(__name__)

# 模板根目录：backend/app/llm/templates
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


class PromptTemplate:
    """单个 Prompt 模板版本。

    封装模板内容与元信息（版本号、描述、标签），便于版本管理与 A/B 测试。
    """

    def __init__(
        self,
        name: str,
        content: str,
        *,
        version: str = "1.0",
        description: str = "",
        tags: list[str] | None = None,
    ) -> None:
        self.name = name
        self.content = content
        self.version = version
        self.description = description
        self.tags = tags or []

    def __repr__(self) -> str:
        return f"<PromptTemplate {self.name}@{self.version}>"


class PromptManager:
    """Prompt 模板管理器。

    用法：
        pm = PromptManager()
        pm.register("planner.system", "你是一个任务规划 Agent，目标：{{ goal }}")
        prompt = pm.render("planner.system", goal="写文献综述")

    版本管理：
        pm.register("planner.system", "...", version="1.0")
        pm.register("planner.system", "...", version="2.0")
        pm.render("planner.system")  # 默认用 latest（2.0）
        pm.render("planner.system", version="1.0")  # 显式指定旧版本

    A/B 测试：
        pm.register("planner.system", "...", version="1.0", tags=["control"])
        pm.register("planner.system", "...", version="2.0", tags=["experiment"])
        # 按 30% 概率分配实验组
        version = pm.ab_assign("planner.system", ratio={"1.0": 0.7, "2.0": 0.3})
    """

    def __init__(self, template_dir: Path | None = None) -> None:
        # 模板存储：name → {version → PromptTemplate}
        self._templates: dict[str, dict[str, PromptTemplate]] = {}
        # 每个模板的 latest 版本号
        self._latest: dict[str, str] = {}
        # A/B 分配记录：name → {version → count}（统计实际分配次数）
        self._ab_stats: dict[str, dict[str, int]] = {}

        # Jinja2 环境：StrictUndefined 强制所有变量必须注入，避免遗漏
        loader_dir = template_dir or TEMPLATE_DIR
        self._env = Environment(
            loader=FileSystemLoader(str(loader_dir)),
            autoescape=select_autoescape(default=False),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=False,
        )

    # ===== 模板注册与查询 =====

    def register(
        self,
        name: str,
        content: str,
        *,
        version: str = "1.0",
        description: str = "",
        tags: list[str] | None = None,
        set_latest: bool = True,
    ) -> PromptTemplate:
        """注册模板版本。

        参数：
            set_latest: 是否将此版本设为 latest（默认 True）
                       旧版本注册时可设 False，避免覆盖最新版本

        异常：
            LLMException: 同名同版本已存在（避免误覆盖）
        """
        versions = self._templates.setdefault(name, {})
        if version in versions:
            raise LLMException(
                f"模板 {name}@{version} 已存在",
                details={"name": name, "version": version},
            )
        tpl = PromptTemplate(
            name=name,
            content=content,
            version=version,
            description=description,
            tags=tags or [],
        )
        versions[version] = tpl
        if set_latest or name not in self._latest:
            self._latest[name] = version
        logger.info(
            "prompt_registered",
            name=name,
            version=version,
            set_latest=set_latest,
        )
        return tpl

    def load_from_file(
        self,
        name: str,
        file_path: str | Path,
        *,
        version: str = "1.0",
        description: str = "",
    ) -> PromptTemplate:
        """从文件加载模板内容并注册。"""
        path = Path(file_path)
        if not path.exists():
            raise NotFoundException(
                f"模板文件不存在: {path}",
                details={"path": str(path)},
            )
        content = path.read_text(encoding="utf-8")
        return self.register(
            name,
            content,
            version=version,
            description=description or f"从 {path.name} 加载",
        )

    def get_template(self, name: str, *, version: str | None = None) -> PromptTemplate:
        """获取模板，未指定 version 时返回 latest。

        异常：
            NotFoundException: 模板或版本不存在
        """
        if name not in self._templates:
            raise NotFoundException(
                f"模板 {name} 不存在",
                details={"name": name, "available": list(self._templates.keys())},
            )
        ver = version or self._latest.get(name)
        if ver is None or ver not in self._templates[name]:
            raise NotFoundException(
                f"模板 {name}@{version} 不存在",
                details={"name": name, "version": version},
            )
        return self._templates[name][ver]

    def list_templates(self) -> list[dict[str, Any]]:
        """列出所有模板及其版本，供管理界面展示。"""
        result: list[dict[str, Any]] = []
        for name, versions in self._templates.items():
            result.append(
                {
                    "name": name,
                    "latest": self._latest.get(name),
                    "versions": sorted(versions.keys()),
                    "version_count": len(versions),
                }
            )
        return result

    # ===== 渲染 =====

    def render(self, name: str, variables: dict[str, Any] | None = None, *, version: str | None = None) -> str:
        """渲染模板：注入变量并返回最终 Prompt 字符串。

        参数：
            variables: Jinja2 上下文变量
            version: 指定版本，None 时用 latest

        异常：
            NotFoundException: 模板不存在
            LLMException: 渲染失败（变量缺失/语法错误）
        """
        tpl = self.get_template(name, version=version)
        try:
            # from_string 避免与 FileSystemLoader 的命名冲突，支持动态内容渲染
            jinja_tpl = self._env.from_string(tpl.content)
            return jinja_tpl.render(**(variables or {}))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "prompt_render_failed",
                name=name,
                version=tpl.version,
                error=str(exc),
            )
            raise LLMException(
                f"模板渲染失败: {exc}",
                details={"name": name, "version": tpl.version, "error": str(exc)},
            ) from exc

    # ===== A/B 测试 =====

    def ab_assign(
        self,
        name: str,
        ratio: dict[str, float],
        *,
        seed: str | None = None,
    ) -> str:
        """按比例分配 A/B 版本，返回被分配的版本号。

        参数：
            ratio: 版本 → 比例（0~1），所有比例之和应为 1
                   例：{"1.0": 0.7, "2.0": 0.3}
            seed: 随机种子，传入相同 seed 保证可复现（用于实验对照）

        异常：
            LLMException: 比例之和不为 1 或版本不存在
        """
        # 校验比例
        total = sum(ratio.values())
        if abs(total - 1.0) > 0.001:
            raise LLMException(
                f"A/B 比例之和必须为 1，当前为 {total}",
                details={"ratio": ratio, "total": total},
            )
        # 校验版本存在
        for ver in ratio:
            if name not in self._templates or ver not in self._templates[name]:
                raise NotFoundException(
                    f"模板 {name}@{ver} 不存在",
                    details={"name": name, "version": ver},
                )

        # 哈希种子：seed 优先，否则随机
        rng = random.Random(seed) if seed else random.Random()
        # 按比例生成区间，落入哪个区间返回对应版本
        point = rng.random()
        cumulative = 0.0
        for ver, r in ratio.items():
            cumulative += r
            if point <= cumulative:
                # 记录分配统计
                stats = self._ab_stats.setdefault(name, {})
                stats[ver] = stats.get(ver, 0) + 1
                logger.info(
                    "ab_assigned",
                    name=name,
                    version=ver,
                    point=point,
                    seed=seed,
                )
                return ver
        # 浮点精度兜底：返回最后一个版本
        last_ver = list(ratio.keys())[-1]
        stats = self._ab_stats.setdefault(name, {})
        stats[last_ver] = stats.get(last_ver, 0) + 1
        return last_ver

    def get_ab_stats(self, name: str) -> dict[str, int]:
        """获取 A/B 分配统计，供实验分析。"""
        return dict(self._ab_stats.get(name, {}))

    def reset_ab_stats(self, name: str | None = None) -> None:
        """重置 A/B 统计，name=None 时重置全部。"""
        if name is None:
            self._ab_stats.clear()
        else:
            self._ab_stats.pop(name, None)


# ===== 全局单例 =====

_global_pm: PromptManager | None = None


def get_prompt_manager() -> PromptManager:
    """获取全局 PromptManager 单例。"""
    global _global_pm
    if _global_pm is None:
        _global_pm = PromptManager()
    return _global_pm


def seed_hash(*args: str) -> str:
    """生成稳定的哈希种子，用于 A/B 分配的可复现性。

    示例：seed_hash(user_id, task_id) 保证同一用户同一任务始终分配相同版本。
    """
    raw = "|".join(args)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()
