# 后端应用（backend）

多智能体协作自动化任务助手 — FastAPI 后端。

完整项目说明见仓库根目录 [`README.md`](../README.md)。

## 运行

```bash
# 开发
pip install -e ".[dev]"
uvicorn app.main:app --reload

# 镜像内（生产）
./entrypoint.sh   # alembic upgrade head && uvicorn ...
```

## 测试

```bash
pytest tests -q --cov=app
black --check app tests
ruff check app tests
```
