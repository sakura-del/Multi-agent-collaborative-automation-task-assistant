# 开放 API 与 MCP

> Phase 2 第 5 项交付。适用对象：集成方（用 Key 调 API / 接 MCP）与运维（Key 治理）。
> 计划：[2026-10-21-03-open-api-mcp-plan.md](../superpowers/plans/2026-10-21-03-open-api-mcp-plan.md)

---

## 1. 两条鉴权通道（物理隔离）

| 通道 | 请求头 | 用途 |
| --- | --- | --- |
| **JWT** | `Authorization: Bearer <token>` | 网页前端 |
| **API Key** | `X-API-Key: maa_xxxx` | 脚本 / MCP / 第三方系统 |

**两条通道互不通用**：

- Key 塞进 `Bearer` → 401（`decode_token` 不认）；
- JWT 塞进 `X-API-Key` → 401（哈希查不到）。

**Key 优先**：同时给两套凭证时，以 Key 的身份与 scope 为准（显式通道优先于隐式）。

---

## 2. 生成与管理 Key

网页：左侧导航「API Key」（`/settings/api-keys`）。

```bash
# 生成（需 JWT）
curl -s -X POST http://10.0.10.2:48080/api/v1/api-keys \
  -H "Authorization: Bearer $JWT" -H 'Content-Type: application/json' \
  -d '{"name":"我的同步脚本","scopes":["tasks:read","tasks:write"],"expires_in_days":90}'
```

响应里的 `key` 字段**只出现这一次** —— 请立即保存。

```bash
# 列出（只返回前缀，不含明文）
curl -s http://10.0.10.2:48080/api/v1/api-keys -H "Authorization: Bearer $JWT"

# 吊销（软删除，重复吊销幂等）
curl -s -X DELETE http://10.0.10.2:48080/api/v1/api-keys/<key_id> \
  -H "Authorization: Bearer $JWT"
```

### ⚠️ 三条安全约束

1. **明文只显示一次**：数据库只存 `sha256`，忘记只能重建。
2. **Key 不能生成新 Key**：管理端点**强制 JWT only**。否则拿到只读 Key 的人
   可以自建全权 Key —— scope 形同虚设。
3. **吊销/过期/不存在返回同一个 401**：不解释原因，避免泄漏「这个 Key 曾经有效」。

---

## 3. Scope 矩阵

| Scope | 允许的操作 |
| --- | --- |
| `tasks:read` | `GET /tasks`、`GET /tasks/{id}`、`GET /tasks/{id}/events` |
| `tasks:write` | `POST /tasks`、`POST /tasks/{id}/plan/approve`、`POST /tasks/{id}/control` |
| `artifacts:read` | `GET /tasks/{id}/artifacts/{aid}`、`.../references.bib`、`.../references.ris` |
| `annotations:write` | `POST /tasks/{id}/annotations` |

**默认**：新建 Key 勾选 `tasks:read` + `tasks:write`。

**最小权限建议**：
- 只把任务状态同步到看板 → 只给 `tasks:read`；
- 导师脚本只写批注 → `tasks:read` + `annotations:write`；
- 完整自动化 → 四个全给。

**JWT 用户不受 scope 限制**（走既有角色逻辑）—— 否则网页操作会被 scope 卡死。

---

## 4. 开放 API 示例

```bash
export KEY=maa_xxxx
export BASE=http://10.0.10.2:48080/api/v1

# 建任务
curl -s -X POST $BASE/tasks \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"topic":"检索增强生成综述","keywords":["RAG","LLM"],"max_documents":20}'

# 查状态
curl -s $BASE/tasks/<task_id> -H "X-API-Key: $KEY"

# 确认规划（revision 必须等于当前 plan_version）
curl -s -X POST $BASE/tasks/<task_id>/plan/approve \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"revision":1}'

# 下载产出
curl -s -OJ $BASE/tasks/<task_id>/artifacts/<artifact_id> -H "X-API-Key: $KEY"
```

**配额仍按 Key 的归属人扣**（与网页操作同口径，不改配额模型）。

---

## 5. MCP 接入

### 5.1 stdio（本地工具，推荐）

```bash
export MAA_API_KEY=maa_xxxx
export MAA_API_BASE=http://10.0.10.2:48080/api/v1
cd backend && python -m app.mcp.server
```

Claude Desktop 配置（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "maa": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "env": {
        "MAA_API_KEY": "maa_xxxx",
        "MAA_API_BASE": "http://10.0.10.2:48080/api/v1"
      }
    }
  }
}
```

### 5.2 HTTP/SSE（机构部署）

`app.mcp.server.streamable_http_app(key)` 返回一个 ASGI app，可挂在既有
FastAPI 的 `/mcp` 路径上。

> ⚠️ **当前是单租户**：用服务启动时的 Key。多租户（按请求解析 `X-API-Key`）
> 需要 SDK 支持请求级上下文注入，属后续迭代。

### 5.3 五个工具

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `create_literature_review` | `topic`、`keywords`、`max_documents?`、`citation_style?` | 发起任务（异步） |
| `get_task_status` | `task_id` | 状态 + 摘要（**不含正文**） |
| `list_tasks` | `limit?` | 最近任务 |
| `get_task_output` | `task_id` | Markdown 全文 + 引用数 |
| `export_task` | `task_id`、`format` | `markdown`/`docx`/`bib`/`ris` |

**为什么只有 5 个**：MCP 客户端把全部工具描述塞进模型上下文，工具太多会让模型
选择困难。这 5 个覆盖「发起 → 查 → 取 → 导出」。

**`get_task_status` 为什么不返正文**：一次「查状态」吃掉大半个上下文窗口后，
后续对话全被挤掉。要正文请显式用 `get_task_output`。

### 5.4 为什么 MCP 不直连数据库

它是**客户端**，调开放 API：

1. 权限与限流自动生效（不用在 MCP 层重写）；
2. 可独立部署（甚至跑在用户机器上）；
3. 不会成为绕过配额的后门。

---

## 6. 运维：Key 治理

### 6.1 排查「某个 Key 还在用吗」

```sql
SELECT name, prefix, last_used_at, expires_at, revoked_at
FROM api_key
WHERE user_id = '<user_id>'
ORDER BY created_at DESC;
```

`last_used_at` 有 60 秒节流（每个 API 请求都写库是真实写放大），
所以它是「分钟级」精度 —— 判断「还在用」足够。

### 6.2 泄漏处置

1. 立刻吊销（`DELETE /api/v1/api-keys/{id}`）；
2. 查 `last_used_at` 与任务创建时间，评估影响面；
3. 必要时用 `user.token_invalid_before` 强制该用户重新登录
   （Key 不受影响，需单独吊销）。

**固定前缀 `maa_` 的价值**：GitHub Secret Scanning / gitleaks 能识别并告警。
建议在仓库加 gitleaks 规则：`maa_[A-Za-z0-9_-]{20,}`。

### 6.3 常见 401/403 原因

| 现象 | 原因 |
| --- | --- |
| 401 | Key 不存在 / 已吊销 / 已过期 / 用户被禁用 |
| 403 | Key 缺所需 scope（错误消息会列出当前 scope） |
| 401（管理端点） | 用 Key 调 `/api-keys` —— 必须用 JWT |

---

## 7. 已知局限

| 局限 | 说明 |
| --- | --- |
| 无 OAuth | 机构集成「发个 Key 就能用」，OAuth 成本是 Key 的 5 倍且当前无应用市场 |
| 无按 Key 计费 | 计费是 Phase 2 第 6 项；当前 Key 只做「可归属调用方 + 用量计数」 |
| 无 Webhook | 客户端轮询 `get_task_status` 即可 |
| MCP HTTP 单租户 | 见 §5.2 |
| 限流仍按 IP | Key 维度限流未实现（见计划 §3.3）；当前靠 IP 维度，同 IP 多 Key 会互相挤占 |
