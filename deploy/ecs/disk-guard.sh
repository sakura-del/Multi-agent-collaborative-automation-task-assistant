#!/usr/bin/env bash
# VeriPaper 磁盘护栏（2026-11-16）
#
# 背景：云盘 40G，一次部署会产生约 1.8GB 镜像 + 构建缓存；长期只涨不清，
# 2026-11-16 实测 80%（30G/40G），其中构建缓存 8.5G、悬空镜像 10G。
#
# 原则：**只删可再生成的东西**。
#   - 不删数据卷（pgdata/redisdata/uploads/artifacts/model_cache）
#   - 不删 /opt/veripaper/backup（数据库备份要人工确认）
#   - 不删 .env、证书、仓库文件
#   - 运行中容器使用的镜像受 Docker 保护，不会被 prune 掉
#
# 由 veripaper-disk-guard.timer 每天 04:40（服务器本地时区）触发。

set -uo pipefail

LOG_FILE=/var/log/veripaper-disk-guard.log
exec >>"${LOG_FILE}" 2>&1

before=$(df --output=pcent / | tail -1 | tr -dc '0-9')
echo "=== $(date -Is) start (disk ${before}%) ==="

# ① 构建缓存：保留 24 小时。一天内通常有多次部署，缓存够用；
#    今天实测 4 次部署就堆到 7.4G，72 小时会直接顶到告警线。
docker builder prune -af --filter until=24h || true

# ② 悬空镜像（无 tag 的中间层）：没有任何回滚价值。
docker image prune -f || true

# ③ 72 小时未被任何容器使用的镜像：旧版 backend / 旧 rollback。
#    运行中镜像受保护；保留最近 3 天，足够回滚，也不会让每天多次部署
#    的镜像把 40G 盘堆满（2026-11-16 实测：14 天策略下一天涨到 64%）。
docker image prune -a -f --filter until=72h || true

# ④ 系统日志与 apt 缓存
journalctl --vacuum-size=200M || true
apt-get clean || true

# ⑤ 容器日志兜底：若 daemon 未配 log rotation，单文件超过 200MB 就截断。
#    Docker 持有文件句柄，truncate 后容器继续写，不会中断日志采集。
for file in /var/lib/docker/containers/*/*-json.log; do
    [ -f "${file}" ] || continue
    size=$(stat -c %s "${file}")
    if [ "${size}" -gt 209715200 ]; then
        echo "truncate ${file} (${size} bytes)"
        truncate -s 0 "${file}"
    fi
done

after=$(df --output=pcent / | tail -1 | tr -dc '0-9')
echo "=== $(date -Is) done (disk ${after}%) ==="

# 护栏自己的日志也不能无限涨：超过 50MB 直接清空（历史价值低）
find "$(dirname "${LOG_FILE}")" -name "$(basename "${LOG_FILE}")" -size +50M -exec truncate -s 0 {} \; 2>/dev/null || true
