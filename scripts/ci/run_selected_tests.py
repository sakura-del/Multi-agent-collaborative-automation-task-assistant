#!/usr/bin/env python3
"""按改动跑测试：本地和 CI 共用同一条命令。

    python scripts/ci/run_selected_tests.py --base origin/main
    python scripts/ci/run_selected_tests.py --base origin/main --cov     # 需要覆盖率时
    python scripts/ci/run_selected_tests.py --base origin/main --dry-run # 只看选了哪些

## 设计

- **默认不带 `--cov`**：覆盖率只在全量跑时有意义（跑 20 个文件算出的覆盖率毫无意义，
  却会因为低于阈值把 CI 卡红）。全量与覆盖率在 `.github/workflows/nightly.yml`。
- **全量触发时不偷偷缩小范围**：`select_tests.py` 判定 full 就老老实实跑 `tests/`。
- 退出码直接透传 pytest，CI 只看这个。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from select_tests import changed_files, expand_globs, select  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按改动范围跑测试")
    parser.add_argument("--base", default=os.environ.get("CI_BASE_REF", "origin/main"))
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--backend-dir", default="backend")
    parser.add_argument("--cov", action="store_true", help="收集覆盖率（仅全量时有意义）")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-x", "--exitfirst", action="store_true", help="首个失败即停")
    args = parser.parse_args(argv)

    files = changed_files(args.base, args.head)
    selection = select(files)

    print(f"[ci] 改动 {len(files)} 个文件 → 模式 {selection.mode}（{selection.reason}）")
    for path in files[:15]:
        print(f"[ci]   · {path}")
    if len(files) > 15:
        print(f"[ci]   · …另有 {len(files) - 15} 个")

    if selection.mode == "none":
        print("[ci] 只有文档/前端改动，跳过后端测试")
        return 0

    backend = Path(args.backend_dir)
    if selection.mode == "full":
        targets = ["tests"]
        print("[ci] 全量：backend/tests")
    else:
        targets = expand_globs(selection.tests, args.backend_dir)
        print(f"[ci] 选中 {len(targets)} 个测试文件:")
        for target in targets:
            print(f"[ci]   - {target}")
        if not targets:
            print("[ci] 没有选中任何测试，按冒烟集处理")
            targets = ["tests/test_config.py", "tests/test_health.py"]

    command = [
        sys.executable,
        "-m",
        "pytest",
        *targets,
        "-q",
        "-p",
        "no:cacheprovider",
        # 单条用例 5 分钟上限：CI 全量两次在 88% 处硬挂 30 分钟且无栈可看。
        # thread 方法在 C 扩展阻塞时也能打断，并打印全部线程栈。
        "--timeout=300",
        "--timeout-method=thread",
    ]
    if args.cov:
        command += ["--cov=app", "--cov-report=term-missing", "--cov-fail-under=80"]
    if args.exitfirst:
        command.append("-x")

    print(f"[ci] 执行: {' '.join(command)}")
    if args.dry_run:
        return 0
    return subprocess.run(command, cwd=backend).returncode


if __name__ == "__main__":
    sys.exit(main())
