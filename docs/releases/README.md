# 版本发布记录（Issue #25 沉淀项 4）

## 规则

- 版本号遵循 **SemVer**（`MAJOR.MINOR.PATCH`）。当前处于 `0.x`：功能迭代加 MINOR，
  修复加 PATCH，不兼容变更才升 MAJOR。
- **每次生产部署必须写一条 release 记录**（本目录 `YYYY-MM-DD-vX.Y.Z.md`），包含：
  版本、部署提交、变更摘要、回滚点、验证证据。
- 版本号三处必须一致：`backend/pyproject.toml`、`frontend/package.json`、
  `backend/app/__init__.py` 的 `__version__`（`/health` 回显的就是它）。
- 回滚点写**上一个稳定版本的提交哈希**；回滚操作见 `docs/runbooks/ecs-deployment.md`。

## 记录列表

| 版本 | 日期 | 摘要 |
| --- | --- | --- |
| v0.2.0 | 2026-11-20 | 后台管理控制台、可等待工具、BYOK 收口、引用一致性闸门、审计日志 |
