#!/usr/bin/env python3
"""把一条文本推到已配置的告警渠道（给 CI / 手工运维用）。

用法：
    echo '内容' | notify.py
    notify.py '内容'
未配置 WECOM_WEBHOOK_URL 时只打印提示并返回 0——CI 里不该因为"还没配通知"
就把构建判红。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from watchdog import send_wecom  # noqa: E402


def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    if not text.strip():
        print("空消息，跳过")
        return 0
    url = os.environ.get("WECOM_WEBHOOK_URL", "").strip()
    if not url:
        print("未配置 WECOM_WEBHOOK_URL，跳过通知")
        return 0
    ok, detail = send_wecom(url, text)
    print("已发送" if ok else f"发送失败：{detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
