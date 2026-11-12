#!/usr/bin/env python3
"""外部可用性探测（Issue #11 C 组，2026-11-12）。

## 为什么需要它

站内自检（`ops/watchdog.py`）活在被监控的机器上：整机宕机、网络中断、安全组改错时，
它连"发通知"这一步都做不到。所以必须有一个**从外部发起**的探测者。
这里用 GitHub Actions 的定时任务（仓库公开 → 分钟数免费），每 5 分钟跑一次本脚本。

## 判定要点

- **必须是应用在答，不是 Cloudflare 在答**：CF 的 5xx 错误页也是 HTTP 响应，
  只看状态码会把「源站挂了被 CF 挡成 502」误判成正常，也会把 CF 的挑战页当成故障。
  所以 API 这条要解析 JSON 且断言 `status == "ready"` 与 `checks` 全 ok。
- **重试后再判定**：单次失败可能是边缘节点抖动或部署瞬间，连续 3 次（间隔 20 秒）都失败
  才报，避免每 5 分钟刷一条假警报。
- **退出码是给 workflow 看的**：0 = 正常，1 = 故障。

用法：
    probe_public.py                 # 只探测，打印结果
    probe_public.py --notify        # 失败时推企业微信（需 WECOM_WEBHOOK_URL）
    probe_public.py --notify --notify-recovery   # 恢复时也推一条
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from watchdog import send_wecom  # noqa: E402  （同目录，脚本方式运行时可用）

API_URL = "https://api.veripaper.me/api/v1/health/ready"
SITE_URL = "https://veripaper.me/"
ATTEMPTS = 3
RETRY_DELAY_SECONDS = 20
TIMEOUT_SECONDS = 15


def fetch(url: str) -> tuple[int, str, dict[str, str]]:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "veripaper-uptime/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode("utf-8", "replace"), dict(response.headers)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        return exc.code, body, dict(exc.headers or {})
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}", {}


def check_api() -> tuple[bool, str]:
    status, body, headers = fetch(API_URL)
    if status != 200:
        via = headers.get("Server", "?")
        return False, f"`/health/ready` 返回 {status or '连接失败'}（Server: {via}）"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return False, f"返回非 JSON（可能被 CF 拦截）：`{body[:120]}`"
    if payload.get("status") != "ready":
        # 先报"哪个依赖坏了"：只说 status=degraded 等于让人自己去猜
        bad = {k: v for k, v in (payload.get("checks") or {}).items() if v != "ok"}
        if bad:
            return False, "，".join(f"{k}={v}" for k, v in bad.items())
        return False, f"`status={payload.get('status')}`"
    bad = {k: v for k, v in (payload.get("checks") or {}).items() if v != "ok"}
    if bad:
        return False, "，".join(f"{k}={v}" for k, v in bad.items())
    return True, "api ok"


def check_site() -> tuple[bool, str]:
    status, body, headers = fetch(SITE_URL)
    if status != 200:
        return False, f"站点返回 {status or '连接失败'}（Server: {headers.get('Server', '?')}）"
    # 只断言"是那个 SPA 的骨架"，不依赖具体文案（文案会改）
    if 'id="app"' not in body and "VeriPaper" not in body:
        return False, "返回 200 但不是应用页面（疑似被拦或落到错误页）"
    return True, "site ok"


def run_checks() -> list[str]:
    problems: list[str] = []
    for checker, label in ((check_api, "API"), (check_site, "站点")):
        last = ""
        for attempt in range(1, ATTEMPTS + 1):
            ok, detail = checker()
            last = detail
            if ok:
                print(f"[probe] {label} 正常（第 {attempt} 次）：{detail}")
                break
            print(f"[probe] {label} 第 {attempt}/{ATTEMPTS} 次失败：{detail}")
            if attempt < ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)
        else:
            problems.append(f"**{label}**：{last}")
    return problems


def main() -> int:
    notify = "--notify" in sys.argv
    notify_recovery = "--notify-recovery" in sys.argv
    webhook = os.environ.get("WECOM_WEBHOOK_URL", "").strip()

    problems = run_checks()
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

    if problems:
        content = (
            "## 🚨 VeriPaper 外部探测失败\n"
            f"> {stamp} ｜ 来自 GitHub Actions（站外视角）\n\n"
            + "\n".join(f"- {p}" for p in problems)
            + "\n\n> 站内自检同时可能已失联，先看服务器是否可达。"
        )
        if notify and webhook:
            ok, detail = send_wecom(webhook, content)
            print(f"[probe] 通知{'已发送' if ok else '发送失败：' + detail}")
        elif notify:
            print("[probe] 未配置 WECOM_WEBHOOK_URL，跳过通知")
        # 供 workflow 读取（写 GitHub Actions 输出文件）
        _write_output("problems", " | ".join(problems))
        _write_output("failed", "true")
        return 1

    print("[probe] 全部正常")
    _write_output("failed", "false")
    if notify_recovery and notify and webhook:
        content = f"## ✅ VeriPaper 外部探测恢复正常\n> {stamp} ｜ 来自 GitHub Actions"
        ok, detail = send_wecom(webhook, content)
        print(f"[probe] 恢复通知{'已发送' if ok else '发送失败：' + detail}")
    return 0


def _write_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{key}={value}\n")
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
