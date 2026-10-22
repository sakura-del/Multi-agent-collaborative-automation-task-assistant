# 多智能体协作自动化任务助手 - 后端镜像
# Plan 1 Task 6：依赖从 pyproject 派生（含 rag-models），迁移经 entrypoint 自包含

FROM python:3.11-slim AS base

# 设置 Python 运行时环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 系统依赖：build-essential 用于编译；libpq-dev 给 asyncpg；curl 用于健康探针
# libgomp1：sentence-transformers(torch) 运行依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先单独复制构建元数据与 app 包，供 pip 读取 pyproject 并 find 到 app 包
# 注意：setuptools 要求 readme 指向包根内部文件，故 backend/README.md 需拷入 /app
# build context 必须是仓库根（见 compose）
COPY backend/pyproject.toml ./
COPY backend/README.md ./
COPY backend/app ./app
# 仓库级配置基线：settings 从 PROJECT_ROOT/config/default.yaml 读取模型等默认值
COPY config ./config
# 冻结离线演示语料清单（PDF 不入镜像，由用户在界面/CLI 上传入库）
COPY backend/demo_data ./demo_data

# 依赖从 pyproject 派生：.[rag-models] 安装运行时 + RAG 真实模型依赖
# torch 先装 CPU 版（--index-url whl/cpu），避免 sentence-transformers 连带拉取
# CUDA wheel（cudnn/nccl/triton 等 1GB+），容器内仅 CPU 推理
# CI 安装 ./backend[dev] 并用 Mock 嵌入/重排，不依赖本镜像
RUN pip install --upgrade pip && \
    pip install torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install ".[rag-models]"

# 复制其余运行时文件
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY backend/entrypoint.sh ./
# 实验框架与运维脚本（2026-10-22）：120-run 实验矩阵必须在容器内跑
# （本机连不上模型端点；且需要与生产同版本的依赖）。
# `evaluation/` 含 15 主题 + 3 领域语料清单（约 200KB），`scripts/` 是运行入口。
COPY backend/evaluation ./evaluation
COPY backend/scripts ./scripts
COPY config ./config
RUN chmod +x ./entrypoint.sh

# 创建非 root 用户运行应用；uploads/artifacts/.cache 持久化目录在容器内预建
# （/app/.cache 为 HF_HOME，真实模型权重缓存于此；预建保证命名卷首次挂载继承 appuser 属主）
RUN useradd -m -u 1000 appuser && \
    mkdir -p /app/data/uploads /app/data/artifacts /app/.cache && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 存活探针走 /health；就绪判定由 Compose healthcheck 走 /health/ready
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

# 生产模式：entrypoint 先迁移再启动，不加 --reload
ENTRYPOINT ["/app/entrypoint.sh"]
