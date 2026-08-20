# Hardening, Evaluation And Defense Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将完整工作流冻结为可重复部署和答辩的候选版本，并生成可复查的四组论文实验数据、指标和图表。

**Architecture:** 工程验收使用确定性 LLM/embedding adapter 和固定离线语料；真实模型实验由独立 `evaluation` 包调用同一个 `TaskExecution`，不复制业务逻辑。CI 阻断后端、前端、迁移、镜像、Compose smoke 和固定 E2E。

**Tech Stack:** pytest、pytest-cov、Playwright、Docker Compose、GitHub Actions、Pandas、SciPy、Matplotlib、JSONL、CSV、Markdown。

---

## File Structure

- Create: `backend/tests/e2e/test_literature_review_e2e.py` - 确定性进程级闭环。
- Create: `backend/tests/integration/test_runtime_resilience.py`, `test_security_boundaries.py` - 故障和隔离。
- Create: `backend/tests/fakes/runtime.py` - 固定 LLM、embedding、事件 adapter。
- Create: `scripts/smoke.ps1`, `scripts/smoke.sh` - Compose 验收。
- Create: `docker/smoke/Dockerfile` and `docker/smoke/smoke.py` - 容器内 smoke runner。
- Create: `backend/evaluation/{__init__,cli,runner,metrics,reporting,models}.py` - 实验执行与指标。
- Create: `backend/evaluation/configs/{baseline_a,baseline_b,baseline_c,full}.yaml` - 四个实验组。
- Create: `backend/evaluation/datasets/topics.json`, `ratings_template.csv` - 固定课题和盲评模板。
- Create: `backend/tests/evaluation/test_metrics.py`, `test_runner.py`, `test_reporting.py`.
- Create: `docs/runbooks/deployment.md`, `recovery.md`, `defense-demo.md`, `experiment-reproduction.md`.
- Modify: `backend/pyproject.toml`, `.github/workflows/ci.yml`, `docker-compose.yml`, `.gitignore`.
- Modify: `README.md`, `checklist.md`, `dev-plan-v2.md` - 对齐真实状态与验收证据。

### Task 1: Add A Deterministic Process-Level End-To-End Test

**Files:**
- Create: `backend/tests/fakes/__init__.py`
- Create: `backend/tests/fakes/runtime.py`
- Create: `backend/tests/e2e/test_literature_review_e2e.py`

- [ ] **Step 1: Implement fixed adapters as test code**

`FixedLiteratureLLM` returns responses by system Prompt purpose: valid `TaskPlan`, evidence-backed node content, semantic validation and final review. It records every call and never accesses environment API keys. `FixedEmbedding` uses deterministic vectors. `InMemoryEventPublisher` records ordered events.

- [ ] **Step 2: Write the full E2E test**

The test must:

1. register/login;
2. upload two TXT documents;
3. create a literature task;
4. wait for `WAITING_APPROVAL`;
5. approve the exact plan revision;
6. wait for `COMPLETED`;
7. assert every formal citation resolves to an uploaded chunk;
8. assert event IDs are strictly increasing;
9. download and open Markdown and DOCX.

- [ ] **Step 3: Run test and verify missing integration wiring**

```powershell
Set-Location backend
python -m pytest tests/e2e/test_literature_review_e2e.py -v
```

Expected: FAIL until all runtime dependencies can be injected without global state.

- [ ] **Step 4: Add only required injection points**

Allow `RuntimeContainer` construction with explicit LLM, embedding/reranker, event publisher, storage roots and session factory. Production defaults remain in `main.lifespan`; tests never mutate module globals.

- [ ] **Step 5: Run E2E twice**

```powershell
python -m pytest tests/e2e/test_literature_review_e2e.py -q --count=2
```

If `pytest-repeat` is not installed, run the same command twice manually instead of adding a dependency.

Expected: both runs PASS with identical event and artifact hashes.

- [ ] **Step 6: Commit**

```powershell
git add backend/tests/fakes backend/tests/e2e backend/app/orchestration/runtime.py
git commit -m "test(e2e): cover deterministic review workflow"
```

### Task 2: Prove Recovery, Isolation And Failure Degradation

**Files:**
- Create: `backend/tests/integration/test_runtime_resilience.py`
- Create: `backend/tests/integration/test_security_boundaries.py`
- Modify: `backend/app/orchestration/services/task_execution.py`
- Modify: `backend/app/knowledge/services/document_storage.py`
- Modify: `backend/app/api/v1/endpoints/task_ws.py`

- [ ] **Step 1: Write fault injection cases**

Cover database loss, Redis loss, Chroma failure, arXiv timeout, LLM timeout, process restart after one committed node, validation exhaustion and cancel during an in-flight LLM request.

Expected behavior:

- database loss stops writes and leaves last committed state;
- Redis loss preserves execution and enables polling;
- Chroma failure moves the affected node to `WAITING_HUMAN`;
- arXiv timeout selects offline corpus;
- restart preserves completed attempts;
- cancel discards a late LLM result.

- [ ] **Step 2: Write security boundary cases**

Cover two-user access to tasks, documents, events, WebSocket tickets and artifacts; traversal filenames; bad signatures; oversized uploads; non-HTTP citation URLs; prompt-injection text requesting tool or system access; reused and expired tickets.

- [ ] **Step 3: Run tests and observe real gaps**

```powershell
Set-Location backend
python -m pytest tests/integration/test_runtime_resilience.py tests/integration/test_security_boundaries.py -v
```

Expected: individual cases FAIL until the exact boundary is implemented.

- [ ] **Step 4: Fix one failure category at a time**

For each red case, make the smallest production change, rerun that test, then rerun both files. Do not add a generic catch-all retry. Every exception must map to one of the design error codes and expose `retryable` explicitly.

- [ ] **Step 5: Run full security and recovery suite**

```powershell
python -m pytest tests/integration/test_runtime_resilience.py tests/integration/test_security_boundaries.py tests/test_task_recovery_integration.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/app backend/tests/integration
git commit -m "test(resilience): cover recovery and isolation"
```

### Task 3: Add Compose Smoke Validation

**Files:**
- Create: `docker/smoke/Dockerfile`
- Create: `docker/smoke/smoke.py`
- Create: `scripts/smoke.ps1`
- Create: `scripts/smoke.sh`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Implement one network-local smoke client**

`smoke.py` waits up to 120 seconds for readiness, registers a unique user, logs in, uploads a TXT fixture, creates an offline task using fixed adapter mode, approves, polls completion, downloads both artifacts and exits non-zero on any mismatch.

- [ ] **Step 2: Add a Compose smoke profile**

Add service `smoke` under profile `test`. It depends on healthy Nginx and uses `BASE_URL=http://nginx`. It mounts no source directories and receives no real model keys.

- [ ] **Step 3: Wrap deterministic cleanup**

Both scripts run:

```text
docker compose --profile test up -d --build
docker compose --profile test run --rm smoke
docker compose --profile test down --volumes
```

PowerShell uses `try/finally`; shell uses `trap`. Cleanup removes only Compose resources with project name `multi-agent-assistant-test`.

- [ ] **Step 4: Run smoke locally**

```powershell
.\scripts\smoke.ps1
```

Expected: exit 0 and print one task ID plus artifact sizes; all test volumes are removed.

- [ ] **Step 5: Commit**

```powershell
git add docker/smoke scripts/smoke.ps1 scripts/smoke.sh docker-compose.yml
git commit -m "build(compose): add release smoke validation"
```

### Task 4: Enforce Full Project CI Gates

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `backend/pyproject.toml`
- Modify: `frontend/package.json`, `frontend/package-lock.json`

- [ ] **Step 1: Remove duplicated manual dependency lists**

Backend CI installs the project from `pyproject.toml`:

```text
python -m pip install -e ".[dev]"
```

Heavy real model packages remain in `rag-models` and are not installed in ordinary CI.

- [ ] **Step 2: Make type and coverage gates blocking**

Remove `|| true` and `continue-on-error` from Mypy. Run:

```text
black --check app tests evaluation
ruff check app tests evaluation
mypy app
pytest tests --cov=app --cov-fail-under=80
```

- [ ] **Step 3: Add frontend job**

Use Node 20 and `npm ci`, then run unit tests, type check and build. Upload Vitest coverage only when generated; no fabricated threshold until coverage instrumentation is configured. Critical task/auth stores and views must have unit tests before merge.

- [ ] **Step 4: Add migration and Compose smoke jobs**

Migration job applies `alembic upgrade head`, downgrades one revision and upgrades again against PostgreSQL 16. Smoke job invokes `scripts/smoke.sh` after backend/frontend jobs pass.

- [ ] **Step 5: Run equivalent commands locally**

```powershell
Set-Location backend
python -m black --check app tests evaluation
python -m ruff check app tests evaluation
python -m mypy app
python -m pytest tests --cov=app --cov-fail-under=80
Set-Location ..\frontend
npm run test:unit -- --run
npm run type-check
npm run build
Set-Location ..
.\scripts\smoke.ps1
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add .github/workflows/ci.yml backend/pyproject.toml frontend/package.json frontend/package-lock.json
git commit -m "ci: enforce full project quality gates"
```

### Task 5: Define Reproducible Experiment Inputs

**Files:**
- Create: `backend/evaluation/__init__.py`, `models.py`
- Create: `backend/evaluation/configs/baseline_a.yaml`, `baseline_b.yaml`, `baseline_c.yaml`, `full.yaml`
- Create: `backend/evaluation/datasets/topics.json`
- Create: `backend/evaluation/datasets/ratings_template.csv`
- Modify: `backend/pyproject.toml`
- Modify: `.gitignore`

- [ ] **Step 1: Add isolated evaluation dependencies**

Add optional group:

```toml
evaluation = [
  "pandas>=2.2,<3",
  "scipy>=1.13,<2",
  "matplotlib>=3.9,<4",
]
```

- [ ] **Step 2: Define four immutable experiment profiles**

```yaml
# baseline_a.yaml
name: single_agent_no_rag
agent_mode: single
retrieval_mode: none
validation_enabled: false
review_enabled: false

# baseline_b.yaml
name: multi_agent_no_rag
agent_mode: multi
retrieval_mode: none
validation_enabled: true
review_enabled: true

# baseline_c.yaml
name: multi_agent_vector_rag
agent_mode: multi
retrieval_mode: vector
validation_enabled: true
review_enabled: true

# full.yaml
name: multi_agent_hybrid_rerank
agent_mode: multi
retrieval_mode: hybrid_rerank
validation_enabled: true
review_enabled: true
```

All profiles also set the same provider/model, temperature `0.2`, max output tokens, Prompt version, corpus version and random seed. Experiment-only switches are accepted by `evaluation.runner`, not exposed through public task endpoints.

- [ ] **Step 3: Freeze 15 topics across three domains**

`topics.json` contains these exact topics:

```text
AI: RAG evaluation, multi-agent coordination, LLM hallucination, prompt engineering, knowledge graph QA
Software engineering: automated code review, test generation, defect prediction, CI optimization, requirements traceability
Education technology: adaptive learning, learning analytics, AI tutoring, automated feedback, academic integrity
```

Each item has `topic_id`, Chinese topic, English search query, domain and corpus manifest path.

- [ ] **Step 4: Define blind human rating input**

`ratings_template.csv` columns are:

```text
anonymous_output_id,evaluator_id,accuracy,coherence,completeness,citation_quality,notes
```

Scores are integers 1 to 5. Output IDs must not reveal experiment group.

- [ ] **Step 5: Validate inputs**

Add Pydantic `ExperimentConfig`, `EvaluationTopic` and `HumanRating` models. A CLI validation command loads every profile/topic and fails on extra fields, duplicate topic IDs or missing corpus manifests.

- [ ] **Step 6: Commit**

```powershell
git add backend/evaluation backend/pyproject.toml .gitignore
git commit -m "feat(evaluation): define reproducible experiments"
```

### Task 6: Implement Experiment Runner And Metrics

**Files:**
- Create: `backend/evaluation/cli.py`, `runner.py`, `metrics.py`
- Create: `backend/tests/evaluation/test_metrics.py`, `test_runner.py`

- [ ] **Step 1: Write metric unit tests**

Use small fixed rankings and claims to assert exact values for Recall@5, reciprocal rank, nDCG@5, citation resolvability, evidence coverage, error citation rate, duplicate paragraph ratio, success rate, mean duration, total tokens, retries and recovery success.

- [ ] **Step 2: Run tests and verify imports fail**

```powershell
Set-Location backend
python -m pytest tests/evaluation/test_metrics.py tests/evaluation/test_runner.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement metric functions as pure functions**

Every metric receives plain typed records and returns `float | None`. Return `None` when a denominator is undefined; never silently report zero for missing labels. nDCG uses graded relevance from qrels; MRR uses the first relevant result.

- [ ] **Step 4: Implement resume-safe experiment execution**

Runner matrix is 15 topics x 4 profiles x 2 repetitions = 120 runs. Each run writes atomically to:

```text
artifacts/evaluation/<run-set-id>/<profile>/<topic-id>/<repetition>/
  config.json
  task.json
  events.jsonl
  result.md
  review.json
  metrics.json
```

If all files and hashes are valid, resume skips the run. Failed runs retain logs and may be retried explicitly; they are never overwritten.

- [ ] **Step 5: Add the CLI**

```powershell
python -m evaluation.cli validate
python -m evaluation.cli run --profiles all --repetitions 2
python -m evaluation.cli run --profiles full --topic ai-rag-evaluation --repetitions 1
```

The CLI prints the run-set ID, completed/skipped/failed counts and artifact root.

- [ ] **Step 6: Run unit tests and one fixed-adapter dry run**

```powershell
python -m pytest tests/evaluation/test_metrics.py tests/evaluation/test_runner.py -q
python -m evaluation.cli run --adapter fixed --profiles all --topic ai-rag-evaluation --repetitions 1
```

Expected: PASS and four run directories.

- [ ] **Step 7: Commit**

```powershell
git add backend/evaluation backend/tests/evaluation
git commit -m "feat(evaluation): add experiment runner and metrics"
```

### Task 7: Generate Reviewable Thesis Reports

**Files:**
- Create: `backend/evaluation/reporting.py`
- Create: `backend/tests/evaluation/test_reporting.py`
- Create: `docs/runbooks/experiment-reproduction.md`

- [ ] **Step 1: Write report tests**

Given a fixed result fixture, assert one CSV row per run, aggregate mean/median/95% bootstrap interval, paired Wilcoxon results against baseline A, and PNG files for quality, retrieval, latency, token and recovery metrics.

- [ ] **Step 2: Run test and verify import failure**

```powershell
Set-Location backend
python -m pytest tests/evaluation/test_reporting.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement report generation**

Generate:

```text
summary.csv
aggregate.csv
significance.csv
figures/retrieval.png
figures/content_quality.png
figures/citations.png
figures/latency_tokens.png
figures/recovery.png
report.md
```

Plots must include labels, sample size and confidence intervals. `report.md` links every table/figure to the run-set ID and records missing/failed runs rather than dropping them.

- [ ] **Step 4: Keep LLM Judge supplementary**

Report deterministic and human metrics first. LLM Judge appears in a separate section with model/version/Prompt and cannot be averaged into the primary quality score.

- [ ] **Step 5: Run report tests and fixed dry report**

```powershell
python -m pytest tests/evaluation/test_reporting.py -q
python -m evaluation.cli report --run-set latest-fixed
```

Expected: PASS and all listed artifacts exist.

- [ ] **Step 6: Commit**

```powershell
git add backend/evaluation/reporting.py backend/tests/evaluation/test_reporting.py docs/runbooks/experiment-reproduction.md
git commit -m "feat(evaluation): generate thesis reports"
```

### Task 8: Freeze Deployment, Recovery And Defense Runbooks

**Files:**
- Create: `docs/runbooks/deployment.md`
- Create: `docs/runbooks/recovery.md`
- Create: `docs/runbooks/defense-demo.md`
- Modify: `README.md`
- Modify: `checklist.md`
- Modify: `dev-plan-v2.md`

- [ ] **Step 1: Write deployment runbook from verified commands**

Include prerequisites, `.env.example` fields, build/start/migrate/readiness commands, model cache preparation, offline corpus preparation, backup paths, stop commands and exact acceptance checks. Do not include real secrets.

- [ ] **Step 2: Write recovery runbook**

Cover database unavailable, Redis unavailable, Chroma unavailable, failed migration, stuck task, corrupted upload and rollback to the previous image. Recovery commands must preserve PostgreSQL, upload and artifact volumes.

- [ ] **Step 3: Write the 5-to-10-minute defense script**

Sequence:

1. show readiness;
2. login;
3. show frozen corpus;
4. create offline task;
5. approve plan;
6. show parallel DAG nodes;
7. demonstrate one controlled retry using a prepared validation failure;
8. show citation traceability;
9. download Markdown/DOCX;
10. show the four-group experiment chart.

Include an offline fallback and a previously completed task for inspection, but never present prerecorded output as a live run.

- [ ] **Step 4: Rewrite project status to four states**

For each checklist capability record `implemented`, `tested`, `integrated`, and `accepted`. Mark only evidence-backed states. Replace the old 23-week/19-week contradiction with the confirmed 10-week route. Remove code generation and e-commerce from current delivery claims.

- [ ] **Step 5: Perform two complete rehearsals**

Run from a clean Compose start. Record exact duration, failures, fallback use and fixes. The second rehearsal must complete without manual database or terminal repair after the demo begins.

- [ ] **Step 6: Verify documentation links and commands**

```powershell
Get-ChildItem README.md,checklist.md,dev-plan-v2.md,docs\runbooks\*.md | Select-String -Pattern 'TBD|TODO|localhost:8000|阶段0仅'
```

Expected: no stale placeholders or stage-0 deployment instructions in active sections.

- [ ] **Step 7: Commit**

```powershell
git add README.md checklist.md dev-plan-v2.md docs/runbooks
git commit -m "docs(defense): add release and demo runbooks"
```

## Plan 5 Exit Gate

The release candidate is accepted only when:

- all blocking CI jobs pass;
- clean Compose smoke passes;
- two-user isolation and restart recovery pass;
- the offline real-model demo completes within 5 to 10 minutes;
- all formal citations resolve;
- the 120-run experiment command is resume-safe;
- at least two full rehearsals are recorded;
- no P0/P1 defect remains open.
