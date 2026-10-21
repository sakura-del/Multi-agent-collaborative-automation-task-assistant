# Phase 2 · MCP server + 开放 API + API Key（实现计划）

> 创建：2026-10-21 ｜ 状态：**待执行**
> 上游：[产品路线图](../../product/2026-10-18-demo-to-product-roadmap.md)（L4 协作与交付层 → 开放能力）
> 前置：PRISMA ✅ + 可观测性 ✅ + 团队工作区 ✅

---

## 0. 为什么做这个

**三个真实需求，按强度排序：**

1. **自动化工作流接入**：研究者已有 Zotero / Obsidian / VS Code / Claude Desktop 等工具，
   希望能「在写作工具里直接发起一次综述」而不是切浏览器。MCP 是当前事实标准
   （Claude Desktop、Cursor、Continue 都支持）。
2. **机构集成**：高校图书馆系统、科研管理系统要调我们的能力，需要稳定的 HTTP API + Key。
3. **计费前置**：计费需要「按 Key 计量」。没有 API Key 就没有可归属的调用方，
   计费只能按用户粗算。

**不做会怎样**：产品停留在「网页工具」，无法进入任何自动化管线，机构采购时
「有没有 API」是必问项。

---

## 1. 范围与不做什么

### 做

| # | 内容 |
| --- | --- |
| 1 | `api_key` 表 + 生成/吊销/列表；**只存哈希**，明文只显示一次 |
| 2 | **scope 粒度**：`tasks:read` / `tasks:write` / `artifacts:read` / `annotations:write` |
| 3 | `X-API-Key` 鉴权依赖（与既有 JWT 共存，不改动 Bearer 链） |
| 4 | 开放 API：任务 CRUD + 产出下载 + 批注（**复用既有端点**，加 Key 鉴权通道） |
| 5 | MCP server（stdio + HTTP/SSE 两种传输）暴露 5 个工具 |
| 6 | 前端 `/settings/api-keys` 管理页 |
| 7 | 速率限制按 Key 生效（复用既有中间件） |

### 明确不做（写进计划）

| 不做 | 理由 |
| --- | --- |
| OAuth 2.0 授权服务器 | 机构集成要的是「发个 Key 就能用」。OAuth 需要授权页/同意流/refresh token，成本是 Key 的 5 倍，且当前没有第三方应用市场 |
| 按 Key 计费扣款 | 计费是 Phase 2 第 6 项。本轮只做「Key 可归属调用方 + 用量计数」，扣款单列 |
| Webhook 回调 | 任务完成通知靠客户端轮询 MCP 工具（`get_task`）即可。Webhook 需要重试/签名/幂等，单独设计 |
| MCP 资源订阅（resources/subscribe） | 工具（tools）已覆盖「发起任务 + 查状态 + 取产出」，资源订阅对异步任务无收益 |
| 独立 API 版本（`/api/v2`） | 现有端点已稳定，加鉴权通道不需要新版本。真需要 v2 时再单独立项 |

---

## 2. API Key 设计

### 2.1 数据模型 `api_key`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID PK | |
| `user_id` | UUID FK→user | 归属人（计费与配额按它） |
| `name` | String(64) | 用户可见名称（"我的脚本"） |
| `prefix` | String(16) | 明文前缀（`maa_xxxx`），用于列表里识别是哪个 Key |
| `key_hash` | String(128) | `sha256(明文)`；**绝不存明文** |
| `scopes` | JSON | 字符串数组，如 `["tasks:read","tasks:write"]` |
| `last_used_at` | nullable | 最后使用时间（判断是否还在用） |
| `expires_at` | nullable | 过期时间（默认 NULL = 永久，可选 90/180/365 天） |
| `revoked_at` | nullable | 吊销时间（**软删除**，保留审计） |

**约束**：`UNIQUE(key_hash)`。

### 2.2 为什么只存哈希 + 只显示一次

与密码同一原则：数据库泄漏时明文 Key 直接可用。哈希后攻击者拿到也只是一串摘要。

**代价**：用户忘记 Key 只能重建。这是**正确的取舍**——Key 泄漏的代价
（他人消耗配额、读写用户数据）远高于「重建一次」的不便。

### 2.3 明文格式与识别

```
maa_<32 字节 base62>     例：maa_7Kx9mQ2pLvN4wR8tYzA6bC1dE5fG0hJ3
```

前缀 `maa_` 的作用：**泄漏检测**。GitHub Secret Scanning / gitleaks 能通过固定
前缀识别并告警。这是行业惯例（`sk-`、`ghp_`、`AKIA`）。

### 2.4 Scope 粒度（为什么是 4 个而不是 1 个或 10 个）

| Scope | 允许 |
| --- | --- |
| `tasks:read` | 列任务 / 查快照 / 回放事件 |
| `tasks:write` | 建任务 / 确认规划 / 控制（取消、重试） |
| `artifacts:read` | 下载 Markdown/DOCX/`.bib`/`.ris` |
| `annotations:write` | 写批注（导师脚本场景） |

**为什么不只给「全权」**：只读 Key 是最常见的集成需求（把任务状态同步到自己的
看板），给全权等于过度授权。**为什么不再细**：再细（如 `tasks:cancel` 单独一个）
会让用户在创建 Key 时面对 10 个勾选框，实际收益极低。

**默认**：新建 Key 默认勾 `tasks:read` + `tasks:write`（最常见组合）。

---

## 3. 鉴权通道设计（关键决策）

### 3.1 与既有 JWT 共存，不替换

```
Authorization: Bearer <jwt>        ← 网页前端（不变）
X-API-Key: maa_xxxx                ← 开放 API / MCP
```

**实现方式**：新增 `get_api_key_user` 依赖，它内部：
1. 读 `X-API-Key`；
2. 查 `key_hash` → 校验未吊销/未过期；
3. 校验 scope（按端点要求）；
4. 更新 `last_used_at`（**节流**：同一 Key 60 秒内只写一次，避免每请求一次 UPDATE）；
5. 返回 `User` 对象。

**为什么不用「把 Key 塞进 Bearer」**：会让 `decode_token` 需要区分两种 token 类型，
且既有中间件会尝试解析失败。两条通道物理隔离更清晰，也不影响现有 400+ 测试。

### 3.2 端点如何同时支持两种鉴权

**方案 A（否决）**：复制一套 `/api/v1/open/...` 端点。
**方案 B（采用）**：把既有端点的鉴权依赖改成「或」逻辑：

```python
async def get_current_user_or_api_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """优先 X-API-Key，其次 Bearer。"""
```

**为什么不用方案 A**：会让每个端点写两遍（业务逻辑完全一样），且未来改业务
要改两处 —— 这是「双份实现」的经典陷阱。

**风险与对策**：改的是**共用依赖**，影响面大。对策：
1. 新依赖只在**开放端点集合**上替换，不全局替换；
2. 每个替换的端点必须有「JWT 仍可用」的回归测试；
3. scope 校验做成独立依赖（`require_scope("tasks:write")`），JWT 用户跳过 scope 检查
   （网页用户按角色走既有逻辑）。

### 3.3 配额与限流

- **配额按 `user_id` 扣**（Key 归属人）——与既有口径一致，不改配额模型；
- **限流按 Key**：既有中间件按 `ip` 限流，开放 API 需要按 Key 限流
  （否则同一 IP 上的多个 Key 互相挤占）。实现：中间件在识别到 `X-API-Key` 时
  用 `key:{prefix}` 作为限流维度。

---

## 4. 开放 API 范围

复用既有端点，只加 Key 通道：

| 方法 | 路径 | Scope |
| --- | --- | --- |
| `GET` | `/api/v1/tasks` | `tasks:read` |
| `GET` | `/api/v1/tasks/{id}` | `tasks:read` |
| `POST` | `/api/v1/tasks` | `tasks:write` |
| `POST` | `/api/v1/tasks/{id}/plan/approve` | `tasks:write` |
| `POST` | `/api/v1/tasks/{id}/control` | `tasks:write` |
| `GET` | `/api/v1/tasks/{id}/events` | `tasks:read` |
| `GET` | `/api/v1/tasks/{id}/artifacts/{aid}` | `artifacts:read` |
| `GET` | `/api/v1/tasks/{id}/references.bib` | `artifacts:read` |
| `GET` | `/api/v1/tasks/{id}/references.ris` | `artifacts:read` |
| `POST` | `/api/v1/tasks/{id}/annotations` | `annotations:write` |

**新增管理端点**（JWT only，不接受 Key —— 防止「Key 生成新 Key」的权限提升）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/api-keys` | 生成（明文只返回一次） |
| `GET` | `/api/v1/api-keys` | 列出（只返回前缀，不返回明文） |
| `DELETE` | `/api/v1/api-keys/{id}` | 吊销（软删除） |

---

## 5. MCP server

### 5.1 传输方式

| 传输 | 用途 |
| --- | --- |
| **stdio** | 本地工具（Claude Desktop、Cursor）：`python -m app.mcp.server` |
| **HTTP/SSE** | 远程接入：挂在既有 FastAPI 上（`/mcp`） |

**为什么两种都做**：stdio 是 MCP 的主流用法（本地进程），但机构部署希望
「一个地址所有人用」→ 需要 HTTP。stdio 实现成本低（复用同一套工具定义）。

### 5.2 工具清单（5 个）

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `create_literature_review` | `topic`, `keywords`, `max_documents?`, `citation_style?` | 建任务，返回 `task_id` |
| `get_task_status` | `task_id` | 返回状态 + 规划摘要（不返回全文，避免撑爆上下文） |
| `list_tasks` | `limit?`, `status?` | 列最近任务 |
| `get_task_output` | `task_id` | 返回 Markdown 全文 + 引用数（**产物内容**） |
| `export_task` | `task_id`, `format` | 返回下载 URL 或 base64（`markdown`/`docx`/`bib`/`ris`） |

**为什么只有 5 个**：工具太多会让模型选择困难（MCP 客户端通常把全部工具描述塞进
上下文）。这 5 个覆盖「发起 → 查状态 → 取产出 → 导出」完整闭环。

### 5.3 MCP 鉴权

stdio：从环境变量 `MAA_API_KEY` 读 Key。
HTTP/SSE：从 `X-API-Key` 头读。

**MCP server 不直接连数据库**：它作为**客户端**调用开放 API。理由：
1. 权限与限流自动生效（不用在 MCP 层重写）；
2. MCP server 可以独立部署（甚至放用户机器上）；
3. 避免 MCP 成为绕过配额的后门。

---

## 6. 实现任务分解

### Task 1：`api_key` 模型 + 迁移

- `app/db/models/api_key.py`
- 迁移 `add_api_key`（幂等）
- 测试：模型约束、迁移 upgrade/downgrade/幂等、`key_hash` 唯一

### Task 2：Key 服务（生成/校验/吊销）

- `app/security/api_key_service.py`
  - `create(user_id, name, scopes, expires_in_days?)` → 返回 `(record, plaintext)`
  - `authenticate(plaintext)` → `(record, user)` 或 None
  - `list_for_user(user_id)` / `revoke(user_id, key_id)`
  - scope 校验 `has_scope(record, scope)`
- 测试：哈希正确、明文只返回一次、吊销后失效、过期失效、scope 判定、
  `last_used_at` 节流

### Task 3：鉴权依赖 + 端点接线

- `app/api/deps.py` 加 `get_current_user_or_api_key` + `require_scope`
- 10 个开放端点改依赖
- 限流中间件支持 `key:{prefix}` 维度
- 测试：JWT 仍可用（回归）、Key 可用、scope 不足 403、吊销后 401、
  管理端点拒绝 Key 鉴权

### Task 4：Key 管理端点 + 前端页

- `app/api/v1/endpoints/api_keys.py`（3 个端点，JWT only）
- `frontend/src/views/ApiKeyView.vue`：列表（前缀/scope/最后使用/状态）+
  创建（scope 勾选 + 有效期）+ **创建后一次性展示明文 + 复制**
- 测试：Vitest 组件测试 + 端点测试

### Task 5：MCP server

- `app/mcp/server.py`（stdio）+ `app/mcp/tools.py`（5 个工具，调开放 API）
- `app/mcp/http.py`（HTTP/SSE 挂载）
- 测试：工具定义的 schema 快照、每个工具的参数校验、错误映射
  （**不测真实网络调用**，用 httpx mock）

### Task 6：端到端验收

- 服务器：生成 Key → 用 Key 跑完整流程（建任务 → 确认 → 查状态 → 取产出 → 导出）
- MCP stdio 实测：`MAA_API_KEY=... python -m app.mcp.server` 走一遍 5 个工具
- 清理探针（Key + 任务 + quota key）
- 浏览器走查 Key 管理页

---

## 7. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 改共用鉴权依赖影响 400+ 既有测试 | 新依赖**只用于开放端点**；每个端点加「JWT 仍可用」回归测试；不全局替换 |
| Key 泄漏 | 只存哈希、固定前缀便于扫描、可吊销、可选过期、`last_used_at` 便于发现异常 |
| 限流维度变更引入绕过 | Key 维度与 IP 维度**同时生效**（不是二选一） |
| MCP 工具返回内容过大撑爆上下文 | `get_task_status` 只返摘要；`get_task_output` 显式说明返回全文 |
| Key 生成新 Key（权限提升） | 管理端点强制 JWT only，显式拒绝 `X-API-Key` |

---

## 8. Exit Gate

- [ ] 迁移可 upgrade / downgrade / 重复 upgrade
- [ ] 后端全量门禁绿（覆盖率不下降）
- [ ] **JWT 通道回归测试全绿**（改共用依赖的最大风险点）
- [ ] scope 矩阵全部有测试（含越权 403）
- [ ] MCP 5 个工具全部有测试
- [ ] 服务器端到端：Key 跑完整流程 + MCP stdio 实测
- [ ] 清理回查（Key/任务/quota key + demo 数据未变）
- [ ] CI 6/6 绿
- [ ] 文档：execution-notes + 路线图勾选 + runbook（Key 管理、MCP 接入）
