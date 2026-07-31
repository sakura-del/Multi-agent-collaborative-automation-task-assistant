# 多智能体协作自动化任务助手 - 后端镜像
# 阶段0：基础 Python 运行环境 + 依赖安装

FROM python:3.11-slim AS base

# 设置 Python 运行时环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 系统依赖：build-essential 用于编译 psycopg / cryptography 等
# libpq-dev 给 asyncpg；curl 用于健康探针
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先单独复制依赖描述文件，利用 Docker 层缓存
COPY pyproject.toml ./

# 安装项目依赖（不安装 dev 依赖以减小镜像体积）
# 注：阶段0仅安装基础运行依赖，RAG/LLM 重依赖在对应阶段按需添加
RUN pip install --upgrade pip && \
    pip install \
    "fastapi>=0.110.0" \
    "uvicorn[standard]>=0.27.0" \
    "pydantic>=2.6.0" \
    "pydantic-settings>=2.1.0" \
    "email-validator>=2.0.0" \
    "PyYAML>=6.0.1" \
    "sqlalchemy[asyncio]>=2.0.25" \
    "asyncpg>=0.29.0" \
    "alembic>=1.13.0" \
    "redis>=5.0.0" \
    "structlog>=24.1.0" \
    "python-jose[cryptography]>=3.3.0" \
    "bcrypt>=4.0.0" \
    "python-multipart>=0.0.9" \
    "websockets>=12.0" \
    "httpx>=0.26.0" \
    "tenacity>=8.2.3" \
    "networkx>=3.2.1"

# 复制源码
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# 创建非 root 用户运行应用
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 健康检查：调用 /health 端点
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

# 开发模式：热重载；生产环境应通过 docker-compose 覆盖为 gunicorn+uvicorn workers
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
