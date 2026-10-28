# 实验矩阵 60 run

> 日期：2026-10-27 ｜ 状态：**运行中**
> 前序：[2026-10-27-03-model-switch-and-404-fallback-plan.md](2026-10-27-03-model-switch-and-404-fallback-plan.md)

---

## 1. 任务定义

跑真实实验矩阵，为论文提供数据。

| 项 | 值 |
| --- | --- |
| 画像 | 4（`single_agent_no_rag` / `multi_agent_no_rag` / `multi_agent_vector_rag` / `multi_agent_hybrid_rerank`） |
| 课题 | 15（ai 5 + software_engineering 5 + education_technology 5） |
| 重复 | 1 |
| **总计** | **4 × 15 × 1 = 60 run** |
| 命令 | `python -m scripts.run_experiments --profiles all --repetitions 1` |

**注意**：`validate` 子命令报告 `runs: 120`，那是按 `runs_per_topic: 2`（画像 yaml 里的
字段）算的；实际 run 数由 CLI 的 `--repetitions` 决定。**60 = `--repetitions 1`**，
已实测确认（`build_matrix(profiles, topics, 1)` → 60）。

---

## 2. 开工前发现的元数据失真（已修）

### 2.1 缺陷

画像 yaml 与 `evaluation/models.py` 的 `FROZEN_MODEL` 都写 `gpt-6-luna`，
但**实际执行根本不读这个字段**：

```python
# scripts/run_experiments.py
llm = get_settings().llm     # ← 真实调用走这里（当前 workbuddy-ai/deepseek-v4.1-flash）
```

画像的 `model` 只被写进每个 run 的 `config.json`（`runner.py:220`），
**那是论文记录实验条件的唯一来源**。

### 2.2 后果

不改就跑，会产出 60 个 `config.json` 声称「用 gpt-6-luna 跑的」，
而实际是 DeepSeek 跑的。而 `gpt-6-luna` **已在 2026-10-27 被端点下架**，
这个声称**永远不可能为真**。

这不是「注释过期」，是**编造实验条件** —— 论文里写「用 X 模型实验」而实际不是。

### 2.3 修法

`FROZEN_MODEL` 与 4 份画像 yaml 统一改成 `workbuddy-ai/deepseek-v4.1-flash`，
并加两条测试把「记录的模型」钉在 `config/default.yaml` 的 `llm.default_model` 上：

| 测试 | 断言 |
| --- | --- |
| `test_frozen_model_matches_runtime_config` | `FROZEN_MODEL` == 运行时 `default_model` |
| `test_all_profiles_use_frozen_model` | 4 份 yaml 的 `model` 都 == `FROZEN_MODEL` |

**这样下次换模型会强制同步画像**，而不是再次静默漂移。

---

## 3. 验收标准

| # | 标准 | 怎么验 |
| --- | --- | --- |
| A1 | 60 个 run 全部产出产物 | 数 `rep-1` 叶子目录个数 |
| A2 | `config.json` 记录的模型正确 | 抽检 `model` 字段 |
| A3 | 失败率可解释 | 统计 `status` 分布，非 completed 的逐个看原因 |
| A4 | 产物落盘到宿主机 | 容器内 `/app/evaluation/runs/` **无挂载**，必须 `docker cp` 出来 |
| A5 | 能生成报告 | `evaluation.cli report` 跑通 |

---

## 4. 风险

| 风险 | 缓解 |
| --- | --- |
| **产物在容器内，无挂载** | `docker inspect` 确认只有 data/artifacts、cache、uploads 三个卷；跑完 `docker cp` 到宿主机 |
| 单 run 超时 1800s | 实测单个约 60~75s，余量充足 |
| 新模型质量导致转人工 | 已知风险（§3.13.4 复杂任务曾 `semantic_consistency` 失败）；如实统计，不掩盖 |
| 60 run 中途容器重建 | 运行期间不重建 backend |

---

## 5. 顺带发现的第二个真实缺陷：报告隐藏失败

### 5.1 现象

生成的 `report.md` 头部写：

```
- 运行总数：**60**
- 完成：**56**　失败：**0**　不完整：**0**
```

**56 + 0 + 0 = 56 ≠ 60**。4 个 run 凭空消失。

第 4 节还写「**无失败或不完整运行**」。

### 5.2 根因

`reporting.py` 只认 `status == "failed"`：

```python
failed = [record for record in records if record["status"] == "failed"]
incomplete = [record for record in records if record["status"] == "incomplete"]
```

而这次 4 个失败 run 的 `metrics.json` 里 `status` 是 **`waiting_human`**
（校验失败转人工）—— 它既不是 `completed` 也不是 `failed`，
于是**从三个计数里全部漏掉**。

### 5.3 为什么这个缺陷很严重

`aggregate.csv` 只统计 `completed`，所以 `multi_agent_hybrid_rerank` 的
`success` 显示 **1.0**（n=11）—— 排除 4 个失败后看起来 100% 成功。

**真实成功率是 11/15 = 73.3%。**

报告掩盖了本轮最重要的发现（最强画像表现最差），会直接误导论文结论。
如果不修，论文会写成「四个画像成功率均为 100%」。

### 5.4 修法

1. 计数改成**互补分桶**：凡 `status != "completed"` 都必须落进
   `failed` / `incomplete` / `其他未完成状态` 之一，保证四者之和 == 总数；
2. 第 4 节**点名列出** `waiting_human` 的 run；
3. 加一段**统计口径提醒**：明确「聚合只统计 completed」，
   并要求以「completed 数 / 总数」作为真实成功率，不能只看 `success` 均值。

### 5.5 测试

`tests/test_reporting_non_completed.py`（3 条）：

| 测试 | 断言 |
| --- | --- |
| `test_waiting_human_is_not_silently_dropped` | 非 completed 的 run 必须被算作非完成，总数互补 |
| `test_report_header_counts_add_up` | 所有分类计数之和 == 运行总数 |
| `test_waiting_human_is_named_in_report` | 报告必须点名 `waiting_human`，不能说「无失败」 |

诚实性验证：去掉 `waiting_human` 分桶后，**2 条变红**（计数和对不上 + 报告仍说「无失败」）。

### 5.6 真实数据验证（服务器不可达时的替代取证）

**限制**：服务器在 2026-10-28 不可达（`ping` 100% 丢包、22/48080 端口均不通），
且 run-set 产物在**容器内无挂载**，无法远程重新生成报告。

**替代取证**：用**真实的** `summary.csv`（60 行，status 真实）+
`aggregate.csv` + `significance.csv` 喂给修复后的 `_build_report_markdown`，
重建 `report.md`。这不是模拟数据 —— 状态分布、聚合值、显著性值全部来自真实 run。

**修复前后对比**：

| | 修复前 | 修复后 |
| --- | --- | --- |
| 表头 | 完成 56 / 失败 0 / 不完整 0（**和 = 56 ≠ 60**） | 完成 56 / 失败 0 / 不完整 0 / **其他 4（waiting_human 4）**（**和 = 60** ✓） |
| 第 4 节 | 「**无失败或不完整运行**」 | 点名列出 4 个 `waiting_human` 的 run_id |
| 口径提醒 | 无 | 明确「聚合只统计 56/60，存在幸存者偏差，真实成功率看 completed/总数」 |

产物：`docs/evaluation-runs/report/report-fixed.md`。

**遗留**：等服务器恢复后，仍需用真实 run-set 重新跑一次
`evaluation.cli report` 覆盖 `report.md`（本轮的 `report-fixed.md` 只覆盖了
表格与失败清单部分，图表段落是静态文本、不受影响）。

---

## 6. 实施结果

### 6.1 完成情况

```
开始：2026-10-27T14:15:51+08:00
结束：约 15:28
总耗时：4364 秒（73 分钟）
单个均速：72 秒
```

| 项 | 值 |
| --- | --- |
| run-set | `rs-20261027T061627Z` |
| 总 run | **60 / 60** |
| completed | **56** |
| waiting_human | **4** |
| 产物 | 361 个文件，6.1 MB（已从容器拷到 `~/maa_eval_runs/`） |
| 报告 | 9 个产物（summary/aggregate/significance + 5 张图 + report.md） |

### 6.2 **失败全部集中在最强画像**（重要发现）

```
single_agent_no_rag          : completed 15 / 15   ✅
multi_agent_no_rag           : completed 15 / 15   ✅
multi_agent_vector_rag       : completed 15 / 15   ✅
multi_agent_hybrid_rerank    : completed 11 / 15   ❌ 4 个 waiting_human
```

失败的 4 个课题：`ai-rag-eval`、`se-test-gen`、`se-defect-pred`、`edu-adaptive`。
**同一个课题在其他三个画像下全部 completed** —— 所以不是课题太难，是画像特有的问题。

### 6.3 根因（从 `validation_result` 表查证）

三类失败原因，全部是**证据多导致的引用管理问题**：

| 原因 | 实例 |
| --- | --- |
| **证据编号捆绑引用** | `"E4的框架[E1][E2][E4][E5]"`、`"E9的FAIR-RAG框架[E6]"` —— 一个编号被捆绑进多个引用，无法判断指代哪篇 |
| **占位符残留** | `"'– 仅支持数据集用途'"` 里的短横线是**没被替换的占位符**，直接进了正文 |
| **参考文献重复** | `Ragas` 条目在参考文献列表出现两次（第 2、3 条完全相同） |

**为什么只有最强画像出问题**：它检索到的证据最多（`evidence_count` 16~18，其他画像更少），
模型在引用大量证据时编号混乱、且留下了未填充的占位符。

### 6.4 ⚠️ 统计口径陷阱（必须写进论文）

`aggregate.csv` 里：

```
multi_agent_hybrid_rerank, success, 11, 1.0
                                          ↑ 只统计了成功的 11 个
```

**`success = 1.0` 是假的**：它把 4 个失败样本**排除在统计外**了。
真实成功率是 **11/15 = 73.3%**，而其他三个画像是 **15/15 = 100%**。

如果不看 `n` 只看 `mean`，会得出「四个画像成功率都是 100%」的错误结论。
**报告解读时必须同时报 `n`**。

### 6.5 核心指标对照

| 指标 | single_agent | multi_no_rag | multi_vector | **multi_hybrid** |
| --- | --- | --- | --- | --- |
| 有效样本 n | 15 | 15 | 15 | **11** |
| **真实成功率** | 100% | 100% | 100% | **73.3%** |
| `citation_resolvability` | 1.0 | 0.9978 | 0.9613 | 0.9537 |
| `evidence_coverage` | 1.0 | 0.9944 | 0.9649 | 0.9583 |
| `duplicate_paragraph_ratio` | — | — | 0.0005 | **0.0058** |
| 平均耗时 (ms) | — | — | 70202 | 76590 |
| 平均重试次数 | — | — | 0.333 | 0.455 |

**观察**：

1. **引用可解析率随画像复杂度下降**（1.0 → 0.9978 → 0.9613 → 0.9537）。
   与「越复杂越好」的预期相反；
2. **最强画像的重复段落比例是 vector 画像的 11 倍**（0.0058 vs 0.0005）；
3. **耗时与重试次数最高**（76.6s / 0.455 次）；
4. 但**注意**：hybrid 的数字是在**排除了 4 个失败样本**之后算的 ——
   真实表现只会更差，不会更好。

### 6.6 显著性检验

`significance.csv` 里只有耗时和重试达到显著：

| 对比 | 指标 | p 值 |
| --- | --- | --- |
| multi_no_rag vs single | duration_ms | **0.0067** |
| multi_no_rag vs single | retries | **0.0339** |
| multi_vector vs single | duration_ms | **0.0103** |
| multi_vector vs single | retries | **0.0253** |
| multi_hybrid vs single | duration_ms | **0.0244** |

**引用质量类指标（`citation_resolvability` / `evidence_coverage`）均不显著**
（p = 0.18 / 0.32 / 0.066 / 0.5 / 0.125）。

**如实结论**：在 n=15 的样本量下，**没有统计证据表明多 Agent / RAG 提升了引用质量**；
能确认的只有「更慢、重试更多」。

### 6.7 验证限制

- **单次重复**（`--repetitions 1`）：无法区分模型随机性与画像差异。
  画像是 `seed: 20261017` 固定，但 LLM 采样仍有波动；
- **`recall_at_5` / `mrr` / `ndcg_at_5` / `total_tokens` / `error_citation_rate` 全为 null**
  —— 检索类指标没有计算，token 统计也未接上。
  **这意味着「RAG 是否提升了检索质量」这个问题本轮数据回答不了**；
- **4 个失败样本被排除**：hybrid 画像的所有指标都建立在「成功的 11 个」上，
  存在**幸存者偏差**；
- 新模型（`workbuddy-ai/deepseek-v4.1-flash`）与历史 `gpt-6-luna` 的结果**不可直接对比**。

### 6.8 产物位置

```
服务器：~/maa_eval_runs/rs-20261027T061627Z/
容器内原始：/app/evaluation/runs/rs-20261027T061627Z/   （无挂载，重建容器会丢）
本地：docs/evaluation-runs/report/（报告 CSV + 图表）
```

**注意**：产物写在容器内且**没有挂载卷**。若重建 backend 容器，容器内的会丢失 ——
已在不可达之前拷到服务器宿主机 `~/maa_eval_runs/`，以那份为准。

---

## 7. 顺带发现的第三个缺陷：live 执行器不记录核心指标（**未修**）

### 7.1 现象

60 个 run 的 `metrics.json` 里，这些字段**全部为 null**：

```
recall_at_5 / mrr / ndcg_at_5 / error_citation_rate / total_tokens
```

对应的 5 张图里 `retrieval.png` 是**空图**（四个画像 `n=0`），
`latency_tokens.png` 的 token 面板也是空的。

### 7.2 根因（已定位到代码行）

`evaluation/runner.py:444-457` 的 `TaskExecutionExecutor.execute()` 返回的 `record`
**只有** `markdown` / `evidence_count` / `status` / `attempts` / `events` /
`review` / `node_count` / `event_count` / `document_count`。

而 `evaluation/metrics.py::summarize_run` 要算检索指标需要
`retrieved_ids` + `relevant_ids`/`qrels`，要算 token 需要
`prompt_tokens`/`completion_tokens`，要算错误引用率需要
`cited_chunk_ids`/`allowed_chunk_ids` —— **这些字段 live 执行器一个都没填**。

只有 `FixedAdapterExecutor`（测试替身，`runner.py:361`）会填 `retrieved_ids`，
所以单测是绿的，真实跑就全是 null。**又一次「测试载荷与生产载荷不同形状」**
（AGENTS §5 #16 / #33）。

### 7.3 为什么没在本轮修

修它需要知道真实载荷里**检索结果和 token 到底放在哪个字段**，
而服务器当前不可达、拿不到真实 `review.json` / `subtask.output`。

AGENTS §5 #33 的教训就是「写提取逻辑前先打印真实载荷」——
盲写提取逻辑极可能又写出一个「单测绿、线上 null」的实现。
**因此本轮只定位、不猜实现。**

### 7.4 修复计划（服务器恢复后）

1. 取一个真实 run 的 `review.json` 与 `subtask.output`，打印载荷结构；
2. 在 `TaskExecutionExecutor` 里补齐：`retrieved_ids`（检索节点的结果）、
   `duration_ms`（任务耗时）、`prompt_tokens`/`completion_tokens`
   （从 `execution_result` 的用量字段取，参考 `sum_actual_tokens` 的既有取法）；
3. 用**真实载荷形状**做测试夹具（照抄，不自己造）；
4. 重跑 60 run，再出报告 —— 那时「RAG 是否提升检索质量」才有数据可答。

---

## 8. 本轮总结
