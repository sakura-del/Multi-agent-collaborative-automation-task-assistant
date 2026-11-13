# Supabase 旧库下线提醒 — 执行记录

## 2026-11-12 16:16（首次执行，已完成核对与记账）
- 生产无依赖已实测通过：DSN 均指向本机 `postgres:5432`，`/health/ready` ready，`/knowledge/search` 3 命中，`verify.sh` 4/4 PASS。
- 备份容器本就备份本机库，Supabase 删除不会造成生产无备份。
- 文档已改：`AGENTS.md` §4.2、`docs/runbooks/2026-11-11-handoff.md` §9/§10、`docs/runbooks/ecs-deployment.md` §2/§4/§7.B/§12；提交 `ecdccb2`，已推 `backup/main`。
- 阻断/待办：控制台删除需用户本人操作，未执行。文档已按“已下线”表述。
- 结论要点：删除后唯一整库重建来源 = `/opt/veripaper/backup/` 下 dump；日常恢复优先七牛 `db-backups/` 每日备份。

## 后续若要再跑
仅在用户确认「已删除」后复核一次 `/health/ready` 与检索；无需重复改文档。若用户决定保留 Supabase，必须回退文档中的“已下线/回滚路径关闭”表述。
