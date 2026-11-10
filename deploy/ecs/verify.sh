#!/usr/bin/env bash
# ECS 部署验收：健康、文档标题、CORS 预检、关键接口计时。
#
# 用法：
#   bash deploy/ecs/verify.sh                    # 走本机网关 127.0.0.1:80
#   BASE=https://api.veripaper.me bash deploy/ecs/verify.sh
# 注意：不要用 127.0.0.1:8000 —— backend 只在容器网络内 expose，宿主上没监听
set -uo pipefail

BASE="${BASE:-http://127.0.0.1}"
ORIGIN="${ORIGIN:-https://multi-agent-assistant.pages.dev}"
ACCOUNT="${ACCOUNT:-demo}"
PASSWORD="${PASSWORD:-demo12345}"

fail=0
ok()   { printf '  \033[32m✔\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✖\033[0m %s\n' "$1"; fail=$((fail + 1)); }

echo "== 1) 健康 =="
body=$(curl -s -m 20 "$BASE/api/v1/health/ready" || true)
case "$body" in
  *'"status":"ready"'*) ok "ready: $body" ;;
  *) bad "health: ${body:-<empty>}" ;;
esac

echo "== 2) OpenAPI 标题 =="
title=$(curl -s -m 20 "$BASE/openapi.json" | grep -o '"title":"[^"]*"' | head -n1 || true)
case "$title" in
  *VeriPaper*) ok "$title" ;;
  *) bad "title: ${title:-<empty>}" ;;
esac

echo "== 3) CORS 预检（Origin=$ORIGIN） =="
hdrs=$(curl -s -o /dev/null -D - -m 20 -X OPTIONS "$BASE/api/v1/tasks" \
  -H "Origin: $ORIGIN" -H "Access-Control-Request-Method: GET" || true)
case "$hdrs" in
  *"access-control-allow-origin: $ORIGIN"*) ok "allow-origin 命中" ;;
  *) bad "预检未返回 allow-origin" ;;
esac

echo "== 4) 登录 + 接口计时 =="
token=$(curl -s -m 30 -X POST "$BASE/api/v1/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"account\":\"$ACCOUNT\",\"password\":\"$PASSWORD\"}" \
  | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4 || true)

if [ -z "${token:-}" ]; then
  bad "登录失败（跳过计时）"
else
  ok "登录成功"
  t=$(curl -s -o /dev/null -m 60 -w '%{time_total}' -H "Authorization: Bearer $token" "$BASE/api/v1/tasks" || echo 999)
  echo "     /tasks       ${t}s   （旧环境 4.2s，目标 <0.5s）"
  t2=$(curl -s -o /dev/null -m 60 -w '%{time_total}' -X POST "$BASE/api/v1/knowledge/search" \
    -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
    -d '{"query":"检索增强生成","top_k":3}' || echo 999)
  echo "     /knowledge/search ${t2}s（旧环境 6.2s，目标 <1s）"
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "结果：PASS"
else
  echo "结果：FAIL（$fail 项）"
fi
exit "$fail"
