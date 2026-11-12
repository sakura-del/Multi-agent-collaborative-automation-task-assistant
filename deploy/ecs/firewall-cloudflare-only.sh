#!/bin/bash
# 只允许 Cloudflare 回源访问 80/443（Issue #11 M-2 的另一半，2026-11-12）
#
# 为什么需要它：ucloud 的端口发布走 iptables NAT（DOCKER 链），**不经过 UFW 的 INPUT**，
# 所以「开 UFW」并不能阻止别人直连源站 IP。实测（改动前）用 --resolve 直打 47.253.81.6
# 返回 200 —— 任何人知道 IP 就能绕过 Cloudflare 的 WAF / DDoS 防护 / 按真实 IP 的限流。
#
# 做法：在 docker 的 DOCKER-USER 链里限制 80/443 的来源只能是 Cloudflare 网段。
# DOCKER-USER 是 docker 官方留给用户的钩子，在 docker 自己的规则之前求值，
# 所以 `docker compose up` 重建容器不会把规则冲掉（但 docker 重启会重建空链，见 systemd 单元）。
#
# 子命令：
#   apply    应用规则（幂等，先清空本链再重建）
#   refresh  先下载最新 Cloudflare 网段再应用（给定时器用）
#   status   打印当前生效规则
#   revert   清空本链（回滚）

set -euo pipefail

RANGES_FILE="${RANGES_FILE:-/etc/veripaper/cloudflare-ranges.txt}"
PORTS="80,443"
IPT="iptables -w 10"
IPT6="ip6tables -w 10"

log() { echo "[$(date -Is)] $*"; }

# DOCKER-USER 是 FORWARD 上的钩子，**两个方向都会经过**：进来的公网请求、以及容器自己
# 发出去的请求。只按 dport 写 DROP 会把容器出网（调用模型 API / 拉镜像）一起掐掉
# —— 实测症状是 `urllib: Network is unreachable`。
# 所以所有限制规则都必须限定 `-i <公网网卡>`：容器出网的入接口是 br-xxx，不会被匹配。
wan_iface() {
    local iface
    iface="$(ip -4 route show default 2>/dev/null | awk '{print $5; exit}')"
    echo "${iface:-eth0}"
}

download_ranges() {
    local tmp4 tmp6
    tmp4="$(mktemp)"; tmp6="$(mktemp)"
    if curl -fsS -m 20 https://www.cloudflare.com/ips-v4 -o "$tmp4" \
       && curl -fsS -m 20 https://www.cloudflare.com/ips-v6 -o "$tmp6"; then
        {
            cat "$tmp4"; echo
            cat "$tmp6"; echo
        } | sed '/^[[:space:]]*$/d' | tr -d '\r' | sort -u > "$RANGES_FILE.new"
        # 基本体检：条数明显不对（比如下回来一个 HTML 错误页）就不要覆盖现有文件
        local count
        count="$(wc -l < "$RANGES_FILE.new")"
        if [ "$count" -lt 10 ]; then
            log "ERROR 下载到的网段只有 ${count} 条，疑似异常，保留原文件"
            rm -f "$tmp4" "$tmp6" "$RANGES_FILE.new"
            return 1
        fi
        mv "$RANGES_FILE.new" "$RANGES_FILE"
        log "网段已更新：${count} 条"
    else
        log "WARN 下载失败，沿用现有 $RANGES_FILE"
        rm -f "$tmp4" "$tmp6"
        return 1
    fi
    rm -f "$tmp4" "$tmp6"
}

ensure_chain() {
    $IPT  -n -L DOCKER-USER >/dev/null 2>&1 || $IPT  -N DOCKER-USER
    $IPT6 -n -L DOCKER-USER >/dev/null 2>&1 || $IPT6 -N DOCKER-USER
}

apply() {
    if [ ! -s "$RANGES_FILE" ]; then
        log "ERROR 缺少网段文件 $RANGES_FILE，拒绝应用（否则会把全部回源流量挡掉）"
        exit 1
    fi

    ensure_chain

    # 幂等：先清空本链（DOCKER-USER 只有我们在用，docker 重启后也是空的）
    $IPT  -F DOCKER-USER
    $IPT6 -F DOCKER-USER

    # 1) 已建立的连接直接放行（否则正在传输的响应会被切断）
    $IPT  -A DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    $IPT6 -A DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN

    # 2) Cloudflare 网段访问 80/443 放行
    local WAN v4=0 v6=0
    WAN="$(wan_iface)"
    while read -r range; do
        [ -z "$range" ] && continue
        case "$range" in
            *:*)
                $IPT6 -A DOCKER-USER -i "$WAN" -s "$range" -p tcp -m multiport --dports "$PORTS" -j RETURN
                v6=$((v6 + 1))
                ;;
            *)
                $IPT  -A DOCKER-USER -i "$WAN" -s "$range" -p tcp -m multiport --dports "$PORTS" -j RETURN
                v4=$((v4 + 1))
                ;;
        esac
    done < "$RANGES_FILE"

    # 3) 其余来源访问 80/443 一律丢弃（-i 限定，容器出网不受影响）
    $IPT  -A DOCKER-USER -i "$WAN" -p tcp -m multiport --dports "$PORTS" -j DROP
    $IPT6 -A DOCKER-USER -i "$WAN" -p tcp -m multiport --dports "$PORTS" -j DROP

    # 4) 其它流量维持 docker 默认行为（链尾 RETURN，交回 docker 自己的规则）
    $IPT  -A DOCKER-USER -j RETURN
    $IPT6 -A DOCKER-USER -j RETURN

    log "已应用：v4 ${v4} 段 / v6 ${v6} 段放行 $PORTS（仅 -i ${WAN}），其余来源丢弃"

    # 自检：DROP 规则必须是「限定入接口」的形态。少了 -i 会把容器出网一起掐掉，
    # 而这种故障在外部看起来完全正常（网站能开，只有调用模型/外部 API 时才报错）。
    if ! $IPT -S DOCKER-USER | grep -- "-j DROP" | grep -q -- "-i ${WAN}"; then
        log "ERROR DROP 规则缺少 -i ${WAN}，会打断容器出网，拒绝生效"
        exit 1
    fi

    # 与 nginx 的 set_real_ip_from 对齐检查：两边不一致会导致「限流按错的 IP 计数」
    if [ -r "${NGINX_CONF:-}" ]; then
        local conf_n live_n
        conf_n="$(grep -c 'set_real_ip_from' "${NGINX_CONF:-/dev/null}" || true)"
        live_n="$(wc -l < "$RANGES_FILE")"
        if [ "$conf_n" -ne "$live_n" ]; then
            log "WARN nginx 的 set_real_ip_from 有 ${conf_n} 条，本文件有 ${live_n} 条 —— 建议同步"
        fi
    fi
}

revert() {
    ensure_chain
    $IPT  -F DOCKER-USER
    $IPT6 -F DOCKER-USER
    log "已清空 DOCKER-USER（恢复为允许任意来源直连 80/443）"
}

# 自愈：docker 重启会重建空链。systemd 的 PartOf=docker.service 正常情况下会重放，
# 但「重放失败」的表现是**源站重新对全世界开放**，而且没有任何报错。
# 定时器每 15 分钟跑一次这里，只在规则缺失时才重建（正常时不改动任何东西，
# 避免每次都 flush 造成瞬时 fail-open 窗口）。
watch() {
    local WAN
    WAN="$(wan_iface)"
    if $IPT -S DOCKER-USER 2>/dev/null | grep -- "-j DROP" | grep -q -- "-i ${WAN}"; then
        exit 0
    fi
    log "WARN DOCKER-USER 规则缺失（docker 重启后常见），重新应用"
    apply
}

status() {
    echo "== ipv4 DOCKER-USER =="
    $IPT -S DOCKER-USER 2>/dev/null | head -30
    echo "== ipv6 DOCKER-USER =="
    $IPT6 -S DOCKER-USER 2>/dev/null | head -30
}

case "${1:-apply}" in
    apply)   apply ;;
    refresh) download_ranges || true; apply ;;
    revert)  revert ;;
    status)  status ;;
    watch)   watch ;;
    *)
        echo "用法: $0 {apply|refresh|revert|status|watch}" >&2
        exit 2
        ;;
esac
