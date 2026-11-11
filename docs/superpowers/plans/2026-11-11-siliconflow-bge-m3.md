# SiliconFlow BGE-M3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用带 RPM/TPM 双限流的 SiliconFlow `BAAI/bge-m3` API 替换生产本地模型推理，在 Mock 原表持续在线的前提下重建并切换影子向量表。

**Architecture:** 独立 API 适配器负责批量、响应校验和有界重试；独立滑动窗口限流器负责每次 HTTP 尝试的请求/token 预算。既有 reindex/reconcile、pgvector 影子表和 Mock 回滚不变。

**Tech Stack:** Python 3.11、httpx、Pydantic Settings、FastAPI、PostgreSQL/pgvector、Docker Compose、pytest。

**Execution:** 沿用用户已确认的本会话串行执行。任何生产清空/重建都在自动测试与真实 API 小样本之后。

---

## 文件结构

- Create `backend/app/knowledge/services/embedding_rate_limiter.py`：进程内 60 秒滑动窗口。
- Create `backend/app/knowledge/services/siliconflow_embedding.py`：API 批量、重试与响应校验。
- Modify `backend/app/knowledge/services/embedding.py`：provider 工厂，API 失败不降级 Mock。
- Modify `backend/app/core/config.py`：provider、凭据与限额配置。
- Modify `backend/app/knowledge/services/__init__.py`：显式导出 API 服务。
- Create `backend/tests/test_embedding_rate_limiter.py`：假时钟与假睡眠。
- Create `backend/tests/test_siliconflow_embedding.py`：httpx MockTransport 与真实调用序列。
- Modify `backend/tests/test_embedding.py`：provider 选择与无静默降级。
- Modify `deploy/ecs/docker-compose.yml`、`deploy/ecs/env.example`：API 模式不安装 torch。
- Modify runbook、handoff、execution-notes：只写现场结果。

## Task 1: 滑动窗口双限流

**Files:**
- Create: `backend/app/knowledge/services/embedding_rate_limiter.py`
- Create: `backend/tests/test_embedding_rate_limiter.py`

- [ ] **Step 1: 写 RPM/TPM 独立触发与实际用量修正测试**

测试用假 `clock` 和 `sleep`，每次 sleep 推进同一时间。至少覆盖：

```python
def test_rpm_waits_for_oldest_event() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(2, 1000, clock=clock)
    limiter.acquire(10)
    limiter.acquire(10)
    record = limiter.acquire(10)
    assert clock.sleeps == [60.0]
    assert record.tokens == 10


def test_tpm_waits_even_when_rpm_is_available() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(100, 100, clock=clock)
    limiter.acquire(80)
    limiter.acquire(30)
    assert clock.sleeps == [60.0]


def test_actual_usage_replaces_estimate() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(10, 100, clock=clock)
    record = limiter.acquire(80)
    limiter.record_actual(record, 20)
    limiter.acquire(80)
    assert clock.sleeps == []


def test_oversized_single_request_is_rejected() -> None:
    with pytest.raises(ValueError, match="TPM"):
        SlidingWindowRateLimiter(10, 100).acquire(101)
```

`FakeClock` 提供 `monotonic()`、`sleep()`、`sleeps` 列表。窗口过期条件用
`event.at <= now - 60`，锁内完成检查与记录。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_embedding_rate_limiter.py -q --basetemp="$env:TEMP\pytest-sf-limiter-red"
```

Expected: 因限流模块/类不存在而失败。

- [ ] **Step 3: 实现最小限流器**

公开接口：

```python
@dataclass
class UsageRecord:
    at: float
    tokens: int


class SlidingWindowRateLimiter:
    def __init__(self, rpm: int, tpm: int, *, clock: Any = None) -> None: ...
    def acquire(self, tokens: int) -> UsageRecord: ...
    def record_actual(self, record: UsageRecord, actual_tokens: int) -> None: ...
```

默认 clock 封装 `time.monotonic/time.sleep`。用 `threading.Lock`、`deque`、
`used_tokens` 维护窗口；wait 时依次计算需要过期的最早事件，直至 RPM 与 TPM
都允许。每次 acquire 返回独立记录；record_actual 在锁内修正窗口合计。
空/负 token 和超过 TPM 的请求直接 ValueError。

- [ ] **Step 4: 运行测试确认 GREEN**

Run Step 2 命令。Expected: 四种行为全绿，等待精确到 60 秒边界。

## Task 2: SiliconFlow 批量、契约与重试

**Files:**
- Create: `backend/app/knowledge/services/siliconflow_embedding.py`
- Create: `backend/tests/test_siliconflow_embedding.py`

- [ ] **Step 1: 写空输入、批量和响应顺序失败测试**

用 `httpx.MockTransport` 捕获请求，返回两个乱序 data 项，每项 1024 维有限向量：

```python
def test_empty_input_sends_no_request() -> None:
    service = make_service(handler=lambda request: (_ for _ in ()).throw(AssertionError))
    assert service.embed([]) == []


def test_batch_response_is_reordered_by_index() -> None:
    service = make_service_with_reversed_data()
    result = service.embed(["第一条", "第二条"])
    assert result[0][0] == 0.0
    assert result[1][0] == 1.0
    assert len(result) == 2
    assert all(len(vector) == 1024 for vector in result)
```

再写 65 条输入测试：必须产生 64+1 两个请求；usage 实际 token 必须调用
`record_actual`。读取 JSON 请求，断言 model=`BAAI/bge-m3`、encoding_format=float，
 Authorization 只出现在请求头，日志/断言不得输出 key。

- [ ] **Step 2: 写失败与重试序列测试**

- 400/401/403/404：只调用 1 次并抛配置错误；
- 429 响应带 `Retry-After: 2` 后成功：调用 2 次，限流 acquire 也 2 次，sleep(2)；
- 第一次 503、第二次 200：有界退避并成功；
- 连续 429：最多 6 次总调用（初次+5 重试）后抛 `VectorStoreUnavailableError`；
- 缺少 data、data 数量不符、非 1024 维、NaN、响应 model 不符均失败且不返回向量；
- 缺 API key 在构造时明确失败，不访问 HTTP。

- [ ] **Step 3: 运行测试确认 RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_siliconflow_embedding.py -q --basetemp="$env:TEMP\pytest-sf-client-red"
```

Expected: 因服务类不存在而失败。

- [ ] **Step 4: 实现 API 服务**

构造参数允许注入 `client`、`limiter`、`sleep`；生产默认 httpx.Client(timeout=60)、
真实滑动窗口和 time.sleep。公开 `dimension=1024` 与 `embed(texts)`。

请求前按 64 条和 TPM 预估值拆分。预估函数固定为
`sum(len(text.encode("utf-8")) + 4)`。每次 HTTP 尝试都 acquire；成功响应先用
`usage.total_tokens` 修正窗口，再校验 model/data/index/维度/有限数值。

400/401/403/404 直接配置错误；429、httpx 网络错误和 5xx 最多重试 5 次。
Retry-After 合法且不超过 60 秒时优先，否则使用 1/2/4/8/16 秒；超过 60 秒直接
`VectorStoreUnavailableError`。所有重试重新 acquire。日志只含状态、trace ID、
数量、token、重试和耗时，不含正文/key。

- [ ] **Step 5: 运行测试确认 GREEN**

Run Step 3 命令。Expected: 全部通过，65 条为两次请求，429 的两次尝试均被计数。

## Task 3: 配置、工厂与精简生产镜像

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/knowledge/services/embedding.py`
- Modify: `backend/app/knowledge/services/__init__.py`
- Modify: `backend/tests/test_embedding.py`
- Modify: `deploy/ecs/docker-compose.yml`
- Modify: `deploy/ecs/env.example`

- [ ] **Step 1: 写 provider 与无降级失败测试**

```python
def test_siliconflow_provider_returns_api_service(monkeypatch) -> None:
    configure_rag(provider="siliconflow", model="BAAI/bge-m3", api_key="test-key")
    service = create_embedding_service(force_mock=False)
    assert isinstance(service, SiliconFlowEmbeddingService)
    assert service.dimension == 1024


def test_siliconflow_missing_key_does_not_fallback_to_mock(monkeypatch) -> None:
    configure_rag(provider="siliconflow", api_key=None)
    with pytest.raises(VectorStoreConfigError):
        create_embedding_service(force_mock=False)
```

保留现有 Mock/本地 provider 测试。配置字段默认 provider=local、模型不变；
API key 默认 None，只有生产 `.env` 注入。

- [ ] **Step 2: 运行测试确认 RED**

Run `pytest tests/test_embedding.py`；Expected: provider/类配置不存在而失败。

- [ ] **Step 3: 实现配置与工厂**

RAGConfig 新增 `embedding_provider="local"`、`siliconflow_api_key`、
`siliconflow_base_url="https://api.siliconflow.cn/v1"`、`embedding_rpm=1600`、
`embedding_tpm=400000`、`embedding_batch_size=64`，正数用 Field(gt=0)。

工厂在 Mock 之后显式分支 siliconflow，并在该分支外不得被本地模型的
`except Exception → Mock` 捕获。未知 provider 直接配置错误。

ECS 默认 `INSTALL_RAG_MODELS=false`；env 模板设置 M3 provider、限额与空 key，
mock=0，重排仍 Mock。HF/model_cache 保留给显式本地 provider，不做无关清理。

- [ ] **Step 4: 运行测试确认 GREEN**

Run `test_embedding.py`、`test_embedding_rate_limiter.py`、
`test_siliconflow_embedding.py`。Expected: 全部通过。

## Task 4: 全量门禁、提交与同版 Mock 部署

- [ ] **Step 1: 运行本地门禁**

```powershell
.\.venv\Scripts\python.exe -m black app tests evaluation
.\.venv\Scripts\python.exe -m ruff check --fix app tests evaluation
.\.venv\Scripts\python.exe -m mypy app evaluation
.\.venv\Scripts\python.exe -m pytest tests -q --cov=app --cov-fail-under=80 --basetemp="$env:TEMP\pytest-siliconflow-full"
```

Expected: 全绿且覆盖率至少 80%。长测试后台运行，每轮最多等待 5 分钟。

- [ ] **Step 2: 精确提交**

只 add 本计划列出的应用、测试与 ECS 文件；`git status --short` 核对后提交
`feat: add rate-limited siliconflow embeddings`。不提交暂停文档或用户并行文件。

- [ ] **Step 3: 构建精简镜像并验收同版 Mock**

git bundle 同步 ECS，`INSTALL_RAG_MODELS=false` 构建。保持生产
`RAG_EMBEDDING_MOCK=1`、原表，启动新代码，确认 migration 仍 `c4f2a8d7e9b1`、
原表 28,341、影子表当前 2700、公网 Mock 4/4 PASS。失败则不进入 API 重建。

## Task 5: 真实 M3 样本、清理旧影子与后台重建

- [ ] **Step 1: 写入服务器凭据并做真实小样本**

将用户提供的 key 只写入 `/opt/veripaper/.env`，权限 600，不回显。用生产配置调用
2 条不同短文本，断言 HTTP 200、BGE-M3、两条 1024 维、顺序正确、usage 存在。
未通过就停止，不清空影子表。

- [ ] **Step 2: 清空旧 BGE-large 部分影子数据**

用现有 `VectorStore.clear()` 且只配置 `knowledge_vectors_bge`，随后回查：
影子表 0、原表 28,341。不得手工 DROP/改 migration，不得触碰原表。

- [ ] **Step 3: dry-run 与 32 条 M3 样本**

先 `--reconcile --dry-run --json`，空影子应 planned=28,341、orphans=0。
再以 M3 API 跑 `--limit 32 --batch-size 32 --json`，要求 32/32、0 失败。
原 Mock 公网持续 ready。

- [ ] **Step 4: 后台全量重建**

一次性容器显式设置影子表、provider=siliconflow、model=BGE-M3、mock=0，
使用 `nohup` 和唯一容器名 `vp-shadow-m3-reindex`。不再加载本地模型。
每 2–5 分钟只取最新批次、请求/token/429 日志、主机 load/内存/磁盘；
出现持续 429、非重试错误或资源越线时停止容器，保留 Mock 与已写影子数据。

Expected: 最终 `indexed == planned`、`failed_batches=0`，并记录真实 elapsed、
usage 与等待情况。不得为了提高速度超过 1600 RPM/400,000 TPM。

## Task 6: 对账、切换、验收与文档

- [ ] **Step 1: 在线预对账与停写最终对账**

全量后先运行 M3 `--reconcile`，再停止 backend 并做第二次 reconcile 和
`check_kb_consistency --json`。要求缺失/孤儿 ID 均为 0。失败则不切换。

- [ ] **Step 2: 切到 M3 并验收**

`.env` 最终为 provider=siliconflow、model=BAAI/bge-m3、影子表、mock=0、
INSTALL_RAG_MODELS=false。只清理 `rag:retrieval:*`，启动 backend。
验证启动日志为 SiliconFlowEmbeddingService、真实中文查询目标
`29159da1-e751-4d30-9ade-b38ac46ee540` 在 top 5、英文结果如实记录、
ID 均为 UUID、公网 health ready 和 frontend 200。

- [ ] **Step 3: 回滚证据**

保留同版 `shadow-mock` 镜像与原表。手册要求停写后用 Mock 对原表 reconcile，
再切回，不做整库恢复。只有用户明确要求时才实际回滚。

- [ ] **Step 4: 回填并推送**

更新 ecs runbook、handoff、execution-notes 和本计划，记录现场请求数、token、
429、耗时、重建数量、检索与公网结果。精确提交文档并推送 backup main；
生产 HEAD、镜像、provider、表名与 migration head 必须一致。
