# 项目长期约定

## 测试在哪跑（2026-11-16 用户明确要求）

- **所有后端测试只在 `10.0.10.2`（`ssh ylin-dev`）跑**。本机 Windows 内存不足；
  美东生产机 `47.253.81.6` 内存也不足。**不要**在两者上跑 pytest。
- 测试环境：`~/vp-test`（独立检出，`git fetch origin main && git reset --hard origin/main`），
  入口 `~/vp-test/run-tests.sh <pytest args>`；venv 为 Python 3.12。
- 本机只做 `black` / `ruff` / `mypy` 与前端 `npm run build`。
- 浏览器验收由 GitHub CI 的 `e2e` 作业负责（隔离 Compose 栈，端口 48082）。

## BYOK（Issue #15）关键决策

- 用户凭据只进不出：响应永不返回明文；任务快照保留 model/provider/base_url。
- BYOK 失败**不静默回退**平台模型；401/403 才计数，连续失败达阈值自动 invalid。
- token 日配额对 BYOK 全程不参与；篇数由 `BILLING__BYOK_CONSUMES_REVIEW_QUOTA` 控制（默认 false）。
- 自定义端点：创建期拦 IP 字面量，运行时 `PinnedDNSAsyncHTTPTransport` 解析后只连已校验 IP。
- 探活打 `/models`（不烧 token），未保存配置不落库。

## CI 结构

- push：选片测试（`mode=full` 触发全量）+ lint + migration + smoke + **e2e**（Playwright，仅 model-settings）。
- nightly：全量 + 覆盖率 ≥80%。
- E2E 用一次性注册账号；`SECRETS__ENCRYPTION_KEY` 是测试专用固定值。
