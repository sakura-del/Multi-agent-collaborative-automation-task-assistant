"""仓库内不得残留真实口令（Issue #10 验收第 4 条）。

## 背景

2026-11-11 安全排查（`docs/security/2026-11-11-security-audit.md` H-1/H-2）：
`demo` 与 `e2efull` / `e2ereconn` 的口令明文写在 `AGENTS.md` 和多份文档、
测试用例里，等于公开；而 `demo` 在生产是 **admin**，名下 86 个任务、11 篇文档、
4 个工作区、1 个 API Key。

轮换口令只解决"当下"——文档和用例里的明文会被后来的人再抄一遍。
所以这里把"仓库里不许出现真实口令"变成一条持续的测试：
任何人再把手上的口令贴进文档、脚本或 spec，CI 立刻红。

## 为什么本文件的字面量是拼接的

扫描范围包含本文件自身。如果这里直接写出那个口令，测试会因为
"唯一的命中就是自己"而永远无法通过——那等于把规则写成注释。
拼接之后，`grep` 在整个仓库（含本文件）都找不到该串，规则才是自洽的。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

# backend/tests/xxx.py -> backend -> <repo root>
REPO_ROOT = Path(__file__).resolve().parents[2]

# 曾经真实有效、已公开泄露的口令。轮换后它们不该再有任何一处出现。
# 拼接只为让本文件自身不命中（见模块 docstring）。
BANNED_LITERALS: tuple[str, ...] = ("demo" + "12345", "e2e-password-" + "2026")

# 只扫文本文件；二进制的向量/图片/归档没有"口令"含义
TEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".vue",
        ".js",
        ".mjs",
        ".cjs",
        ".json",
        ".yaml",
        ".yml",
        ".md",
        ".sh",
        ".txt",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".example",
        ".html",
        ".css",
        ".sql",
        ".env",
    }
)

# 第三方依赖、构建产物、测试产物：不是我们维护的内容
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        "htmlcov",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".runtime",
        ".artifacts",
        "coverage",
        ".vite",
        ".idea",
        ".vscode",
    }
)

MAX_BYTES = 2 * 1024 * 1024


def _iter_text_files(root: Path) -> Iterator[Path]:
    """遍历仓库文本文件，跳过依赖/产物目录与超大文件。"""
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
        except OSError:  # 权限/竞态，视为不可读
            continue
        yield path


@pytest.fixture(scope="module")
def text_files() -> list[Path]:
    files = list(_iter_text_files(REPO_ROOT))
    # 扫描面本身要有下限：路径算错时应该报错，而不是"0 个文件所以很干净"
    assert len(files) > 100, f"扫描面异常，只找到 {len(files)} 个文本文件"
    return files


def test_banned_list_is_non_empty() -> None:
    """防止有人把列表清空让测试永远绿。"""
    assert len(BANNED_LITERALS) == 2
    assert all(len(item) >= 8 for item in BANNED_LITERALS)


def test_repo_contains_no_banned_password_literal(text_files: list[Path]) -> None:
    """全仓库（含本文件）不得出现任何已泄露口令。"""
    hits: list[str] = []
    for path in text_files:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for literal in BANNED_LITERALS:
            if literal in content:
                line_no = content[: content.index(literal)].count("\n") + 1
                hits.append(f"{path.relative_to(REPO_ROOT)}:{line_no}")

    assert (
        hits == []
    ), "仓库里仍有已泄露口令的明文，请改为环境变量或“见部署环境变量”表述：\n" + "\n".join(hits)


def test_verify_script_requires_explicit_password() -> None:
    """部署验收脚本不能内置默认口令——它是"拿来就能跑"的便利，也是泄露点。"""
    text = (REPO_ROOT / "deploy" / "ecs" / "verify.sh").read_text(encoding="utf-8")

    assert (
        'PASSWORD="${PASSWORD:-' not in text
    ), "verify.sh 仍在使用默认口令，请改为必须显式传入 PASSWORD"
    # 必须给出可执行提示，否则脚本会变成一个"跑不起来也没人知道为什么"的坑
    assert "PASSWORD" in text


def test_frontend_e2e_reads_credentials_from_env() -> None:
    """前端 E2E 必须从环境变量取口令，不能在 spec 里写死。"""
    e2e_dir = REPO_ROOT / "frontend" / "e2e"
    helper = e2e_dir / "credentials.ts"

    assert helper.is_file(), "缺少 frontend/e2e/credentials.ts（E2E 凭据统一入口）"

    leaked = [
        str(path.relative_to(REPO_ROOT))
        for path in e2e_dir.glob("*.spec.ts")
        if 'password: "' in path.read_text(encoding="utf-8")
        and "E2E_" not in path.read_text(encoding="utf-8")
    ]
    assert leaked == [], f"这些 spec 仍在硬编码口令：{leaked}"
