#!/usr/bin/env python3
"""按改动范围挑测试（Issue #11 C 组后：CI 不再每次跑全量）。

## 为什么不再每次全量

3209 条用例在本地要 19 分钟，在 GitHub 2 核 runner 上超过 45 分钟：
- 每个 push 都等一次，反馈慢到没人愿意用；
- 真出问题时反而不看结果，因为"反正要等一小时"。

但"少跑测试"不等于"少验证"。做法是把**全量**挪到每天一次（`nightly.yml`），
push/PR 只跑**与改动相关**的那部分 + 一组固定的快速冒烟。

## 选片规则（保守优先）

| 改动 | 跑什么 |
| --- | --- |
| 只有 `docs/**`、`*.md` | 不跑后端测试（只跑 lint） |
| `backend/app/core/**`、`db/**`、`main.py`、`alembic/**`、`config/**`、`pyproject.toml` | **全量**（这些是全局前提，任何用例都可能受影响） |
| `backend/app/<模块>/**` | 该模块的测试 + 固定冒烟 |
| `backend/tests/<x>.py` | 该文件自身 + 固定冒烟 |
| `deploy/**`、`ops/**` | 锁配置的测试（网关头、防火墙、CORS、监控） |
| `frontend/**` | 不跑后端测试（前端有独立 job） |
| 其它 `backend/app/**`（没有映射表覆盖的新模块） | **全量**（宁可多跑，不可漏跑） |

## 用法

    # 看这次改动会跑哪些测试（不执行）
    python scripts/ci/select_tests.py --base origin/main

    # 直接跑
    python scripts/ci/run_selected_tests.py --base origin/main

    # CI 里把选中项写成文件，交给下一步
    python scripts/ci/select_tests.py --base "$BEFORE" --format paths > /tmp/tests.txt
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from fnmatch import fnmatch

#: 每个 push 都跑的最小冒烟集：配置加载 + 健康检查。合起来约 1~2 秒，
#: 目的是"应用能不能起来"，不承担业务覆盖。
ALWAYS_TESTS: tuple[str, ...] = (
    "tests/test_config.py",
    "tests/test_health.py",
)

#: 命中就全量。这些是**全局前提**：改了它们，任何用例都可能受影响，
#: 逐个推断映射表只会给人虚假的安全感。
FULL_TRIGGERS: tuple[str, ...] = (
    "backend/app/core/*",
    "backend/app/db/*",
    "backend/app/main.py",
    "backend/app/api/deps.py",
    "backend/alembic/*",
    "backend/pyproject.toml",
    "backend/tests/conftest.py",
    "config/*",
)

#: 纯文档：只跑 lint，不跑测试。
DOC_PATTERNS: tuple[str, ...] = (
    "docs/*",
    "*.md",
    ".workbuddy/*",
    ".github/ISSUE_TEMPLATE/*",
)

#: 前端：由独立的 frontend job 负责，不碰后端测试。
FRONTEND_PATTERNS: tuple[str, ...] = ("frontend/*",)

#: 模块 → 测试文件（glob）。顺序无关，命中即累加。
AREA_TESTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "backend/app/api/v1/endpoints/auth*.py",
        (
            "tests/test_auth.py",
            "tests/test_auth_lifecycle.py",
            "tests/test_refresh_rotation.py",
            "tests/test_login_lockout.py",
            "tests/test_client_ip_identity.py",
            "tests/test_token_invalidation_timezone.py",
        ),
    ),
    (
        "backend/app/api/v1/endpoints/task*.py",
        ("tests/test_task*.py", "tests/test_task_repository.py"),
    ),
    (
        "backend/app/api/v1/endpoints/knowledge*.py",
        ("tests/test_knowledge*.py",),
    ),
    (
        "backend/app/knowledge/*",
        (
            "tests/test_knowledge*.py",
            "tests/test_factory.py",
            "tests/test_embedding*.py",
            "tests/test_vector*.py",
            "tests/test_hybrid_retriever.py",
            "tests/test_index_manager.py",
            "tests/test_reindex*.py",
            "tests/test_document*.py",
        ),
    ),
    (
        "backend/app/agents/*",
        ("tests/test_dag_engine.py", "tests/test_*workflow*.py", "tests/test_*agent*.py"),
    ),
    (
        "backend/app/tools/*",
        (
            "tests/test_csl*.py",
            "tests/test_template*.py",
            "tests/test_reference*.py",
        ),
    ),
    (
        "backend/app/api/v1/endpoints/*.py",
        ("tests/test_health*.py", "tests/test_quota*.py", "tests/test_rate_limit*.py"),
    ),
    # 部署与运维脚本：没有"功能测试"，但有锁配置的断言
    (
        "deploy/*",
        (
            "tests/test_gateway_security_headers.py",
            "tests/test_firewall_cloudflare_only.py",
            "tests/test_cors_origin_allowlist.py",
        ),
    ),
    (
        "ops/*",
        ("tests/test_ops_watchdog.py", "tests/test_firewall_cloudflare_only.py"),
    ),
)



@dataclass
class Selection:
    mode: str  # "none" | "selected" | "full"
    tests: list[str] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {"mode": self.mode, "tests": self.tests, "reason": self.reason}


def _matches(path: str, pattern: str) -> bool:
    """目录模式 `a/*` 视为「a 下任意层级」，其余按 fnmatch 精确匹配。"""
    if pattern.endswith("/*"):
        return path.startswith(pattern[:-1])
    return fnmatch(path, pattern)


def select(changed_paths: list[str]) -> Selection:
    """给定改动文件列表，返回要跑的测试。"""
    paths = [p.strip().lstrip("./") for p in changed_paths if p.strip()]
    if not paths:
        return Selection("selected", list(ALWAYS_TESTS), "没有识别到改动，跑冒烟集")

    backend_paths = [p for p in paths if p.startswith("backend/") and "/tests/" not in p]
    if not backend_paths and all(
        any(_matches(p, pat) for pat in DOC_PATTERNS + FRONTEND_PATTERNS) for p in paths
    ):
        return Selection("none", [], "只有文档/前端改动，不跑后端测试")

    for path in paths:
        for pattern in FULL_TRIGGERS:
            if _matches(path, pattern):
                return Selection("full", [], f"命中全量触发条件：{path}")

    selected: set[str] = set(ALWAYS_TESTS)
    unmapped_backend = False

    for path in paths:
        if path.startswith("backend/tests/") and path.endswith(".py"):
            selected.add(path[len("backend/") :])
            continue

        mapped = False
        for pattern, tests in AREA_TESTS:
            if _matches(path, pattern):
                selected.update(tests)
                mapped = True
        if mapped:
            continue

        # 未覆盖的 backend/app 新模块：不猜，直接全量
        if path.startswith("backend/app/"):
            unmapped_backend = True

    if unmapped_backend:
        return Selection("full", [], "有未映射的 backend/app 模块改动，保守起见跑全量")

    return Selection("selected", sorted(selected), f"{len(paths)} 个改动文件 → {len(selected)} 组测试")


def changed_files(base: str, head: str = "HEAD") -> list[str]:
    """`git diff --name-only base...head`；base 无效时退回 HEAD^。"""
    for rev in (f"{base}...{head}", "HEAD^"):
        try:
            out = subprocess.run(
                ["git", "diff", "--name-only", rev],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
        files = [line.strip() for line in out.stdout.splitlines() if line.strip()]
        if files:
            return files
    return []


def expand_globs(tests: list[str], backend_dir: str = "backend") -> list[str]:
    """把 `tests/test_task*.py` 展开成真实存在的文件（pytest 自己也能处理 glob，
    但这里展开便于日志里看清到底跑了哪些）。"""
    from pathlib import Path

    base = Path(backend_dir)
    expanded: list[str] = []
    for item in tests:
        candidate = base / item
        if "*" in item:
            matches = sorted(str(p.relative_to(base)) for p in base.glob(item))
            expanded.extend(matches)
        elif candidate.exists():
            expanded.append(item)
        else:
            # 不存在就传给 pytest，它会报 "file or directory not found"，
            # 比静默忽略更容易发现映射表写错了
            expanded.append(item)
    # 去重但保序
    seen: set[str] = set()
    result: list[str] = []
    for item in expanded:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按改动范围选测试")
    parser.add_argument("--base", default="origin/main", help="比较基准（默认 origin/main）")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--format", choices=["text", "json", "paths"], default="text")
    parser.add_argument("--backend-dir", default="backend")
    args = parser.parse_args(argv)

    files = changed_files(args.base, args.head)
    selection = select(files)

    if selection.mode == "full":
        payload = {"mode": "full", "tests": [], "reason": selection.reason, "files": files}
    else:
        payload = {
            "mode": selection.mode,
            "tests": expand_globs(selection.tests, args.backend_dir),
            "reason": selection.reason,
            "files": files,
        }

    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.format == "paths":
        for test in payload["tests"]:  # type: ignore[union-attr]
            print(test)
    else:
        print(f"模式: {payload['mode']}")
        print(f"原因: {payload['reason']}")
        if payload["tests"]:
            print("选中:")
            for test in payload["tests"]:  # type: ignore[union-attr]
                print(f"  - {test}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
