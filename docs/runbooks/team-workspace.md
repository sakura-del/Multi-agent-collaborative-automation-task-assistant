# 团队工作区与席位

> Phase 2 第 4 项交付。适用对象：运维（席位口径、计费对接）与产品（权限语义）。
> 计划：[2026-10-21-02-team-workspace-plan.md](../superpowers/plans/2026-10-21-02-team-workspace-plan.md)

---

## 1. 为什么有这个功能

路线图把「无协作」列为 **高校采购硬门槛**：

> 当前只有 owner 隔离，无法回答「一个导师带 5 个学生怎么共用」。

机构版按席位收费（对标 Rayyan 机构版 $2,500/年量级），**席位必须是计费单位**，
否则计费无从落地。

---

## 2. 数据模型

| 表 | 说明 |
| --- | --- |
| `workspace` | 团队容器，含 `seat_limit`（按工作区差异化） |
| `workspace_member` | 成员 = **一个席位** |
| `workspace_invite` | 一次性邀请码 |
| `task_annotation` | 导师批注（段落指纹锚点） |

### 席位口径（**计费相关，改动需评审**）

- 席位 = `workspace_member` 的一行；
- **owner 也占一个席位** —— 否则「1 人团队」无法表达，且计费会漏算；
- 上限在 `workspace.seat_limit`（**不写全局配置**：一个团队买 10 席、另一个买 3 席
  必须能表达）；
- 当前**不做扣款**，只做「存在 + 计数 + 上限校验」；计费是 Phase 2 第 6 项。

查询已用/上限：

```sql
SELECT w.id, w.name, w.seat_limit, count(m.id) AS seat_used
FROM workspace w
LEFT JOIN workspace_member m ON m.workspace_id = w.id
GROUP BY w.id, w.name, w.seat_limit;
```

---

## 3. 任务归属双轨

| `task.workspace_id` | 含义 | 可见范围 |
| --- | --- | --- |
| `NULL` | 个人任务（**存量任务全部是这一类**） | 仅 `user_id` 本人 |
| 非空 | 工作区任务 | 工作区全部成员（按角色） |

**关键不变量**：`workspace_id` 非空时 `user_id` 仍是**创建者**，
**配额按发起人扣**（谁发起谁付费），不按工作区。本轮不改配额模型。

代码侧：

- 旧方法 `get_for_user` / `list_for_user` **语义不变**（仅 owner）——
  几十处存量调用点（含编排层后台任务）依赖这个语义；
- 新代码走 `get_visible` / `list_visible`（双轨）。

---

## 4. 权限矩阵

| 能力 | owner | admin | member | viewer |
| --- | --- | --- | --- | --- |
| 查看工作区任务与产出 | ✅ | ✅ | ✅ | ✅ |
| 创建任务到工作区 | ✅ | ✅ | ✅ | ❌ |
| **写批注** | ✅ | ✅ | ✅ | **✅** |
| 删除自己的批注 | ✅ | ✅ | ✅ | ✅ |
| 删除他人的批注 | ✅ | ✅ | ❌ | ❌ |
| 邀请 / 移除成员 | ✅ | ✅ | ❌ | ❌ |
| 改成员角色 / 改工作区名 | ✅ | ❌ | ❌ | ❌ |
| 退出工作区 | ❌（owner 不可退） | ✅ | ✅ | ✅ |

### 为什么 viewer 能写批注

导师最常见的使用方式是「只看 + 批注」，不给写权限但**必须能批注**。
把批注权限绑到「能创建任务」会让付费方无法工作。这是产品决策，不是实现细节。

### 越权响应口径

| 场景 | HTTP | 理由 |
| --- | --- | --- |
| 非成员访问工作区 / 任务 | **404** | 不泄漏「那个 id 存在」 |
| 成员但角色不足 | **403** | 成员已知工作区存在，隐藏无意义 |
| 席位已满 | **409** | 需要扩容，不是改参数能解决 |
| 邀请码无效/过期/已用 | **400** | 请求内容问题 |

---

## 5. 邀请流程（为什么用邀请码）

SMTP 未配齐（P1-C 遗留：未配 SMTP 时注册不阻塞）。邀请码可复制粘贴，
不依赖邮件通道；后续配齐 SMTP 再加邮件通知，邀请码作为兜底保留。

- 一次性（`used_at` 非空即失效）；
- 默认 7 天过期；
- 高熵随机（约 160 bit）；
- **只在创建时返回一次**（列表接口不返回）—— 等同临时凭证，
  能重复读取会让「一次性」形同虚设。

---

## 6. 批注锚点：段落指纹

任务产出会因**重跑**整体变化。字符偏移在重跑后立刻失效，更糟的是会
**静默错挂**（导师的「这段缺页码」挂到无关文字上）。

所以用 `sha256(text.strip())[:16]`：

| 情况 | 行为 |
| --- | --- |
| 段落未变 | 指纹一致 → 批注正常挂载 |
| 段落被改写 | 指纹失配 → `matched=false`，前端显示「原文已变更」 |

**这是有意的取舍**：宁可让导师知道「这条批注的原文已改」，也不要让批注出现在
错误的位置上。

已解决只标记 `resolved_at`、**不删行**：导师要能看到历史意见。

---

## 7. 运维注意

### 清理探针数据（AGENTS #11/#12/#15）

**顺序**（外键依赖）：

```
task_annotation → screening_decision → screening_record → search_log
→ task → workspace_member → workspace_invite → workspace → email_token → user
```

**必须回查**，并给出对照数字（demo 27 任务 / 104 文档）。

### ⚠️ 踩过的坑：`psql -c` 的引号与 `LIKE`

用 `psql -c "DELETE ... WHERE username LIKE 'team%'"` 从 PowerShell/ssh 传参时，
引号可能被层层处理导致 `LIKE` 匹配 0 行而**静默 `DELETE 0`**。
**一律用 `.sql` 文件 + `psql < file`**（AGENTS #1 的延伸）。

### 孤儿数据检查

```sql
-- 批注指向不存在的任务
SELECT count(1) FROM task_annotation a
LEFT JOIN task t ON t.id = a.task_id WHERE t.id IS NULL;

-- 任务指向不存在的用户
SELECT count(1) FROM task t
LEFT JOIN "user" u ON u.id = t.user_id WHERE u.id IS NULL;
```

外键都是 `ON DELETE CASCADE`，正常不会有孤儿；出现即说明有人绕过外键删数据。

---

## 8. 手工验证

```bash
# 建工作区（需 token）
curl -s -X POST http://10.0.10.2:48080/api/v1/workspaces \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"测试组","slug":"test-team","seat_limit":3}'

# 生成邀请码
curl -s -X POST http://10.0.10.2:48080/api/v1/workspaces/$WS_ID/invites \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"role":"viewer"}'

# 另一账号接受
curl -s -X POST http://10.0.10.2:48080/api/v1/workspaces/accept \
  -H "Authorization: Bearer $TOKEN2" -H 'Content-Type: application/json' \
  -d '{"code":"<邀请码>"}'
```

前端：左侧导航「团队」（`/workspaces`）。
