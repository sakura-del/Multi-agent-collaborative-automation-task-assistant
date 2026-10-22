# 实验结果报告（run-set `full-60`）

- 运行总数：**60**
- 完成：**29**　失败：**0**　不完整：**0**
- 指标口径：确定性指标与人工指标为主；LLM Judge 仅作补充（见文末单独章节）。

## 1. 汇总表

逐次运行明细：[`summary.csv`](summary.csv)；分组聚合（含 95% bootstrap 置信区间）：[`aggregate.csv`](aggregate.csv)；显著性检验（vs baseline A）：[`significance.csv`](significance.csv)。

| 画像 | 指标 | n | 均值 | 中位数 | 95% CI |
| --- | --- | --- | --- | --- | --- |
| A: 单 Agent 无 RAG | citation_resolvability | 1 | 0.000 | 0.000 | — |
| A: 单 Agent 无 RAG | duplicate_paragraph_ratio | 15 | 0.000 | 0.000 | [0.000, 0.001] |
| A: 单 Agent 无 RAG | success | 15 | 1.000 | 1.000 | [1.000, 1.000] |
| A: 单 Agent 无 RAG | duration_ms | 15 | 38981.981 | 39175.086 | [33970.101, 43798.037] |
| A: 单 Agent 无 RAG | retries | 15 | 0.000 | 0.000 | [0.000, 0.000] |
| B: 多 Agent 无 RAG | duplicate_paragraph_ratio | 14 | 0.003 | 0.000 | [0.000, 0.008] |
| B: 多 Agent 无 RAG | success | 14 | 1.000 | 1.000 | [1.000, 1.000] |
| B: 多 Agent 无 RAG | duration_ms | 14 | 45416.118 | 43687.688 | [38526.648, 53379.107] |
| B: 多 Agent 无 RAG | retries | 14 | 0.214 | 0.000 | [0.000, 0.571] |

## 2. 图表

每张图都带标题、样本量与置信区间误差线。

### 检索质量（Recall@5 / nDCG@5）

![检索质量（Recall@5 / nDCG@5）](figures/retrieval.png)

指标：recall_at_5, ndcg_at_5。数据来源：`aggregate.csv`（run-set `full-60`）。

### 内容质量（证据覆盖 / 重复段落比）

![内容质量（证据覆盖 / 重复段落比）](figures/content_quality.png)

指标：evidence_coverage, duplicate_paragraph_ratio。数据来源：`aggregate.csv`（run-set `full-60`）。

### 引用可靠性（可解析率 / 错误引用率）

![引用可靠性（可解析率 / 错误引用率）](figures/citations.png)

指标：citation_resolvability, error_citation_rate。数据来源：`aggregate.csv`（run-set `full-60`）。

### 执行成本（平均耗时 / 总 token）

![执行成本（平均耗时 / 总 token）](figures/latency_tokens.png)

指标：duration_ms, total_tokens。数据来源：`aggregate.csv`（run-set `full-60`）。

### 鲁棒性（成功率 / 恢复成功率 / 重试次数）

![鲁棒性（成功率 / 恢复成功率 / 重试次数）](figures/recovery.png)

指标：success, recovery_success, retries。数据来源：`aggregate.csv`（run-set `full-60`）。

## 3. 显著性检验

配对 Wilcoxon 秩和检验，配对键为 (课题, 重复次数)；样本不足或差值全零时记为 —，不编造 p 值。

| 画像 | 指标 | 配对数 | 统计量 | p 值 |
| --- | --- | --- | --- | --- |
| B: 多 Agent 无 RAG | duplicate_paragraph_ratio | 14 | 0.000 | 0.1797 |
| B: 多 Agent 无 RAG | duration_ms | 14 | 32.000 | 0.2166 |
| B: 多 Agent 无 RAG | retries | 14 | 0.000 | 0.1797 |

## 4. 缺失与失败运行

无失败或不完整运行。

## 5. LLM Judge（补充指标，不并入主质量分）

LLM Judge 得分仅用于**补充**观察，需在论文中标注所用模型、版本与 Prompt 版本，且不得与确定性/人工指标平均成单一主质量分。本节数据需单独运行 Judge 脚本后填入。

| 项目 | 值 |
| --- | --- |
| 模型 / 版本 | 待填写 |
| Prompt 版本 | 待填写 |
| 说明 | 仅作补充，不参与主指标计算 |

## 6. 复现方式

```bash
cd backend
python -m evaluation.cli validate
python -m evaluation.cli run --profiles all --repetitions 2
python -m evaluation.cli report --run-set <run-set-id>
```

本报告由 `evaluation/reporting.py` 从 run-set `full-60` 自动生成，bootstrap 与检验均使用固定 seed，同输入两次生成结果一致。
