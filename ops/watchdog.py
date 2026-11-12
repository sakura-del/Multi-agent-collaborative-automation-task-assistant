#!/usr/bin/env python3
"""生产自检与告警（Issue #11 C 组，2026-11-12）。

## 为什么不塞进后端

检查器必须活在**被检查对象之外**：后端 OOM / 启动失败 / 卡死时，应用内的定时任务
一样死。所以做成宿主机的 systemd timer + 独立脚本，不 import 任何项目代码。

## 为什么只用标准库

它是"最后一层"探测。引入 requests / prometheus_client 之后，一次依赖升级就可能让
告警静默失效——而告警失效本身是**没有症状**的。只用 os/json/urllib/subprocess。

## 为什么还需要外部探测

整机宕机、网络中断、安全组改错时，本脚本连"发通知"这一步都做不到。
外部那一半在 `.github/workflows/uptime.yml`：从 GitHub 机房每 5 分钟探公网。

## 检查项

| key | 判据 | 严重度 |
| --- | --- | --- |
| `api_health` | 回环 `/api/v1/health/ready` 非 200，或 `checks` 里有非 ok | 红 |
| `error_rate` | 窗口内 5xx 占比 ≥ 阈值且请求数达下限 | 红 |
| `disk` | `/` 使用率 ≥ 85%（橙）/ ≥ 92%（红） | 橙/红 |
| `containers` | 有容器非 `Up` 或状态含 `unhealthy` | 红 |
| `backup` | 最近一次成功备份超过 30 小时 | 橙 |

## 状态与去重

状态落盘在 `$STATE_DIR/state.json`：同一条故障只推一次，恢复时推"已恢复"；
持续故障默认每 180 分钟再提醒一次（否则一次性告警会被刷过去）。

用法：
    watchdog.py              # 跑一轮检查（systemd timer 每分钟）
    watchdog.py --digest     # 发一条日报（每天 09:00）
    watchdog.py --dry-run    # 只打印将要发送的内容，不发网络请求
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_ENV_FILE = "/etc/veripaper/notify.env"
DEFAULT_STATE_DIR = "/var/lib/veripaper-watchdog"

#: 企业微信 markdown 消息上限 4096 **字节**（不是字符）。中文一个字 3 字节，
#: 按字符截断会算出 ~11 KB 并被网关直接拒绝——告警就这么丢了。
MAX_MESSAGE_BYTES = 3800


def truncate_bytes(text: str, limit: int = MAX_MESSAGE_BYTES) -> str:
    """按 UTF-8 字节数截断，且不切坏多字节字符。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", "ignore")

SEVERITY_EMOJI = {"red": "🔴", "orange": "🟠"}


@dataclass
class Issue:
    """一条待报告的问题。`key` 是稳定标识，用于状态去重。"""

    key: str
    severity: str
    title: str
    detail: str = ""


@dataclass
class Config:
    webhook_url: str = ""
    state_dir: Path = field(default_factory=lambda: Path(DEFAULT_STATE_DIR))
    health_url: str = "http://127.0.0.1/api/v1/health/ready"
    health_host: str = "api.veripaper.me"
    metrics_url: str = ""
    disk_warn_pct: float = 85.0
    disk_crit_pct: float = 92.0
    error_rate_pct: float = 20.0
    error_min_requests: int = 20
    backup_max_age_hours: float = 30.0
    repeat_minutes: int = 180
    host_label: str = "veripaper"
    http_timeout: float = 8.0


# --------------------------------------------------------------------------- 配置


def load_env_file(path: str | os.PathLike[str]) -> dict[str, str]:
    """读 `KEY=VALUE` 形式的环境文件（systemd EnvironmentFile 兼容）。

    只做最小解析：忽略空行与 `#` 注释、去掉成对引号。**不做变量展开**——
    展开会让 `$` 出现在密钥里时行为不可预测。
    """
    values: dict[str, str] = {}
    p = Path(path)
    if not p.is_file():
        return values
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def build_config(env: dict[str, str] | None = None) -> Config:
    """环境变量优先于配置文件；两种来源都没有就用默认值。"""
    merged = load_env_file(os.environ.get("WATCHDOG_ENV_FILE", DEFAULT_ENV_FILE))
    merged.update({k: v for k, v in (env or {}).items() if v is not None})
    # 进程环境变量优先级最高（systemd 里只放非敏感项）
    for key in (
        "WECOM_WEBHOOK_URL",
        "WATCHDOG_STATE_DIR",
        "WATCHDOG_HEALTH_URL",
        "WATCHDOG_METRICS_URL",
        "WATCHDOG_HOST_LABEL",
    ):
        if os.environ.get(key):
            merged[key] = os.environ[key]

    cfg = Config()
    cfg.webhook_url = merged.get("WECOM_WEBHOOK_URL", "")
    cfg.state_dir = Path(merged.get("WATCHDOG_STATE_DIR") or DEFAULT_STATE_DIR)
    cfg.health_url = merged.get("WATCHDOG_HEALTH_URL", cfg.health_url)
    cfg.metrics_url = merged.get("WATCHDOG_METRICS_URL", "")
    cfg.host_label = merged.get("WATCHDOG_HOST_LABEL", cfg.host_label)
    for attr, key in (
        ("disk_warn_pct", "WATCHDOG_DISK_WARN_PCT"),
        ("disk_crit_pct", "WATCHDOG_DISK_CRIT_PCT"),
        ("error_rate_pct", "WATCHDOG_ERROR_RATE_PCT"),
        ("backup_max_age_hours", "WATCHDOG_BACKUP_MAX_AGE_HOURS"),
    ):
        if merged.get(key):
            try:
                setattr(cfg, attr, float(merged[key]))
            except ValueError:
                pass
    for attr, key in (
        ("error_min_requests", "WATCHDOG_ERROR_MIN_REQUESTS"),
        ("repeat_minutes", "WATCHDOG_REPEAT_MINUTES"),
    ):
        if merged.get(key):
            try:
                setattr(cfg, attr, int(merged[key]))
            except ValueError:
                pass
    return cfg


# --------------------------------------------------------------------------- 采集


def http_get(url: str, *, host: str = "", timeout: float = 8.0) -> tuple[int, str]:
    """返回 (状态码, 正文)。失败一律返回 (0, 错误描述)，不抛异常。"""
    request = urllib.request.Request(url, method="GET")
    if host:
        request.add_header("Host", host)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也要能读到 body
        try:
            return exc.code, exc.read().decode("utf-8", "replace")
        except Exception:
            return exc.code, ""
    except Exception as exc:  # 连接失败 / 超时 / DNS
        return 0, f"{type(exc).__name__}: {exc}"


def check_health(cfg: Config) -> tuple[Issue | None, dict[str, Any]]:
    status, body = http_get(cfg.health_url, host=cfg.health_host, timeout=cfg.http_timeout)
    info: dict[str, Any] = {"status": status}
    if status != 200:
        return Issue("api_health", "red", "健康检查失败", f"`{cfg.health_url}` 返回 {status or '连接失败'}\n{body[:200]}"), info
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return Issue("api_health", "red", "健康检查返回非 JSON", f"`{body[:200]}`"), info
    checks = payload.get("checks") or {}
    bad = {k: v for k, v in checks.items() if v != "ok"}
    info["checks"] = checks
    if payload.get("status") != "ready" or bad:
        detail = "，".join(f"{k}={v}" for k, v in bad.items()) or f"status={payload.get('status')}"
        return Issue("api_health", "red", "依赖不健康", detail), info
    return None, info


def parse_prometheus_counters(text: str, metric: str = "maa_http_requests_total") -> dict[str, float]:
    """把 `metric{...status="500"} 12.0` 聚合成 {"5xx": n, "all": m}。

    只关心状态码首位数字，避免按具体 route 展开（基数会爆）。
    """
    totals = {"all": 0.0, "5xx": 0.0, "4xx": 0.0}
    for line in text.splitlines():
        if not line.startswith(metric + "{") and not line.startswith(metric + " "):
            continue
        try:
            value = float(line.rsplit(" ", 1)[1])
        except (IndexError, ValueError):
            continue
        totals["all"] += value
        if 'status="5' in line:
            totals["5xx"] += value
        elif 'status="4' in line:
            totals["4xx"] += value
    return totals


def compute_error_rate(
    previous: dict[str, float] | None,
    current: dict[str, float],
    *,
    min_requests: int,
    threshold_pct: float,
) -> tuple[float | None, int, int]:
    """返回 (5xx 占比, 5xx 增量, 总增量)。

    计数器是累计值，必须做差。**进程重启会让计数变小**——此时把当前值当作增量
    （保守：宁可多算 NaN 也不产生负数占比）。
    """
    if previous is None:
        return None, 0, 0
    total_delta = current.get("all", 0.0) - previous.get("all", 0.0)
    five_delta = current.get("5xx", 0.0) - previous.get("5xx", 0.0)
    if total_delta < 0 or five_delta < 0:  # 后端重启，计数归零
        total_delta = current.get("all", 0.0)
        five_delta = current.get("5xx", 0.0)
    if total_delta < min_requests:
        return None, int(five_delta), int(total_delta)
    return (five_delta / total_delta) * 100.0, int(five_delta), int(total_delta)


def discover_metrics_url(runner=subprocess.run) -> str:
    """自动找到 backend 容器的 IP。

    不写死 IP：容器重启后地址会变，写死会让「5xx 比例」这项检查**永久静默**，
    而静默的检查比没有检查更危险（你以为在看着）。
    """
    try:
        result = runner(
            [
                "docker",
                "inspect",
                "-f",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                "vp-backend",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return ""
    ip = (result.stdout or "").strip().splitlines()[0].strip() if result.stdout else ""
    return f"http://{ip}:8000/metrics" if ip else ""


def check_error_rate(cfg: Config, state: dict[str, Any]) -> tuple[Issue | None, dict[str, Any]]:
    url = cfg.metrics_url or discover_metrics_url()
    if not url:
        return None, state
    status, body = http_get(url, timeout=cfg.http_timeout)
    if status != 200:
        # 指标取不到不等于服务坏了（可能是容器刚重启），下一轮再看
        return None, state
    current = parse_prometheus_counters(body)
    previous = state.get("metrics_snapshot")
    state["metrics_snapshot"] = current
    ratio, five, total = compute_error_rate(
        previous,
        current,
        min_requests=cfg.error_min_requests,
        threshold_pct=cfg.error_rate_pct,
    )
    if ratio is not None and ratio >= cfg.error_rate_pct:
        return (
            Issue(
                "error_rate",
                "red",
                "5xx 比例过高",
                f"最近一轮 {five}/{total} 次请求失败（{ratio:.0f}%，阈值 {cfg.error_rate_pct:.0f}%）",
            ),
            state,
        )
    return None, state


def check_disk(cfg: Config, path: str = "/") -> Issue | None:
    usage = shutil.disk_usage(path)
    pct = usage.used / usage.total * 100 if usage.total else 0.0
    free_gb = usage.free / 1024**3
    if pct >= cfg.disk_crit_pct:
        return Issue("disk", "red", f"磁盘 {pct:.0f}%（严重）", f"`{path}` 剩余 {free_gb:.1f} GB")
    if pct >= cfg.disk_warn_pct:
        return Issue("disk", "orange", f"磁盘 {pct:.0f}%", f"`{path}` 剩余 {free_gb:.1f} GB（阈值 {cfg.disk_warn_pct:.0f}%）")
    return None


def parse_container_status(output: str) -> list[str]:
    """从 `name\\tstatus` 行里挑出运行异常的名字。"""
    bad: list[str] = []
    for line in output.splitlines():
        if "\t" not in line:
            continue
        name, status = line.split("\t", 1)
        status = status.strip()
        if "unhealthy" in status or not status.startswith("Up"):
            bad.append(f"{name.strip()}({status})")
    return bad


def check_containers(cfg: Config, runner=subprocess.run) -> Issue | None:
    try:
        result = runner(
            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:
        return Issue("containers", "red", "无法查询容器状态", f"{type(exc).__name__}: {exc}")
    if result.returncode != 0:
        return Issue("containers", "red", "无法查询容器状态", (result.stderr or "")[:200])
    bad = parse_container_status(result.stdout)
    if bad:
        return Issue("containers", "red", "容器异常", "、".join(bad[:6]))
    return None


BACKUP_DONE_MARKERS = ("已上传", "上传完成", "uploaded")


def parse_last_backup_ts(log_text: str) -> datetime | None:
    """从备份容器日志里找最后一次成功的 UTC 时间戳。

    日志行形如 `[db-backup] 2026-11-11T11:47:52Z 已上传 s3://...`。
    """
    latest: datetime | None = None
    for line in log_text.splitlines():
        if not any(marker in line for marker in BACKUP_DONE_MARKERS):
            continue
        for token in line.replace("[", " ").split():
            candidate = token.strip().rstrip(":,;")
            if len(candidate) < 19 or candidate[4] != "-" or "T" not in candidate:
                continue
            try:
                parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            if latest is None or parsed > latest:
                latest = parsed
            break
    return latest


def check_backup(cfg: Config, runner=subprocess.run, now: datetime | None = None) -> Issue | None:
    now = now or datetime.now(UTC)
    try:
        result = runner(
            ["docker", "logs", "vp-db-backup", "--since", "168h"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as exc:
        return Issue("backup", "orange", "无法读取备份日志", f"{type(exc).__name__}: {exc}")
    # docker logs 把容器输出写到 stderr
    text = (result.stdout or "") + (result.stderr or "")
    last = parse_last_backup_ts(text)
    if last is None:
        return Issue("backup", "orange", "找不到成功备份记录", "近 7 天日志里没有上传成功标记")
    age_hours = (now - last).total_seconds() / 3600
    if age_hours > cfg.backup_max_age_hours:
        return Issue(
            "backup",
            "orange",
            "备份过期",
            f"最近一次成功备份在 {last:%m-%d %H:%M} UTC（{age_hours:.0f} 小时前，阈值 {cfg.backup_max_age_hours:.0f}h）",
        )
    return None


# --------------------------------------------------------------------------- 通知


def decide_notifications(
    previous: dict[str, Any],
    issues: list[Issue],
    *,
    now: datetime,
    repeat_minutes: int,
) -> tuple[dict[str, Any], list[Issue], list[str]]:
    """对比上一轮状态，决定这一轮要发什么。

    返回 (新状态, 新出现的故障, 已恢复的 key 列表)。
    - 新故障：立即发
    - 持续故障：距上次通知超过 `repeat_minutes` 才再发（防止刷屏但也不能永远静音）
    - 恢复：发一条"已恢复"
    """
    firing: dict[str, Any] = previous.get("firing") or {}
    current_keys = {issue.key: issue for issue in issues}
    new_issues: list[Issue] = []
    recovered: list[str] = []

    for key, issue in current_keys.items():
        record = firing.get(key)
        if record is None:
            firing[key] = {"since": now.isoformat(), "last_notified": now.isoformat(), "severity": issue.severity}
            new_issues.append(issue)
            continue
        last = datetime.fromisoformat(record.get("last_notified", now.isoformat()))
        if (now - last).total_seconds() >= repeat_minutes * 60:
            record["last_notified"] = now.isoformat()
            record["severity"] = issue.severity
            new_issues.append(issue)

    for key in list(firing):
        if key not in current_keys:
            del firing[key]
            recovered.append(key)

    previous["firing"] = firing
    return previous, new_issues, recovered


def format_message(
    *,
    issues: list[Issue],
    recovered: list[str],
    cfg: Config,
    now: datetime,
    digest: str = "",
) -> str:
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    if digest:
        return truncate_bytes(f"## VeriPaper 日报\n> {stamp} ｜ {cfg.host_label}\n\n{digest}")

    if issues:
        lines = [f"## 🚨 VeriPaper 告警（{len(issues)} 项）", f"> {stamp} ｜ {cfg.host_label}", ""]
        for issue in issues:
            emoji = SEVERITY_EMOJI.get(issue.severity, "🔴")
            lines.append(f"**{emoji} {issue.title}**")
            if issue.detail:
                lines.append(issue.detail)
            lines.append("")
        if recovered:
            lines.append(f"同时已恢复：{'、'.join(recovered)}")
        return truncate_bytes("\n".join(lines))

    if recovered:
        lines = [
            "## ✅ VeriPaper 已恢复",
            f"> {stamp} ｜ {cfg.host_label}",
            "",
            f"恢复正常：{'、'.join(recovered)}",
        ]
        return truncate_bytes("\n".join(lines))

    return ""


def send_wecom(webhook_url: str, content: str, *, timeout: float = 8.0) -> tuple[bool, str]:
    """发到企业微信群机器人。返回 (是否成功, 说明)。

    企业微信即使 HTTP 200 也可能返回 errcode != 0（比如 key 被禁用），必须看 body。
    """
    if not webhook_url:
        return False, "未配置 WECOM_WEBHOOK_URL"
    payload = json.dumps({"msgtype": "markdown", "markdown": {"content": content}}).encode()
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            status = response.status
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return status == 200, body[:120]
    if data.get("errcode", 0) != 0:
        return False, f"errcode={data.get('errcode')} {data.get('errmsg', '')}"
    return True, "ok"


# --------------------------------------------------------------------------- 主流程


def load_state(state_dir: Path) -> dict[str, Any]:
    path = state_dir / "state.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # 状态文件坏了就当没有：宁可重复告警，也不能因为文件问题停止告警
        return {}


def save_state(state_dir: Path, state: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "state.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def collect_issues(cfg: Config, state: dict[str, Any]) -> tuple[list[Issue], dict[str, Any]]:
    issues: list[Issue] = []
    issue, health_info = check_health(cfg)
    if issue:
        issues.append(issue)
    issue, state = check_error_rate(cfg, state)
    if issue:
        issues.append(issue)
    # 健康检查已失败时，其余检查只会产生连带噪音，跳过更利于定位
    if health_info.get("status") != 200:
        return issues, state
    for candidate in (check_disk(cfg), check_containers(cfg), check_backup(cfg)):
        if candidate:
            issues.append(candidate)
    return issues, state


def render_digest(cfg: Config, state: dict[str, Any], now: datetime) -> str:
    parts = [f"健康检查正常，当前无告警项。"]
    snapshot = state.get("metrics_snapshot") or {}
    if snapshot:
        parts.append(f"累计请求 {int(snapshot.get('all', 0))}，其中 5xx {int(snapshot.get('5xx', 0))}。")
    try:
        usage = shutil.disk_usage("/")
        parts.append(f"磁盘 {usage.used / usage.total * 100:.0f}%（剩 {usage.free / 1024**3:.1f} GB）。")
    except OSError:
        pass
    parts.append(f"这是每日心跳：若你连续两天收不到它，说明站内自检已经挂了。")
    return " ".join(parts)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in args
    digest_mode = "--digest" in args

    cfg = build_config()
    now = datetime.now(UTC)
    state = load_state(cfg.state_dir)

    if digest_mode:
        content = format_message(
            issues=[],
            recovered=[],
            cfg=cfg,
            now=now,
            digest=render_digest(cfg, state, now),
        )
        return _deliver(cfg, content, dry_run=dry_run)

    issues, state = collect_issues(cfg, state)
    state, new_issues, recovered = decide_notifications(
        state,
        issues,
        now=now,
        repeat_minutes=cfg.repeat_minutes,
    )
    save_state(cfg.state_dir, state)

    if not new_issues and not recovered:
        return 0
    content = format_message(issues=new_issues, recovered=recovered, cfg=cfg, now=now)
    return _deliver(cfg, content, dry_run=dry_run)


def _deliver(cfg: Config, content: str, *, dry_run: bool) -> int:
    if not content:
        return 0
    if dry_run:
        print(content)
        return 0
    if not cfg.webhook_url:
        # 没配渠道也不能静默：写日志留证据，但返回 0 避免 systemd 反复报 failed
        print(f"[watchdog] 未配置 WECOM_WEBHOOK_URL，仅记录：\n{content}", file=sys.stderr)
        return 0
    ok, detail = send_wecom(cfg.webhook_url, content)
    if not ok:
        print(f"[watchdog] 发送失败：{detail}", file=sys.stderr)
        return 1
    print(f"[watchdog] 已发送（{len(content)} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
