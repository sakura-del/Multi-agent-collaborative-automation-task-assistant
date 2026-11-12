"""锁定「源站只接受 Cloudflare 回源」的部署配置（Issue #11 M-2 的另一半）。

这些断言防的是两类真实事故：

1. **`DOCKER-USER` 规则少了 `-i <公网网卡>`**：会把容器出网一起掐掉。外部看起来完全正常
   （网站能开、健康检查 200），只有真正调模型/外部 API 时才 `Network is unreachable`。
2. **网段与 nginx 的 `set_real_ip_from` 不一致**：限流会按错的 IP 计数（#50 那个坑）。

部署脚本本身跑在服务器上，这里只能校验**仓库里的配置形态**；运行期效果见
`docs/runbooks/ecs-deployment.md` §19.6 的实测证据。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "ecs"
SCRIPT = DEPLOY / "firewall-cloudflare-only.sh"
RANGES = DEPLOY / "cloudflare-ranges.txt"
SERVICE = DEPLOY / "veripaper-firewall.service"
TIMER = DEPLOY / "veripaper-firewall.timer"
NGINX = DEPLOY / "nginx-api-tls.conf"


@pytest.fixture(scope="module")
def script() -> str:
    assert SCRIPT.is_file(), f"缺少 {SCRIPT}"
    return SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ranges() -> list[str]:
    assert RANGES.is_file(), f"缺少 {RANGES}"
    return [
        line.strip() for line in RANGES.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


class TestScriptShape:
    def test_drop_rule_is_limited_to_public_interface(self, script: str) -> None:
        """DROP 规则必须带 `-i`：少了它容器出网会被掐断（见 AGENTS 铁律 #53）。"""
        # 只看真正的规则行（`-A DOCKER-USER ... -j DROP`），排除脚本里那段自检 grep
        drop_lines = [
            line
            for line in script.splitlines()
            if "-A DOCKER-USER" in line and "-j DROP" in line and not line.strip().startswith("#")
        ]
        assert drop_lines, "脚本里找不到 DROP 规则"
        for line in drop_lines:
            assert '-i "$WAN"' in line, f'DROP 规则缺少 -i "$WAN"：{line.strip()}'

    def test_wan_interface_derived_from_default_route(self, script: str) -> None:
        """网卡名不能写死：换机型/多网卡时写死会让规则永不匹配（静默失效）。"""
        assert "ip -4 route show default" in script
        assert "wan_iface" in script

    def test_apply_is_idempotent(self, script: str) -> None:
        """先清空再重建：重复 apply 不能叠加出重复规则。"""
        assert "DOCKER-USER" in script
        assert re.search(r"\$IPT\s+-F DOCKER-USER", script), "apply 必须先清空链"

    def test_refuses_when_ranges_missing(self, script: str) -> None:
        """网段文件缺失时必须拒绝应用，否则会把全部回源流量挡掉。"""
        assert '-s "$RANGES_FILE"' in script
        assert "拒绝应用" in script

    def test_has_revert_path(self, script: str) -> None:
        """必须有一键放开（应急时先把站点救回来，再查原因）。"""
        assert "revert)" in script
        assert "revert" in script.lower()

    def test_self_check_rejects_rule_without_iface(self, script: str) -> None:
        """脚本自己要能发现「DROP 缺 -i」，而不是等人事后拍脑袋。"""
        assert "会打断容器出网" in script

    def test_has_watchdog_subcommand(self, script: str) -> None:
        """docker 重启会重建空链；规则丢失时源站就对全世界开放，必须能自愈。"""
        assert "watch)" in script
        assert "重新应用" in script
        # 正常时不能改动规则：每次都 flush 会制造瞬时 fail-open 窗口
        watch_block = script.split("watch() {", 1)[1].split("\n}", 1)[0]
        assert "exit 0" in watch_block, "规则正常时必须直接退出，不要重建"


class TestRanges:
    def test_has_v4_and_v6(self, ranges: list[str]) -> None:
        assert any(":" in r for r in ranges), "缺少 IPv6 网段"
        assert any(":" not in r for r in ranges), "缺少 IPv4 网段"

    def test_every_entry_is_cidr(self, ranges: list[str]) -> None:
        for entry in ranges:
            assert re.fullmatch(r"[0-9a-fA-F:.]+/\d{1,3}", entry), f"不是合法 CIDR：{entry}"

    def test_matches_nginx_real_ip_trust(self, ranges: list[str]) -> None:
        """两边必须逐条一致：不一致会让 nginx 采信的头与限流用的 IP 对不上。"""
        assert NGINX.is_file()
        conf = NGINX.read_text(encoding="utf-8")
        nginx_ranges = set(re.findall(r"set_real_ip_from\s+([0-9a-fA-F:./]+)", conf))
        assert nginx_ranges == set(ranges), (
            f"nginx 独有：{sorted(nginx_ranges - set(ranges))}；"
            f"ranges 文件独有：{sorted(set(ranges) - nginx_ranges)}"
        )


class TestSystemdUnits:
    def test_service_runs_after_docker(self) -> None:
        """docker 启动时会重建空链，晚于 docker 才能覆盖上。"""
        unit = SERVICE.read_text(encoding="utf-8")
        assert "After=docker.service" in unit
        assert "PartOf=docker.service" in unit, "docker 重启后规则会丢，必须跟着重放"

    def test_service_verifies_drop_present(self) -> None:
        unit = SERVICE.read_text(encoding="utf-8")
        assert "ExecStartPost" in unit and "-j DROP" in unit

    def test_timer_refreshes_periodically(self) -> None:
        timer = TIMER.read_text(encoding="utf-8")
        assert "OnCalendar=" in timer
        assert "Persistent=true" in timer, "关机错过时间点后要补跑"

    def test_watchdog_runs_frequently(self) -> None:
        """巡检必须比「网段刷新」频繁得多：丢规则是安全回退，丢网段只是精度下降。"""
        watchdog = DEPLOY / "veripaper-firewall-watchdog.timer"
        assert watchdog.is_file(), "缺少看门狗定时器"
        text = watchdog.read_text(encoding="utf-8")
        assert "OnCalendar=*:0/" in text, "看门狗应按分钟级间隔运行"

    def test_watchdog_service_calls_watch(self) -> None:
        unit = DEPLOY / "veripaper-firewall-watchdog.service"
        assert unit.is_file(), "缺少看门狗单元"
        assert "firewall.sh watch" in unit.read_text(encoding="utf-8")
