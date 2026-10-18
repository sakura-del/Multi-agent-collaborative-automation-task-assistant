# 冒烟客户端镜像（Plan 5 Task 3）：只装 httpx，不挂载源码、不注入真实模型密钥
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /smoke

RUN pip install --no-cache-dir "httpx>=0.27,<1"

COPY docker/smoke/smoke.py ./smoke.py

# 非 root 运行，冒烟只需要网络访问
RUN useradd --create-home --shell /usr/sbin/nologin smoke
USER smoke

ENTRYPOINT ["python", "-m", "smoke"]
