# 实验复现手册（Plan 5 Task 7）

> 面向：论文实验复现、答辩现场重跑、答辩前数据核对。
> 目标：任何人拿到仓库 + 模型密钥，能按本文档复现 **4 组 × 15 课题 × 2 次 = 120 runs** 与全部图表。

## 0. 前置条件

| 项 | 要求 |
| --- | --- |
| Python | 3.11 |
| 依赖 | `cd backend && pip install -e ".[dev,evaluation]"` |
| 模型密钥 | `LLM__OPENAI_API_KEY`（写入 `.env`，**不要提交**） |
| 模型 | 固定 `gpt-6-luna`（`config/default.yaml` 已锁定） |
| 网络 | 只有"准备语料"一步需要；其余全部离线 |
| 磁盘 | 语料 PDF 约 55 MB，runs 产物约 120 × 小文件 |

## 1. 校验实验输入（必做第一步）

```bash
cd backend
python -m evaluation.cli validate
```

期望输出包含：

```json
{
  "profiles": ["single_agent_no_rag", "multi_agent_no_rag",
               "multi_agent_vector_rag", "multi_agent_hybrid_rerank"],
  "topics": 15,
  "domains": {"ai": 5, "software_engineering": 5, "education_technology": 5},
  "corpora": { "evaluation/datasets/corpora/ai.json": 10, ... },
  "runs": 120
}
```

- `runs` 必须是 **120**；不是则说明改动过课题数或重复次数，需先确认是否允许。
- 若报"语料清单不存在"，执行下一步（唯一需要网络的步骤）。

## 2. 准备离线语料（一次性，需要网络）

```bash
cd backend
python -m evaluation.prepare_corpora          # 三个领域各 10 篇 PDF
```

- 产物：`evaluation/datasets/corpora/<domain>.json`（**入仓库**）+ `<domain>-files/`（PDF，gitignore）
- 每个领域固定 10 篇，少于 10 篇会失败且**不写入任何文件**（原子替换）
- 清单含 `sha256`；校验时对比记录数与 `corpus_version` 声明

## 3. 离线演练（不消耗模型额度）

```bash
cd backend
python -m evaluation.cli run --adapter fixed --profiles all --repetitions 1
```

- 用确定性适配器（与真实模型走同一 Agent 调用路径）跑完全部链路
- 期望：`ok: true`，`completed == total`
- 用途：验证 runner/指标/报告链路本身正确，答案不进入论文数据

## 4. 真实实验（120 runs）

```bash
cd backend
# 全量：4 组 × 15 课题 × 2 次
python -m evaluation.cli run --profiles all --repetitions 2

# 或先跑单组冒烟，确认模型/语料就绪
python -m evaluation.cli run --profiles full --topic ai-rag-eval --repetitions 1
```

**resume 语义**：直接重复执行同一条命令即可断点续跑——
已完成（六个产物齐全且 `metrics.json` 可解析）的 run 会被跳过，
失败 run 保留现场不覆盖。

**失败重跑**：显式加 `--retry-failed`，旧现场会归档到 `attempts/retry-*/`。

产物位置：`backend/evaluation/runs/<run-set-id>/<profile>/<topic>/rep-N/`：

```
config.json    该组画像（含固定模型/温度/种子）
task.json      课题与 run 标识
events.jsonl   事件流（无事件时写占位事件，保证非空）
result.md      综述正文
review.json    复盘报告
metrics.json   本次运行的指标
```

## 5. 生成论文报告

```bash
cd backend
python -m evaluation.cli report --run-set latest
# 或指定 run-set
python -m evaluation.cli report --run-set rs-20261210T030000Z
```

产物（默认写到 `<run-set>/report/`）：

| 文件 | 内容 |
| --- | --- |
| `summary.csv` | 每次运行一行（含 failed / incomplete 状态） |
| `aggregate.csv` | 每组每指标：n / mean / median / 95% bootstrap CI |
| `significance.csv` | 各组 vs baseline A 的配对 Wilcoxon（样本不足记 `—`，不编造 p 值） |
| `figures/retrieval.png` | 检索质量（Recall@5 / nDCG@5） |
| `figures/content_quality.png` | 内容质量（证据覆盖 / 重复段落比） |
| `figures/citations.png` | 引用可靠性（可解析率 / 错误引用率） |
| `figures/latency_tokens.png` | 成本（耗时 / token） |
| `figures/recovery.png` | 鲁棒性（成功率 / 恢复率 / 重试） |
| `report.md` | 全部表与图 + 缺失运行清单 + LLM Judge 补充章节 |

报告性质：
- **固定 seed**（默认 `20261017`），同输入两次生成结果逐字节一致；
- 失败/不完整运行**列在报告第 4 节**，不参与聚合也不被丢弃；
- LLM Judge 只在第 5 节出现，**不并入主质量分**。

## 6. 复现性核对清单

- [ ] `validate` 的 `runs` == 120
- [ ] 四组画像的 `model/temperature/prompt_version/corpus_version/seed` 与 `evaluation/configs/*.yaml` 一致
- [ ] `aggregate.csv` 每组各指标 `n` == 30（15 课题 × 2 次）
- [ ] `report.md` 第 4 节为空或无失败运行
- [ ] 两次 `report` 生成的 `summary.csv`/`aggregate.csv`/`significance.csv` 完全一致
- [ ] 图表中每个柱子都标注了样本量

## 7. 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| `validate` 报语料清单不存在 | 未跑第 2 步；或换了机器（PDF 不入库，清单入库） |
| run 全部 `failed` 且报 401 | `.env` 里 `LLM__OPENAI_API_KEY` 无效或过期 |
| `significance.csv` 全是 `—` | 有效配对数 < 2（运行次数太少）或两组差值全为 0 |
| 聚合 `mean` 是空 | 该指标在**所有**运行里都没有数据（不是 0，是"未测量"） |
| 图表中文变方框 | 容器缺 CJK 字体；图内文字已改用英文标签规避 |
| resume 时 run 未被跳过 | 该 run 缺文件或 `metrics.json` 不可解析 → 视为未完成 |

## 8. 与论文的对应关系

| 论文章节 | 数据来源 |
| --- | --- |
| 第 5 章 实验设置 | `evaluation/configs/*.yaml` + 本文档第 1–2 步输出 |
| 第 5 章 检索消融 | `figures/retrieval.png` + `aggregate.csv`（recall_at_5 / ndcg_at_5） |
| 第 5 章 引用可靠性 | `figures/citations.png` + `significance.csv` |
| 第 5 章 内容质量 | `figures/content_quality.png` |
| 第 5 章 成本 | `figures/latency_tokens.png` |
| 第 5 章 鲁棒性 | `figures/recovery.png` |
| 第 6 章 局限性 | `report.md` 第 4 节（失败运行）+ reports 的 LLM Judge 章节 |
