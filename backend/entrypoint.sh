#!/bin/sh
set -eu

# 启动前执行数据库迁移（自包含：compose up 即完成 schema 就绪，无需人工步骤）。
# 迁移失败则不启动应用，避免在错误 schema 上服务。
alembic upgrade head

# 以非 root 用户运行；uploads/artifacts 目录在镜像内已创建并授权
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
