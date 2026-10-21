# Phase 2 · 团队工作区 + 席位 + 导师批注（实现计划）

> 创建：2026-10-21 ｜ 状态：**待执行**
> 上游：[产品路线图](../../product/2026-10-18-demo-to-product-roadmap.md)（L4 协作与交付层）
> 前置：PRISMA ✅ + 可观测性 ✅

---

## 0. 为什么做这个（商业理由先于技术）

路线图把「无协作」列为 **🟡 高校采购硬门槛**：

> 现状：仅 owner 隔离，无团队/席位/批注 → 高校采购时无法回答「一个导师带 5 个学生怎么共用」。

竞品对标：Rayyan 机构版按席位收费（$2,500/年量级）。我们要卖机构版，**席位**必须是
一等公民，否则计费无从落地。

**导师批注**是第二个硬需求：高校场景的真实工作流是「学生跑综述 → 导师批注 → 学生改」，
当前只能导出 Word 后在 Word 里批注，割裂且无法回流系统。

---

## 1. 范围与不做什么

### 做

| # | 内容 |
| --- | --- |
| 1 | `workspace`（团队）+ `workspace_member`（成员/席位）+ 角色权限模型 |
| 2 | 任务归属从 `user_id` 扩展到「个人任务 + 工作区任务」双轨 |
| 3 | 邀请流程（邀请码，非邮件——SMTP 未配齐） |
| 4 | 导师批注：`task_annotation`（任务级 / 段落级锚点） |
| 5 | 前端：工作区管理页 + 成员管理 + 任务详情页批注面板 |

### 明确不做（写进计划，避免范围蔓延）

| 不做 | 理由 |
| --- | --- |
| 实时协同编辑（OT/CRDT） | 文献综述是「跑任务 → 看结果 → 批注」的异步流程，不是 Google Docs。实时编辑要引入 WebSocket 协同层 + 冲突解决，成本远超收益 |
| 席位计费扣款 | 计费是 Phase 2 第 6 项独立任务。本轮只做「席位存在且可计数」，不做扣款 |
| 批注导出到 Word 批注 | Word 批注（`w:comment`）需要改 DOCX 渲染器，本轮只做**系统内批注 + Markdown 导出**；Word 批注单列后续 |
| 通知（邮件/站内信） | SMTP 未配齐；批注先靠用户主动查看 |
| 跨工作区资源共享（知识库/模板共享） | 本轮只做**任务**可见性；知识库与模板共享会牵动配额归属，需单独设计 |

---

## 2. 数据模型

### 2.1 `workspace`（团队工作区）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID PK | |
| `name` | String(128) | 团队名 |
| `slug` | String(64) UNIQUE | URL 友好标识（邀请码也用它做前缀） |
| `owner_id` | UUID FK→user | 创建者（**不可移除**，转让需显式操作） |
| `seat_limit` | Integer | 席位数上限（默认 5；机构版按购买数） |
| `created_at` / `updated_at` | | |

**为什么 `seat_limit` 是列而不是配置**：计费按席位，必须能按工作区差异化；
写进全局配置会导致「一个团队买 10 席、另一个买 3 席」无法表达。

### 2.2 `workspace_member`（成员 = 席位）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID PK | |
| `workspace_id` | UUID FK→workspace | |
| `user_id` | UUID FK→user | |
| `role` | String(16) | `owner` / `admin` / `member` / `viewer` |
| `joined_at` | | |
| UNIQUE(`workspace_id`, `user_id`) | | 同一人不能重复入组 |

**席位计数口径**：`COUNT(*) WHERE workspace_id = ?`。
`owner` 也占一个席位（否则「1 人团队」无法表达，且计费会漏算）。

### 2.3 任务归属双轨（关键设计决策）

**方案 A（否决）**：给 `task` 加 `workspace_id` 可空列，`user_id` 保留。
**方案 B（否决）**：新建 `workspace_task` 关联表。
**方案 C（采用）**：`task` 加 `workspace_id`（**可空**），语义如下：

| `workspace_id` | 含义 | 可见范围 |
| --- | --- | --- |
| `NULL` | 个人任务 | 仅 `user_id` 本人 |
| 非空 | 工作区任务 | 工作区内全部成员（按角色） |

**为什么不用方案 B**：任务已经是「归属一个用户」的模型，加关联表会让所有查询变成
`LEFT JOIN` 双路判断，且「个人任务」与「工作区任务」在代码里要写两套分支。
方案 C 下，查询条件是：

```python
# 个人任务：user_id == me AND workspace_id IS NULL
# 工作区任务：workspace_id IN (我的工作区)
# 合并：OR
```

**关键不变量**：`workspace_id` 非空时，`user_id` 仍是**创建者**（保留审计与配额归属）。
配额按 `user_id` 扣（谁发起谁付费），不按工作区——本轮不改配额模型。

### 2.4 `workspace_invite`（邀请码）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID PK | |
| `workspace_id` | UUID FK | |
| `code` | String(32) UNIQUE | 一次性邀请码（高熵随机） |
| `role` | String(16) | 受邀者加入后的角色 |
| `created_by` | UUID FK→user | |
| `expires_at` | | 默认 7 天 |
| `used_at` | nullable | 用过即失效（**一次性**） |
| `used_by` | UUID FK→user nullable | |

**为什么用邀请码而不是邮件邀请**：SMTP 未配齐（P1-C 遗留：未配 SMTP 时注册不阻塞）。
邀请码可复制粘贴，不依赖邮件通道；后续配齐 SMTP 再加邮件通知，邀请码作为兜底保留。

**为什么一次性**：多人共享一个码会导致席位被超发（`seat_limit` 检查与实际加入有竞态）。
一次性码 + 加入时事务内校验席位，可以把风险降到可控。

### 2.5 `task_annotation`（导师批注）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID PK | |
| `task_id` | UUID FK→task | |
| `author_id` | UUID FK→user | 批注人 |
| `anchor_type` | String(16) | `task` / `paragraph` / `citation` |
| `anchor_key` | String(256) nullable | 段落锚点（见下） |
| `content` | Text | 批注正文 |
| `resolved_at` | nullable | 已解决时间（学生处理完可标记） |
| `created_at` / `updated_at` | | |

**锚点设计（为什么不用字符偏移）**：任务产出（Markdown）会因重跑而整体变化，
字符偏移立刻失效。用**段落内容指纹**做锚点：

- `anchor_type="paragraph"` 时，`anchor_key` = 该段落文本的 `sha256[:16]`；
- 渲染时对每个段落算指纹，匹配上的段落高亮并挂批注；
- 段落被改写 → 指纹失配 → 批注显示为「原文已变更」而不是错挂到别处。

`anchor_type="task"` 时 `anchor_key` 为 NULL（任务级批注，不定位到具体段落）。
`anchor_type="citation"` 时 `anchor_key` = 引用条目的 `source_id`（后续实现，本轮预留枚举）。

---

## 3. 权限模型

### 3.1 角色能力表

| 能力 | owner | admin | member | viewer |
| --- | --- | --- | --- | --- |
| 查看工作区任务与产出 | ✅ | ✅ | ✅ | ✅ |
| 创建任务到工作区 | ✅ | ✅ | ✅ | ❌ |
| 写批注 | ✅ | ✅ | ✅ | ✅ |
| 删除**自己的**批注 | ✅ | ✅ | ✅ | ✅ |
| 删除**他人的**批注 | ✅ | ✅ | ❌ | ❌ |
| 邀请成员 | ✅ | ✅ | ❌ | ❌ |
| 移除成员 | ✅ | ✅ | ❌ | ❌ |
| 改成员角色 | ✅ | ✅ | ❌ | ❌ |
| 改工作区名 / 席位上限 | ✅ | ❌ | ❌ | ❌ |
| 解散工作区 | ✅ | ❌ | ❌ | ❌ |

**为什么 viewer 也能写批注**：导师最常见的使用方式是「只看 + 批注」，不给写权限但必须能批注。
这是本产品的核心场景，不能把批注权限绑到「能创建任务」。

### 3.2 越权响应口径

与既有 `TaskRepository` 一致：**跨工作区访问一律返回 404**（"不存在"），
不返回 403 —— 403 会泄漏「那个 id 存在」。

**例外**：在工作区内但角色不足（如 member 想邀请成员）→ **403**。
因为成员已知道工作区存在，隐藏无意义，403 能给出可读的「权限不足」。

---

## 4. 实现任务分解

### Task 1：数据模型 + 迁移

- `backend/app/db/models/workspace.py`：`Workspace` / `WorkspaceMember` / `WorkspaceInvite`
- `backend/app/db/models/annotation.py`：`TaskAnnotation`
- `task` 表加 `workspace_id`（nullable, FK, index）
- 迁移 `add_workspace_and_annotation`：**幂等**（`IF NOT EXISTS`），存量任务 `workspace_id` 保持 NULL
- 测试：模型字段、约束（唯一键）、迁移 upgrade/downgrade/幂等

### Task 2：工作区领域服务

- `backend/app/collaboration/services/workspace_service.py`：
  - `create_workspace(owner, name, seat_limit)` → 自动把 owner 加入为 `owner`
  - `list_workspaces(user_id)` → 我参与的工作区
  - `get_workspace_for_user(user_id, workspace_id)` → 非成员返回 None（404 口径）
  - `invite(...)` / `accept_invite(code)` / `remove_member(...)` / `change_role(...)`
  - **席位校验**：`accept_invite` 事务内 `SELECT ... FOR UPDATE` 工作区行，校验 `COUNT < seat_limit`
- 测试：创建、邀请、接受、席位满拒绝、重复加入、移除 owner 被拒、转让

### Task 3：任务归属双轨改造

- `TaskRepository` 加 `list_visible(user_id, workspace_ids, ...)` 与 `get_visible(...)`
- `tasks.py` 端点改用可见性查询（含 POST 支持 `workspace_id` 参数）
- 配额仍按 `user_id` 扣（不改）
- 测试：个人任务仅本人可见、工作区任务全成员可见、非成员 404、创建时校验成员身份与角色

### Task 4：批注服务 + 端点

- `backend/app/collaboration/services/annotation_service.py`：
  - 创建/列出/更新/删除/标记已解决
  - 段落指纹计算（`sha256(text.strip())[:16]`）
  - 权限：删除他人批注需 admin+
- 端点 `/api/v1/tasks/{task_id}/annotations`（GET/POST）、`/annotations/{id}`（PATCH/DELETE）
- 测试：锚点匹配、权限矩阵、已解决状态、跨工作区 404

### Task 5：前端工作区管理

- `/workspaces` 页：我的工作区列表 + 创建 + 进入
- `/workspaces/:id` 页：成员列表（角色/加入时间/席位用量）+ 邀请码生成/复制 + 移除成员 + 改角色
- 导航加「团队」项
- 测试：Vitest 组件测试 + data-test 定位

### Task 6：前端批注面板

- 任务详情页加批注侧栏：任务级批注列表 + 新建 + 标记已解决
- 段落级批注：在 Markdown 预览的段落旁显示批注气泡（按指纹匹配）
- 测试：Vitest（含「原文已变更」失配态）

### Task 7：端到端验收

- 服务器：两个账号建工作区 → 邀请 → 加入 → 共享任务可见 → 导师批注 → 学生看到 → 标记已解决
- 清理探针数据（含 Redis quota key，见 AGENTS #15）
- 浏览器双视口走查

---

## 5. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 任务可见性改造遗漏某处 → 越权 | `TaskRepository` 的旧方法（`get_for_user` / `list_for_user`）**保留但标 deprecated**，新代码一律走 `get_visible` / `list_visible`；加「越权访问全端点扫描」测试 |
| 席位竞态（并发接受邀请超发） | 事务内 `SELECT ... FOR UPDATE` 锁工作区行 + 唯一键兜底 |
| 段落指纹锚点因重跑失效 | 明确设计为「失配显示原文已变更」，不静默错挂；前端必须有失配态测试 |
| 迁移影响存量数据 | `workspace_id` 可空，存量全 NULL（语义即「个人任务」），无需回填 |
| 配额归属歧义（工作区任务谁付费） | 明确：**按发起人 `user_id` 扣**，不改配额模型；写进 runbook |

---

## 6. Exit Gate

- [ ] 迁移可 upgrade / downgrade / 重复 upgrade
- [ ] 后端全量门禁绿（black/ruff/mypy/pytest，覆盖率不下降）
- [ ] 权限矩阵全部有测试（含越权 404 与角色不足 403）
- [ ] 前端 Vitest 绿 + build 成功
- [ ] 服务器端到端：两账号 + 邀请 + 共享任务 + 双向批注 + 标记已解决
- [ ] 清理回查（探针删除 + demo 数据未变 + quota keys 清零）
- [ ] CI 6/6 绿
- [ ] 文档：execution-notes + 路线图勾选 + runbook（工作区与席位口径）
