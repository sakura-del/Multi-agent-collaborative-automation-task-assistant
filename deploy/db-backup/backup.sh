#!/usr/bin/env bash
# 每日把 Supabase（生产库）dump 成 gzip 传到七牛云 Kodo，并按天数轮转。
#
# 设计要点：
# - 连 **session pooler 5432**，不用 6543：transaction pooler 会让 pg_dump 的长事务失败；
# - 不用 `--single-transaction`：跨洋链路会 SSL EOF，逐语句提交更稳（失败重跑即可）；
# - 单个容器内 `while true + sleep` 轮询，不依赖 cron 守护进程；
# - 任何一次失败只记日志、不退出容器（第二天还会再试）。
set -uo pipefail

: "${PGHOST:?PGHOST 未配置}"
: "${PGUSER:?PGUSER 未配置}"
: "${PGPASSWORD:?PGPASSWORD 未配置}"
: "${S3_ENDPOINT:?S3_ENDPOINT 未配置}"
: "${S3_BUCKET:?S3_BUCKET 未配置}"

export PGPASSWORD
export PGSSLMODE="${PGSSLMODE:-require}"

# awscli 只认 AWS_* 前缀；compose 里为了和文档存储共用变量名用的是 S3_*
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-${S3_ACCESS_KEY_ID:-}}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-${S3_SECRET_ACCESS_KEY:-}}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-${S3_REGION:-cn-east-1}}"

if [ -z "${AWS_ACCESS_KEY_ID}" ] || [ -z "${AWS_SECRET_ACCESS_KEY}" ]; then
    echo "[db-backup] 缺少 S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY，退出" >&2
    exit 1
fi

KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"
PREFIX="${S3_PREFIX:-db-backups}"
INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"
REGION="${S3_REGION:-cn-east-1}"

log() { echo "[db-backup] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

# 七牛只接受 virtual-hosted 寻址（bucket.endpoint/key），path 样式会 SignatureDoesNotMatch
aws configure set default.s3.addressing_style virtual >/dev/null
aws configure set default.s3.signature_version s3v4 >/dev/null

s3() {
    aws --endpoint-url "$S3_ENDPOINT" --region "$REGION" s3 "$@" --only-show-errors
}

prune() {
    local cutoff date time size name obj_ts
    cutoff=$(( $(date -u +%s) - KEEP_DAYS * 86400 ))
    s3 ls "s3://${S3_BUCKET}/${PREFIX}/" 2>/dev/null | while read -r date time size name; do
        [ -z "${name:-}" ] && continue
        obj_ts=$(date -u -d "${date}T${time}Z" +%s 2>/dev/null) || continue
        if [ "$obj_ts" -lt "$cutoff" ]; then
            log "prune ${name} (${KEEP_DAYS} 天前)"
            s3 rm "s3://${S3_BUCKET}/${PREFIX}/${name}" || true
        fi
    done
}

run_backup() {
    local ts file
    ts="$(date -u +%Y%m%dT%H%M%SZ)"
    file="/tmp/${ts}.sql.gz"

    log "dump 开始（${PGUSER}@${PGHOST}:${PGPORT:-5432}/${PGDATABASE:-postgres}）"
    if ! pg_dump --no-owner --no-privileges --schema=public "${PGDATABASE:-postgres}" \
        | gzip -9 > "$file"; then
        log "pg_dump 失败"
        rm -f "$file"
        return 1
    fi
    log "dump 完成：$(du -h "$file" | cut -f1)"

    if ! s3 cp "$file" "s3://${S3_BUCKET}/${PREFIX}/${ts}.sql.gz"; then
        log "上传失败：${ts}.sql.gz"
        rm -f "$file"
        return 1
    fi
    rm -f "$file"
    log "已上传 s3://${S3_BUCKET}/${PREFIX}/${ts}.sql.gz"
    prune
    return 0
}

if [ "${RUN_ONCE:-0}" = "1" ]; then
    run_backup
    exit $?
fi

log "启动：每 ${INTERVAL}s 备份一次，保留 ${KEEP_DAYS} 天"
run_backup || log "首次备份失败，${INTERVAL}s 后重试"
while true; do
    sleep "$INTERVAL"
    run_backup || log "本次备份失败，下个周期重试"
done
