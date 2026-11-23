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
| v0.3.2 | 2026-11-23 | Issue #29：`/api/*` 改由 zone Worker 反代，`/api/health` 不再被 SPA 吞成 200 HTML |
| v0.3.1 | 2026-11-20 | BYOK 收尾（Issue #28）：凭据级回退开关 + 生成能力探测 + planner 门禁 |
| v0.3.0 | 2026-11-20 | 定时重跑（Issue #27）：增量检索 + 增量摘要 + 周/月排期 + 邮件汇报 |
| v0.2.1 | 2026-11-20 | 公开页 UI 打磨（Issue #26）：注册页文案/路由、重置页返回入口、假链接与文案空格 |
| v0.2.0 | 2026-11-20 | 后台管理控制台、可等待工具、BYOK 收口、引用一致性闸门、审计日志 |
