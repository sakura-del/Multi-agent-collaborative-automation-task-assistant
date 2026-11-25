#!/usr/bin/env bash
# 在 10.0.10.2 上隔离运行全量 Playwright E2E（含 UX Matrix / Reduced Motion / Perf）
#
# 用法：
#   bash scripts/e2e-isolated.sh                    # 全量运行（chromium + mobile-chrome）
#   bash scripts/e2e-isolated.sh e2e/ux-matrix.spec.ts
#   KEEP=1 bash scripts/e2e-isolated.sh             # 失败时保留隔离栈排查
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# 1. 注入 Node 20（Playwright 1.64.1 要求 >= 20）
if [ -d "${HOME}/node20/bin" ]; then
  export PATH="${HOME}/node20/bin:${PATH}"
fi

if ! command -v node >/dev/null 2>&1; then
  echo "[e2e-isolated] 未找到 node，请确认 PATH 或 ~/node20/bin" >&2
  exit 1
fi
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
if [ "${NODE_MAJOR}" -lt 20 ]; then
  echo "[e2e-isolated] 当前 node 版本过低（$(node -v)），需要 >= v20" >&2
  exit 1
fi

# 2. 空闲端口配置与冲突预检（避开 46379 与 48080~48087）
PROJECT="${E2E_PROJECT:-vp-e2e}"
E2E_PORT="${E2E_PORT:-49084}"
E2E_PG_PORT="${E2E_PG_PORT:-45433}"
E2E_REDIS_PORT="${E2E_REDIS_PORT:-46389}"
E2E_CHROMA_PORT="${E2E_CHROMA_PORT:-48012}"

for port in "${E2E_PORT}" "${E2E_PG_PORT}" "${E2E_REDIS_PORT}" "${E2E_CHROMA_PORT}"; do
  if ss -tan 2>/dev/null | awk 'NR>1 {print $4}' | grep -qE ":${port}$"; then
    echo "[e2e-isolated] 端口 ${port} 已被占用（含 TIME-WAIT），请通过环境变量覆盖端口后重试。" >&2
    exit 1
  fi
done

# 3. 一次性密钥与账号环境变量（不落盘进仓库、不硬编码口令）
export JWT_SECRET_KEY="${JWT_SECRET_KEY:-$(openssl rand -hex 32)}"
export SECRETS__ENCRYPTION_KEY="${SECRETS__ENCRYPTION_KEY:-$(openssl rand -hex 32)}"
export POSTGRES_USER="${POSTGRES_USER:-maa}"
export POSTGRES_DB="${POSTGRES_DB:-multi_agent_assistant}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(openssl rand -hex 16)}"

export E2E_DEMO_ACCOUNT="${E2E_DEMO_ACCOUNT:-demo}"
export E2E_DEMO_PASSWORD="${E2E_DEMO_PASSWORD:-$(openssl rand -hex 12)}"
export E2E_SECONDARY_ACCOUNT="${E2E_SECONDARY_ACCOUNT:-e2efull}"
export E2E_SECONDARY_PASSWORD="${E2E_SECONDARY_PASSWORD:-$(openssl rand -hex 12)}"
export E2E_REVIEW_ACCOUNT="${E2E_REVIEW_ACCOUNT:-e2ereview}"
export E2E_RECONNECT_ACCOUNT="${E2E_RECONNECT_ACCOUNT:-e2ereconn}"

OVERRIDE_FILE="$(mktemp "${REPO_ROOT}/.docker-compose.e2e.XXXXXX.yml")"
ENV_FILE="$(mktemp "${REPO_ROOT}/.env.e2e.XXXXXX")"
chmod 600 "${ENV_FILE}"

cat >"${ENV_FILE}" <<EOF
POSTGRES_USER=${POSTGRES_USER}
POSTGRES_DB=${POSTGRES_DB}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
JWT_SECRET_KEY=${JWT_SECRET_KEY}
SECRETS__ENCRYPTION_KEY=${SECRETS__ENCRYPTION_KEY}
PIP_INDEX_URL=${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}
INSTALL_RAG_MODELS=false
EOF

cat >"${OVERRIDE_FILE}" <<EOF
services:
  postgres:
    container_name: ${PROJECT}-postgres
    ports: !override
      - "${E2E_PG_PORT}:5432"

  redis:
    container_name: ${PROJECT}-redis
    ports: !override
      - "${E2E_REDIS_PORT}:6379"

  chroma:
    container_name: ${PROJECT}-chroma
    ports: !override
      - "${E2E_CHROMA_PORT}:8000"

  backend:
    container_name: ${PROJECT}-backend
    build:
      args:
        INSTALL_RAG_MODELS: "false"
        PIP_INDEX_URL: "${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
    environment:
      APP__ENV: "test"
      RATE_LIMIT__ENABLED: "false"
      CAPTCHA__ENABLED: "false"
      EMAIL__REQUIRE_VERIFIED_LOGIN: "false"
      LLM__DEFAULT_PROVIDER: "deterministic"
      LLM__DEFAULT_MODEL: "deterministic"
      LLM__OPENAI_API_KEY: ""
      LLM__DEEPSEEK_API_KEY: ""
      OPENAI_API_KEY: ""
      GLM_API_KEY: ""
      QWEN_API_KEY: ""
      RAG_EMBEDDING_MOCK: "1"
      RAG_RERANKER_MOCK: "1"

  nginx:
    container_name: ${PROJECT}-nginx
    ports: !override
      - "${E2E_PORT}:80"

volumes:
  postgres_data:
    name: ${PROJECT}-postgres-data
  redis_data:
    name: ${PROJECT}-redis-data
  chroma_data:
    name: ${PROJECT}-chroma-data
  uploads_data:
    name: ${PROJECT}-uploads-data
  artifacts_data:
    name: ${PROJECT}-artifacts-data
  model_cache:
    name: ${PROJECT}-model-cache
EOF

compose() {
  docker compose -p "${PROJECT}" --env-file "${ENV_FILE}" -f "${REPO_ROOT}/docker-compose.yml" -f "${OVERRIDE_FILE}" "$@"
}

cleanup() {
  if [ "${KEEP:-0}" = "1" ]; then
    echo "[e2e-isolated] KEEP=1，保留隔离栈。手动清理："
    echo "  docker compose -p ${PROJECT} --env-file ${ENV_FILE} -f docker-compose.yml -f ${OVERRIDE_FILE} down --volumes --remove-orphans"
    return
  fi
  echo "[e2e-isolated] 清理隔离栈与临时凭据..."
  compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -f "${OVERRIDE_FILE}" "${ENV_FILE}"
}
trap cleanup EXIT INT TERM

echo "[e2e-isolated] 启动隔离栈 (${PROJECT}, nginx=127.0.0.1:${E2E_PORT})..."
if ! compose up -d --build; then
  echo "[e2e-isolated] Compose 启动失败" >&2
  compose logs --tail 60 backend nginx >&2 || true
  exit 1
fi

READY_URL="http://127.0.0.1:${E2E_PORT}/api/v1/health/ready"
READY_OK=0
for _ in $(seq 1 90); do
  if curl -fsS "${READY_URL}" 2>/dev/null | grep -qE '"(ready|degraded)"'; then
    READY_OK=1
    break
  fi
  sleep 2
done

if [ "${READY_OK}" != "1" ]; then
  echo "[e2e-isolated] 后端未在预期时间内就绪: ${READY_URL}" >&2
  compose logs --tail 80 backend nginx >&2 || true
  exit 1
fi

echo "[e2e-isolated] 注入幂等 E2E 种子数据..."
compose exec -T \
  -e E2E_DEMO_ACCOUNT \
  -e E2E_DEMO_PASSWORD \
  -e E2E_SECONDARY_ACCOUNT \
  -e E2E_SECONDARY_PASSWORD \
  -e E2E_REVIEW_ACCOUNT \
  -e E2E_RECONNECT_ACCOUNT \
  backend python -m scripts.seed_e2e_data

echo "[e2e-isolated] 执行 Playwright E2E..."
cd "${REPO_ROOT}/frontend"
E2E_BASE_URL="http://127.0.0.1:${E2E_PORT}" \
  npx playwright test "$@"
