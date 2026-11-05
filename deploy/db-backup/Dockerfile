# Supabase 每日备份镜像：pg_dump 17 + awscli
#
# 为什么不用社区的 postgres-backup-s3 镜像：
# 它在 Docker Hub 上只有 ≤16 的 tag（实测 16/15/14/.../9），而 Supabase 是
# PostgreSQL 17.11 —— pg_dump 16 连 17 服务端会直接报 "server version mismatch"。
# 官方 postgres:17 自带 pg_dump 17，版本天然匹配。
FROM postgres:17

RUN apt-get update \
    && apt-get install -y --no-install-recommends awscli ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY deploy/db-backup/backup.sh /usr/local/bin/backup.sh
RUN chmod +x /usr/local/bin/backup.sh

# 覆盖 postgres 镜像的 docker-entrypoint（我们不需要 initdb）
ENTRYPOINT ["/usr/local/bin/backup.sh"]
