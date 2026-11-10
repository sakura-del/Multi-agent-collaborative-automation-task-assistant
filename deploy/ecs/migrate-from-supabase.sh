#!/usr/bin/env bash
# Supabase(17.11) → ECS 自建 PG17 的全量迁移与三重校验。
#
# 为什么用 postgres:17 容器：宿主上的 pg_dump 版本低于服务端会直接
# `server version mismatch`（源库是 17.11）。
# 为什么不加 --single-transaction：跨洋链路长事务会被 SSL EOF 断开，
# 逐语句提交 + 失败重跑更可靠（--clean 幂等）。
#
# 用法（ECS 上，仓库根目录）：
#   bash deploy/ecs/migrate-from-supabase.sh
set -euo pipefail

cd "$(dirname "$0")/../.."

# 不用 `source .env`：值里有正则括号（CORS__ALLOW_ORIGIN_REGEX=^https://(...)…），
# bash 会把它当语法错误。这里只按需取值，且去掉可能残留的 \\r。
get_env() {
    grep -E "^$1=" .env | tail -n1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

POSTGRES_USER=$(get_env POSTGRES_USER)
POSTGRES_PASSWORD=$(get_env POSTGRES_PASSWORD)
POSTGRES_DB=$(get_env POSTGRES_DB)
SUPABASE_PG_HOST=$(get_env SUPABASE_PG_HOST)
SUPABASE_PG_USER=$(get_env SUPABASE_PG_USER)
SUPABASE_PG_PASSWORD=$(get_env SUPABASE_PG_PASSWORD)

: "${POSTGRES_USER:?}" "${POSTGRES_PASSWORD:?}" "${POSTGRES_DB:?}"
: "${SUPABASE_PG_HOST:?}" "${SUPABASE_PG_USER:?}" "${SUPABASE_PG_PASSWORD:?}"

SUP_PASS="${SUPABASE_PG_PASSWORD}"
SUP_PORT="${SUPABASE_PG_PORT:-5432}"
SUP_DB="${SUPABASE_PG_DB:-postgres}"

BACKUP_DIR="/opt/veripaper/backup"
mkdir -p "$BACKUP_DIR"
DUMP="$BACKUP_DIR/supabase-$(date +%Y%m%d-%H%M%S).sql.gz"

echo "== 1) 建 extensions schema 与 vector 扩展（dump 里的列类型是 extensions.vector） =="
docker exec -i vp-postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;
SQL

echo "== 2) 从 Supabase 导出（public schema，PG17 客户端） =="
if [ "${SKIP_DUMP:-0}" = "1" ] && ls "$BACKUP_DIR"/*.sql.gz >/dev/null 2>&1; then
    DUMP=$(ls -t "$BACKUP_DIR"/*.sql.gz | head -n 1)
    echo "   复用已有 dump：$DUMP ($(du -h "$DUMP" | cut -f1))"
else
    docker run --rm \
      -e PGPASSWORD="$SUP_PASS" -e PGSSLMODE=require \
      postgres:17 \
      pg_dump -h "$SUPABASE_PG_HOST" -p "$SUP_PORT" -U "$SUPABASE_PG_USER" -d "$SUP_DB" \
      --no-owner --no-privileges --schema=public \
      | gzip -9 > "$DUMP"
    echo "   dump: $DUMP ($(du -h "$DUMP" | cut -f1))"
fi

echo "== 3) 恢复（先清空 public，保证可重复执行） =="
docker exec -i vp-postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "DROP SCHEMA IF EXISTS public CASCADE;" \
  -c "CREATE SCHEMA public;" \
  -c "ALTER SCHEMA public OWNER TO \"$POSTGRES_USER\";"

# PG17 的 pg_dump 会自带 `CREATE SCHEMA public;` 与 COMMENT ON SCHEMA。
# 若直接灌入会 `already exists` 并因 ON_ERROR_STOP 中断整个恢复（2026-11-10 实测），
# 所以流式过滤掉这两类语句（schema 已在上一步建好）。
gunzip -c "$DUMP" \
  | sed -e '/^CREATE SCHEMA public;$/d' -e '/^COMMENT ON SCHEMA public /d' \
  | docker exec -i vp-postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null
echo "   restore done"

echo "== 4) ANALYZE + 三重校验 =="
docker exec -i vp-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
ANALYZE;
\echo '--- 行数 ---'
SELECT 'user' t, count(*) FROM "user"
UNION ALL SELECT 'task', count(*) FROM task
UNION ALL SELECT 'subtask', count(*) FROM subtask
UNION ALL SELECT 'execution_log', count(*) FROM execution_log
UNION ALL SELECT 'validation_result', count(*) FROM validation_result
UNION ALL SELECT 'knowledge_doc', count(*) FROM knowledge_doc
UNION ALL SELECT 'doc_chunk', count(*) FROM doc_chunk
UNION ALL SELECT 'vector_index', count(*) FROM vector_index
UNION ALL SELECT 'knowledge_vectors', count(*) FROM knowledge_vectors
ORDER BY 1;
\echo '--- alembic ---'
SELECT version_num FROM alembic_version;
\echo '--- 向量 id 双向差（都应为 0） ---'
SELECT (SELECT count(*) FROM (SELECT chunk_id::text AS id FROM vector_index EXCEPT SELECT id FROM knowledge_vectors) a) AS index_not_in_vectors,
       (SELECT count(*) FROM (SELECT id FROM knowledge_vectors EXCEPT SELECT chunk_id::text FROM vector_index) b) AS vectors_not_in_index;
\echo '--- 库大小 ---'
SELECT pg_size_pretty(pg_database_size(current_database()));
\echo '--- 扩展 ---'
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
SQL

echo "== 迁移完成 =="
