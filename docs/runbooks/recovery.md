# 故障恢复手册（Plan 5 Task 8）

> 面向：答辩现场/演示中出问题时的处置、交付后运维。
> 核心原则：**先保住数据卷**，再恢复服务；能降级就降级，不要现场排查根因。
> 所有命令在服务器 Compose 部署上验证过。

## 0. 快速判断表

| 症状 | 最可能原因 | 跳转 |
| --- | --- | --- |
| `/health/ready` 返回 503 且 `database: error` | PostgreSQL 不可用 | §1 |
| `/health/ready` 返回 503 且 `redis: error` | Redis 不可用 | §2 |
| `/health/ready` 返回 503 且 `chroma: error` | Chroma 不可用 | §3 |
| 容器反复重启、日志有迁移报错 | 迁移失败 | §4 |
| 任务长期停在 `running`/`validating`，事件不再增长 | 任务卡住 | §5 |
| 上传成功但解析/检索报错 | 上传文件损坏 | §6 |
| 新版本有问题，需回退 | 版本回滚 | §7 |
| 演示前想要干净环境 | 重置（保留数据） | §8 |

统一入口：

```bash
cd ~/projects/multi-agent-assistant
curl -s http://127.0.0.1/api/v1/health/ready        # 或 :48080
docker compose ps                                   # 五容器状态
docker compose logs --tail 120 backend              # 后端最近日志
```

---

## 1. PostgreSQL 不可用

**症状**：`ready` 里 `database: error`；后端日志有 `asyncpg` 连接错误；任务创建返回 5xx。

```bash
# 1) 看容器状态与日志
docker compose ps postgres
docker compose logs --tail 80 postgres

# 2) 常见：容器被 OOM 或异常退出 → 重启（数据在命名卷，安全）
docker compose restart postgres

# 3) 等待健康后确认后端恢复
until docker compose exec -T postgres pg_isready -U maa -d multi_agent_assistant; do sleep 2; done
curl -s http://127.0.0.1/api/v1/health/ready
```

> ⚠️ **不要**执行 `docker compose down --volumes`。数据在 `maa-postgres-data` 卷里。

若卷损坏（罕见）：

```bash
# 先备份坏卷内容（尽力而为）
docker run --rm -v maa-postgres-data:/data -v $PWD:/backup alpine \
  tar czf /backup/postgres_broken_$(date +%F_%H%M).tgz -C /data . || true
# 再从最近备份恢复（见 deployment.md §6）
```

---

## 2. Redis 不可用

**症状**：`ready` 里 `redis: error`；实时事件推送失效、票据服务降级。

**影响面小**：应用会在 Redis 不可用时回退到**内存事件发布器与内存票据服务**，任务执行与 HTTP 事件补拉仍然可用（Plan 5 Task 2 有专门测试覆盖）。

```bash
docker compose restart redis
until docker compose exec -T redis redis-cli ping | grep -q PONG; do sleep 2; done
curl -s http://127.0.0.1/api/v1/health/ready
```

演示中若 Redis 一直起不来：**继续演示**——事件会通过前端轮询补拉（1/3/9s 退避后降级为 3s 轮询），刷新不丢状态。

---

## 3. Chroma（向量库）不可用

**症状**：`ready` 里 `chroma: error`；检索报错；文献任务节点转 `waiting_human` 且 error 提到检索失败。

```bash
docker compose ps chroma
docker compose logs --tail 80 chroma
docker compose restart chroma
until curl -s http://127.0.0.1:38002/api/v1/heartbeat >/dev/null 2>&1; do sleep 2; done
curl -s http://127.0.0.1/api/v1/health/ready
```

**演示降级方案**：Chroma 挂掉时文献任务无法检索证据。
- 优先：重启 Chroma（通常 10–20 秒恢复）。
- 兜底：改用**已完成的历史任务**演示结果与下载（见 `defense-demo.md` 的离线兜底），并说明"检索依赖向量库，当前降级"。

> 注意：容器重启后 BM25 内存索引会从数据库中的 ready 分块自动重建（`main.lifespan`），向量索引在 Chroma 卷中持久化，不会丢。

---

## 4. 迁移失败

**症状**：backend 反复重启；日志有 `alembic` 异常；`ready` 一直 502。

```bash
# 1) 看当前迁移状态与错误
docker compose exec backend alembic current
docker compose logs --tail 120 backend | grep -A20 -i alembic

# 2) 版本不一致（DB 领先/落后于代码）
docker compose exec backend alembic heads
docker compose exec backend alembic history | head -20

# 3) 落后于代码 → 前进
docker compose exec backend alembic upgrade head

# 4) DB 领先于代码（回滚代码后常见）→ 退到代码对应的版本
docker compose exec backend alembic downgrade -1
docker compose exec backend alembic current
```

**回滚到上一版镜像**（迁移不可逆时的安全做法）：

```bash
docker image ls | grep multi-agent-assistant-backend   # 找到上一版 image id
docker compose down                                    # 只删容器，不删卷
docker tag <上一版 image id> multi-agent-assistant-backend:rollback
# 临时用回滚镜像启动（不改 compose 文件）
docker run -d --name maa-backend-rollback --network multi-agent-assistant_maa-network \
  --env-file .env ... <上一版 image id>
```

> 更简单的做法：`git checkout <上一个 tag>` → `docker compose build backend && docker compose up -d`。
> 迁移只需 `alembic downgrade` 一步即可回到旧 schema（Plan 2 Task 1 已验证 `upgrade → downgrade -1 → upgrade` 往返）。

---

## 5. 任务卡住

**症状**：任务状态长期 `running`/`reviewing`/`validating`，`events` 不再增长；或进程重启后任务没继续。

```bash
# 1) 看任务快照（需要 token；也可用前端详情页）
#    关注每个 subtask 的 status / retry_count / error

# 2) 触发运行时恢复：重启 backend 会执行 TaskExecution.recover()
docker compose restart backend
sleep 15
curl -s http://127.0.0.1/api/v1/health/ready
```

`recover()` 的语义（Plan 5 Task 2 已验证）：
- 遗留 `running/validating/retrying` 节点被重置为 `ready` 并继续执行；
- **已提交完成**的节点不会重跑，`retry_count` 与事件数保持不变；
- 追加 `task_recovery` 事件，便于审计。

**若仍卡住**：
- 前端点「取消」→ 任务转 `cancelled`，再新建任务；
- 或对具体节点用「重试节点」（需要该节点处于 failed/waiting_human）。

---

## 6. 上传文件损坏 / 解析失败

**症状**：文档 `index_status=failed`，`failure_reason` 给出简明原因（不含栈）。

```bash
# 前端知识库页会显示"索引失败：<原因>"
# 处理：删除该文档后重新上传
#   - 界面上点垃圾桶 + 二次确认
#   - 或 API：DELETE /api/v1/knowledge/documents/{doc_id}
```

常见原因：
| 原因 | 处理 |
| --- | --- |
| 文件签名与扩展名不符（伪装 PDF） | 用真实文件重新上传 |
| 超过 30 MB | 压缩或拆分 |
| 扫描版 PDF 无可提取文本 | 换可复制文本的 PDF |
| 文档被并发删除 | 重新上传 |

---

## 7. 回滚到上一版本

```bash
cd ~/projects/multi-agent-assistant
git log --oneline -10                       # 找上一个稳定 commit/tag
git checkout <上一个 tag 或 commit>
docker compose build
docker compose up -d
docker compose exec backend alembic current  # 若 schema 前进了，执行 downgrade
curl -s http://127.0.0.1/api/v1/health/ready
```

数据兼容性：
- 会话数据（用户上传、任务、产物）在新旧版本间通常兼容；
- 若新版加了列，旧代码读不到但不会崩（新增列均有默认值/NULL 允许）；
- **回滚前建议先 `pg_dump` 备份**（见 deployment.md §6）。

---

## 8. 演示前重置为干净环境（保留数据）

```bash
docker compose down          # 删容器与网络，保留命名卷
docker compose up -d --build
curl -s http://127.0.0.1/api/v1/health/ready
```

彻底重置（**会删除所有数据**，只用于测试环境）：

```bash
docker compose down --volumes
docker compose up -d --build
```

---

## 9. 现场处置优先级（答辩用）

1. **别慌、别现场调代码**。先看 `ready` 与 `docker compose ps`（10 秒内可判断是哪个组件）。
2. 单组件挂了 → `docker compose restart <service>`（Postgres 数据安全、Redis 可降级、Chroma 会重建 BM25）。
3. 30 秒内没恢复 → 切**离线兜底**：用已完成的历史任务展示结果、引用追溯与下载（见 `defense-demo.md` §7），并如实说明是降级演示。
4. 全程不执行任何 `--volumes` 命令。

---

## 10. 恢复后的确认清单

- [ ] `curl /api/v1/health/ready` → `{"status":"ready", checks 全 ok}`
- [ ] 五容器 healthy/running
- [ ] `alembic current` == `alembic heads`
- [ ] 老任务快照仍可查（数据未丢）
- [ ] 新建一个小任务能跑到 `waiting_approval`（链路通）
- [ ] 已完成任务的产物仍可下载
