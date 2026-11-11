# ECS Real Embeddings Shadow-Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Mock 原表持续在线服务，同时在 `knowledge_vectors_bge` 后台生成真实 BGE 向量，通过增量对账后短停切换并保留即时配置回滚。

**Architecture:** 新 Alembic 迁移只增加影子表。`reindex --reconcile` 比较当前 ready chunk 与目标表 ID，只计算缺项并删除孤儿；通用 VectorStore 按 ID 删除同时支持 pgvector 与 Chroma。新代码先以 Mock + 原表上线，再后台重建真实表。

**Tech Stack:** FastAPI、SQLAlchemy、Alembic、PostgreSQL/pgvector、Chroma、SentenceTransformers、pytest、Docker Compose。

**Execution:** 用户已选择本会话串行执行。生产构建、迁移、重建和对账不得并行。

**当前进度：** Task 1–4 已完成；同版 Mock 公网 4/4 PASS，migration head 为 `c4f2a8d7e9b1`。Task 5 影子表全量重建已后台启动，生产继续使用原表 Mock。

---

## Task 1: 影子表迁移（先红后绿）

**Files:**
- Create: `backend/alembic/versions/c4f2a8d7e9b1_add_knowledge_vectors_bge.py`
- Create: `backend/tests/test_migration_knowledge_vectors_bge.py`
- Modify: `backend/tests/test_migration_knowledge_vectors.py`

- [ ] **Step 1: 写迁移链失败测试**

在既有迁移测试增加：

```python
EXPECTED_HEAD = "c4f2a8d7e9b1"

def test_expected_head_is_bge_shadow() -> None:
    assert _script().get_heads() == [EXPECTED_HEAD]
```

新测试文件加载 `c4f2a8d7e9b1_add_knowledge_vectors_bge.py`，断言：

```python
def test_shadow_follows_current_pgvector_revision() -> None:
    revision = _script().get_revision("c4f2a8d7e9b1")
    assert revision is not None
    assert revision.down_revision == "d5a8c1e3f7b2"
```

用既有 `_FakeBind`/`_FakeOp` 风格验证：无 pgvector 时不执行 DDL；扩展在 `extensions` 时，
DDL 包含 `knowledge_vectors_bge`、`"extensions"."vector"(1024)`、三个 `ix_kvbge_*`，
且不包含在原 `knowledge_vectors` 上建索引。downgrade 只执行
`DROP TABLE IF EXISTS knowledge_vectors_bge`。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_migration_knowledge_vectors.py tests/test_migration_knowledge_vectors_bge.py -q --basetemp="$env:TEMP\pytest-bge-migration-red"
```

Expected: 新 head/文件/迁移行为断言因 `c4f2a8d7e9b1` 不存在而失败。

- [ ] **Step 3: 实现最小迁移**

新 revision 固定：

```python
revision = "c4f2a8d7e9b1"
down_revision = "d5a8c1e3f7b2"
VECTOR_DIMENSION = 1024
_TABLE = "knowledge_vectors_bge"
```

复用原迁移的扩展探测/无扩展 no-op 语义，建表列与原表完全一致。索引名只能是
`ix_kvbge_embedding_hnsw`、`ix_kvbge_collection_doc`、`ix_kvbge_collection_user`；
downgrade 只 `DROP TABLE IF EXISTS knowledge_vectors_bge`。

- [ ] **Step 4: 运行迁移测试确认 GREEN**

Run Step 2 命令。Expected: 全部通过，迁移只有一个 head。

## Task 2: 通用按 ID 删除（先红后绿）

**Files:**
- Modify: `backend/app/knowledge/services/vector_backends/base.py`
- Modify: `backend/app/knowledge/services/vector_backends/pgvector_backend.py`
- Modify: `backend/app/knowledge/services/vector_backends/chroma_backend.py`
- Modify: `backend/app/knowledge/services/vector_store.py`
- Test: `backend/tests/test_pgvector_backend_unit.py`
- Test: `backend/tests/test_vector_store.py`

- [ ] **Step 1: 写 pgvector 参数化删除失败测试**

```python
class TestDeleteByIds:
    def test_deletes_by_parameterized_ids(self) -> None:
        backend, pool = _make_backend(script=[{"rowcount": 2}])
        removed = backend.delete_by_ids(["c1", "c2"])
        assert removed == 2
        statement, params = pool.cursor.executed[-1]
        assert "DELETE FROM" in statement
        assert "id = ANY(%s)" in statement
        assert params == ("kb", ["c1", "c2"])

    def test_empty_ids_do_not_touch_database(self) -> None:
        backend, pool = _make_backend()
        assert backend.delete_by_ids([]) == 0
        assert pool.cursor.executed == []
```

- [ ] **Step 2: 写真实 Chroma/VectorStore 失败测试**

```python
def test_remove_chunks_deletes_exact_ids(vector_store: VectorStore) -> None:
    vector_store.add_chunks(_make_chunks(3))
    assert vector_store.remove_chunks(["chunk-0", "chunk-2"]) == 2
    assert vector_store.list_ids() == ["chunk-1"]
    assert vector_store.remove_chunks([]) == 0
```

- [ ] **Step 3: 运行测试确认 RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_pgvector_backend_unit.py tests/test_vector_store.py -q --basetemp="$env:TEMP\pytest-delete-ids-red"
```

Expected: `delete_by_ids/remove_chunks` 不存在而失败。

- [ ] **Step 4: 实现协议、两后端与门面**

协议和门面签名统一为：

```python
def delete_by_ids(self, ids: list[str]) -> int: ...
```

```python
def remove_chunks(self, chunk_ids: list[str]) -> int:
    if not chunk_ids:
        return 0
    return self._backend.delete_by_ids(chunk_ids)
```

pgvector 空列表直接返回 0，否则执行参数化
`DELETE FROM {table} WHERE collection = %s AND id = ANY(%s)`。
Chroma 空列表直接返回 0，否则以 count 差值包裹 `collection.delete(ids=ids)`。

- [ ] **Step 5: 运行测试确认 GREEN**

Run Step 3 命令。Expected: 全部通过，精确删除两个 ID，无额外调用。

## Task 3: reindex 增量对账（先红后绿）

**Files:**
- Modify: `backend/app/knowledge/services/reindex.py`
- Modify: `backend/scripts/reindex_vectors.py`
- Test: `backend/tests/test_reindex_vectors.py`
- Create: `backend/tests/test_reindex_reconcile_cli.py`

- [ ] **Step 1: 扩展测试替身并写核心失败测试**

`_RecordingVectorStore` 增加初始 IDs、`list_ids()` 和 `remove_chunks()` 记录。
核心测试：

```python
@pytest.mark.asyncio
async def test_reconcile_writes_only_missing_and_deletes_orphans() -> None:
    chunks = _chunks(3)
    store = _RecordingVectorStore(initial_ids=[str(chunks[1].id), "orphan"])
    result = await reindex_from_chunks(
        session=_FakeSession(chunks), vector_store=store, batch_size=10, reconcile=True
    )
    assert result.planned == 2
    assert result.indexed == 2
    assert store.batches == [2]
    assert set(store.ids) == {str(chunks[0].id), str(chunks[2].id)}
    assert store.deleted_ids == ["orphan"]
    assert result.orphans_present == 1
    assert result.deleted_orphans == 1
```

另写 dry-run 测试：缺项 2、孤儿 1，但 `batches=[]`、`deleted_ids=[]`。
另写普通模式测试：即使目标已有相同 ID，仍全量 upsert 且 `deleted_ids=[]`，锁住旧行为。

- [ ] **Step 2: 写 CLI 互斥失败测试**

```python
import pytest
from scripts.reindex_vectors import _parse_args

def test_reconcile_rejects_limit() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--reconcile", "--limit", "1"])

def test_reconcile_rejects_user_id() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--reconcile", "--user-id", "00000000-0000-0000-0000-000000000000"])
```

- [ ] **Step 3: 运行测试确认 RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_reindex_vectors.py tests/test_reindex_reconcile_cli.py -q --basetemp="$env:TEMP\pytest-reconcile-red"
```

Expected: 因 `reconcile` 参数、结果字段或 CLI 检查不存在而失败。

- [ ] **Step 4: 实现最小对账逻辑**

`ReindexResult` 增加默认 0 的 `orphans_present/deleted_orphans` 并纳入 `as_dict()`。
普通路径保持原查询与全量 upsert。`reconcile=True` 时：

```python
current_ids = {str(row.id) for row, _owner in rows}
store_ids = set(vector_store.list_ids())
missing_ids = current_ids - store_ids
orphan_ids = sorted(store_ids - current_ids)
rows = [row for row in rows if str(row[0].id) in missing_ids]
```

dry-run 在任何写入前返回缺项数与 `orphans_present=len(orphan_ids)`。
缺项全部写入成功后才调用 `remove_chunks(orphan_ids)`；写失败时保留现场供重试。
CLI 增加 `--reconcile`，传入服务函数，并用 `parser.error()` 拒绝与 limit/user-id 组合。

- [ ] **Step 5: 运行测试确认 GREEN**

Run Step 3 命令，再跑 `test_reindex_vector_index_sync.py`。Expected: 全部通过；
 reconcile 只写两个缺项，普通模式仍全量写。

## Task 4: 本地门禁、提交与部署同版 Mock

**Files:**
- Modify: 上述应用、脚本、迁移与测试文件

- [ ] **Step 1: 运行格式、静态检查与聚焦回归**

```powershell
.\.venv\Scripts\python.exe -m black app/knowledge/services/vector_backends/base.py app/knowledge/services/vector_backends/pgvector_backend.py app/knowledge/services/vector_backends/chroma_backend.py app/knowledge/services/vector_store.py app/knowledge/services/reindex.py scripts/reindex_vectors.py tests/test_migration_knowledge_vectors.py tests/test_migration_knowledge_vectors_bge.py tests/test_pgvector_backend_unit.py tests/test_vector_store.py tests/test_reindex_vectors.py tests/test_reindex_reconcile_cli.py
.\.venv\Scripts\python.exe -m ruff check --fix app/knowledge/services/vector_backends/base.py app/knowledge/services/vector_backends/pgvector_backend.py app/knowledge/services/vector_backends/chroma_backend.py app/knowledge/services/vector_store.py app/knowledge/services/reindex.py scripts/reindex_vectors.py tests/test_migration_knowledge_vectors.py tests/test_migration_knowledge_vectors_bge.py tests/test_pgvector_backend_unit.py tests/test_vector_store.py tests/test_reindex_vectors.py tests/test_reindex_reconcile_cli.py
.\.venv\Scripts\python.exe -m mypy app/knowledge/services scripts/reindex_vectors.py
.\.venv\Scripts\python.exe -m pytest tests/test_embedding.py tests/test_vector_store.py tests/test_pgvector_backend_unit.py tests/test_reindex_vectors.py tests/test_reindex_reconcile_cli.py tests/test_reindex_vector_index_sync.py tests/test_vector_consistency.py tests/test_vector_consistency_ids.py tests/test_migration_knowledge_vectors.py tests/test_migration_knowledge_vectors_bge.py -q --basetemp="$env:TEMP\pytest-shadow-green"
```

Expected: black/ruff/mypy 无错误，测试全绿。若出现无关基线失败，记录并用聚焦门禁判断，不伪造通过。

- [ ] **Step 2: 精确提交**

`git status --short` 逐条核对，只 add 本任务应用/脚本/迁移/测试路径，提交
`feat: reconcile shadow vectors for real embeddings`。禁止 add -A。

- [ ] **Step 3: 同步、构建并以 Mock 原表启动新代码**

用 git bundle 同步；构建 `INSTALL_RAG_MODELS=true`。构建后保持
`RAG_EMBEDDING_MOCK=1`、`RAG__PGVECTOR_TABLE=knowledge_vectors`，
`docker compose up -d --no-build backend` 让新代码自动迁移到 `c4f2a8d7e9b1`。
启动 healthy 后给该镜像打 `veripaper-backend:shadow-mock` 标签。

Expected: `alembic current` 为新 head；影子表存在且 0 行；原表仍 28,341 行；
启动日志为 Mock；公网 verify 4/4 PASS。未满足不得开始后台重建。

## Task 5: 后台全量重建影子表

**Files:**
- Runtime only: `knowledge_vectors_bge`
- Runtime only: `/tmp/vp-shadow-bge-reindex.log`

- [ ] **Step 1: dry-run 与 32 条样本**

一次性容器在 `.env` 基础上显式覆盖：

```bash
-e RAG__PGVECTOR_TABLE=knowledge_vectors_bge -e RAG_EMBEDDING_MOCK=1
```

dry-run 使用 `--reconcile`（空影子表应计划 28,341），样本阶段单独以
`RAG_EMBEDDING_MOCK=0` 执行原 reindex `--limit 32 --batch-size 32`。
Expected: 原 Mock API 持续 ready；影子样本 32/32、0 失败；真实 encode 与缓存有效。

- [ ] **Step 2: 启动唯一全量后台进程**

```bash
nohup docker run --rm --name vp-shadow-bge-reindex --cpus=1 --memory=2500m \
  --network veripaper_default --env-file /opt/veripaper/.env \
  -e RAG__PGVECTOR_TABLE=knowledge_vectors_bge -e RAG_EMBEDDING_MOCK=0 \
  -e HF_HOME=/app/.cache -e OMP_NUM_THREADS=1 \
  -v veripaper_model_cache:/app/.cache \
  --entrypoint python veripaper-backend:latest \
  -m scripts.reindex_vectors --batch-size 100 --json \
  >/tmp/vp-shadow-bge-reindex.log 2>&1 &
echo $! >/tmp/vp-shadow-bge-reindex.pid
```

Expected: 旧 Mock 服务在线，只有影子表接收 BGE 写入。每 2–5 分钟检查日志、load、
内存、Swap、磁盘；达到规格中止线就 `docker stop vp-shadow-bge-reindex`，不并行重启。

- [ ] **Step 3: 全量结果与在线预对账**

Expected 全量 `indexed=planned=28341`（若期间新增则以现场 planned 为准）、0 失败。
随后用相同影子表/真实模型配置运行 `--reconcile --batch-size 100 --json`，只补当前缺项、
删除孤儿。最后 `check_kb_consistency --json` 必须证明 ID 级一致。

## Task 6: 最终对账、短停切换与验收

- [ ] **Step 1: 停止写入并做最终对账**

`docker compose stop backend` 后立即运行影子表 `--reconcile`，再跑 ID 一致性检查。
Expected: 通常 `planned=0/indexed=0`；若有竞态新增则只处理实际差集。任何失败都不切流量。

- [ ] **Step 2: 切到真实表**

`.env` 设置 `RAG__PGVECTOR_TABLE=knowledge_vectors_bge`、`RAG_EMBEDDING_MOCK=0`、
`INSTALL_RAG_MODELS=true`，权限保持 600。只删除 `rag:retrieval:*`，再执行
`docker compose up -d --no-build --force-recreate backend`。

Expected: healthy，日志为 SentenceTransformerEmbeddingService + pgvector 影子表；
BM25 重建完成后公网 ready。

- [ ] **Step 3: 真实语义与缓存验收**

运行本会话已准备的 `vp_verify_real_embeddings_20261111.py`：中文目标
`29159da1-e751-4d30-9ade-b38ac46ee540` 必须在 top 5，英文结果如实记录，所有 ID 为 UUID。
新一次性容器用 `HF_HUB_OFFLINE=1` 实际 encode 1024 维。公网 API ready、frontend 200，
最终无重建进程且负载回落。

- [ ] **Step 4: 验证配置回滚路径但不反复切生产**

核对 `veripaper-backend:shadow-mock` 指向同版代码、原 Mock 表行数/ID 未变。
回滚手册明确：停写后用同版代码 + Mock + `knowledge_vectors` 跑 `--reconcile`，
只补切换后的新写入；不全量重算、不恢复整库。只有用户要求实际回滚时才执行切换，
不做额外生产来回重启。

## Task 7: 文档、提交与备份

- [ ] **Step 1: 回填实测证据**

更新 `ecs-deployment.md` 为影子表 SOP；原地方案保留实测偏差但不再作为操作步骤。
handoff 只有在真实验收全通过后才把待办 #1 标完成。execution-notes 记录迁移 head、
全量数量/耗时、两次 reconcile、负载、检索与回滚锚点。勾选本计划实际完成步骤。

- [ ] **Step 2: 精确提交并推送**

`git diff --check`、`git status --short` 后只 add 本任务文档，提交
`docs: record shadow embedding rollout`。推送 `backup main`，并核对服务器 HEAD、
生产镜像 tag、新 migration head 与最终表名一致。
