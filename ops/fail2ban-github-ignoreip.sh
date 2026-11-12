#!/bin/bash
# 把 GitHub Actions 的出网网段加进 fail2ban 白名单（2026-11-12 CD 被自己封掉）
#
# 事故：CD 用 ssh-keyscan 预扫主机密钥，一次连 5 个连接、其中几次密钥协商失败，
# fail2ban 计到 maxretry 就把 runner IP 封了 —— 而那条**已建立**的部署会话被 DROP，
# 表现为 ssh 客户端 "Timeout, server not responding"，完全看不出是防火墙干的。
#
# GitHub 的 runner IP 是动态的，但官方在 https://api.github.com/meta 公布了网段，
# 这里据此生成白名单。装在服务器 /usr/local/sbin/，建议每周跑一次（GitHub 偶尔加段）。
#
# 用法（服务器上）：veripaper-f2b-ignoreip.sh
set -euo pipefail

CONF=/etc/fail2ban/jail.d/00-ignoreip.local
TMP=$(mktemp)

if curl -fsS -m 20 https://api.github.com/meta -o "$TMP"; then
    RANGES=$(python3 -c "
import json

d = json.load(open('$TMP'))
print(' '.join(d.get('actions', []) + d.get('actions_inbound', {}).get('full', [])))
")
else
    echo "WARN 无法获取 GitHub meta，仅保留默认白名单" >&2
    RANGES=""
fi

BASE="127.0.0.1/8 ::1"
if [ -n "${RANGES// /}" ]; then
    cat > "$CONF" <<CONFEOF
# 由 /usr/local/sbin/veripaper-f2b-ignoreip.sh 生成，请勿手改
# 来源：https://api.github.com/meta 的 actions 网段（GitHub Actions runner 出网）
[DEFAULT]
ignoreip = $BASE $RANGES
CONFEOF
    echo "已写入 $(echo "$RANGES" | wc -w) 个 GitHub 网段到 $CONF"
else
    cat > "$CONF" <<CONFEOF
[DEFAULT]
ignoreip = $BASE
CONFEOF
fi

rm -f "$TMP"
fail2ban-client reload >/dev/null 2>&1 && echo "fail2ban 已重载"
