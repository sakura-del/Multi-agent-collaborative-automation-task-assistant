#!/usr/bin/env bash
# 发布冒烟脚本（Plan 5 Task 3）
#
# 在隔离的 Compose 栈（project=maa-test）上跑完整用户链路，结束后清理自己创建的
# 容器/网络/卷；**不会**触碰生产栈（project=multi-agent-assistant）。
#
# 用法：
#   ./scripts/smoke.sh              # 构建 + 启动 + 冒烟 + 清理
#   KEEP=1 ./scripts/smoke.sh       # 保留栈便于排查（仍打印清理命令）
set -uo pipefail

PROJECT="maa-test"
COMPOSE="docker compose -p ${PROJECT} -f docker-compose.yml -f docker-compose.smoke.yml --profile test"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# 冒烟栈需要 JWT 密钥；未提供时用一次性随机值（不落盘、不出现在日志）
if [ -z "${JWT_SECRET_KEY:-}" ]; then
  export JWT_SECRET_KEY="$(openssl rand -hex 32)"
fi
# Postgres 密码同样使用一次性值
if [ -z "${POSTGRES_PASSWORD:-}" ]; then
  export POSTGRES_PASSWORD="$(openssl rand -hex 16)"
fi

cleanup() {
  if [ "${KEEP:-0}" = "1" ]; then
    echo "保留冒烟栈；手动清理： ${COMPOSE} down --volumes"
    return
  fi
  echo "清理冒烟栈..."
  ${COMPOSE} down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "构建并启动冒烟栈（project=${PROJECT}）..."
${COMPOSE} up -d --build || exit 1

echo "运行冒烟客户端..."
${COMPOSE} run --rm smoke
STATUS=$?

if [ "${STATUS}" -ne 0 ]; then
  echo "冒烟失败，输出最近 80 行服务日志：" >&2
  ${COMPOSE} logs --tail 80 backend nginx >&2 || true
fi

exit "${STATUS}"
