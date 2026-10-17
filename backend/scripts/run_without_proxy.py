"""清理畸形代理变量的开发包装器。

背景：某些 Windows 开发环境把 IPv6 回环写成 `[::1]` 放进 `NO_PROXY`，httpx 会把每个
NO_PROXY 条目当 URL 解析并抛 `InvalidURL: Invalid port: ':1]'`。同时外部模型端点又
**需要**代理（`http://127.0.0.1:7894`），所以不能整体删除代理变量——本包装器只剔除
带方括号的非法 NO_PROXY 条目，保留 HTTP(S)_PROXY。

用法：
    python scripts/run_without_proxy.py scripts/prepare_demo_corpus.py --dry-run
    python scripts/run_without_proxy.py -m app.cli run-literature-demo --offline --check
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

PROXY_KEYS = {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    # 让 `-m app.cli` 这类模块入口可用（与 backend/ 下直接运行一致）
    sys.path.insert(0, str(BACKEND_DIR))


def scrub_proxy_env() -> list[str]:
    """剔除 NO_PROXY 中无法解析的条目（含方括号），返回形如 `NO_PROXY=[::1]` 的说明。"""
    changes: list[str] = []
    for key in list(os.environ):
        if key.lower() != "no_proxy":
            continue
        raw = os.environ.get(key, "")
        kept = [item for item in raw.split(",") if item and "[" not in item and "]" not in item]
        if len(kept) != len([item for item in raw.split(",") if item]):
            changes.append(f"{key} 去除非法条目")
        os.environ[key] = ",".join(kept)
    return changes


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "用法: python scripts/run_without_proxy.py <脚本路径|-m 模块> [参数...]",
            file=sys.stderr,
        )
        return 2
    removed = scrub_proxy_env()
    print(f"已清理代理变量: {sorted(removed)}", file=sys.stderr)
    if sys.argv[1] == "-m":
        if len(sys.argv) < 3:
            print("缺少模块名", file=sys.stderr)
            return 2
        module = sys.argv[2]
        sys.argv = [module, *sys.argv[3:]]
        runpy.run_module(module, run_name="__main__")
        return 0
    target = sys.argv[1]
    sys.argv = sys.argv[1:]
    runpy.run_path(target, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
