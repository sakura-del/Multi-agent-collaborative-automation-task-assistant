# 实验矩阵实测记录（2026-10-22）

> 计划：[2026-10-22-02-experiment-matrix-plan.md](../superpowers/plans/2026-10-22-02-experiment-matrix-plan.md)
> 产物：[full-60/](full-60/)（report.md + 3 CSV + 5 图表说明）
> **状态：部分完成（29/60），阻塞原因已定位**

---

## 1. 结论先说

| 项 | 结果 |
| --- | --- |
| **可运行化** | ✅ **完成**（此前 `--adapter live` 是未实现占位，120-run 矩阵根本跑不了） |
| **画像开关** | ✅ **完成**（四个画像产出可测量差异，见 §4） |
| **实跑数据** | ⚠️ **29/60**（A 组 15 + B 组 14），C/D 两组 0 |
| **阻塞原因** | **DeepSeek API 余额耗尽**（-0.08 元），非代码缺陷 |
| **补齐方式** | 充值后 `--retry-failed` 续跑，约 40 分钟 |

---

## 2. 跑通过程中修复的 5 个真实缺陷

这些都是**不做实测就发现不了**的问题，且前 4 个都会导致「实验跑完但结论是假的」。

### 2.1 任务永远卡在 `waiting_approval`（`9dd5ea2`）

```python
snapshot = await execution.get_snapshot(...)   # submit 后是 planning
while snapshot.status == WAITING_APPROVAL:     # 条件不成立 → 从不审批
    await execution.approve_plan(...)
    break
```

规划是异步的，`submit` 返回时状态是 `planning`。首版只在「第一次取快照时
恰好已是 waiting_approval」才审批 —— 实际几乎不可能命中。实测卡 8 分钟。

### 2.2 画像开关没传给请求（`9dd5ea2`）

`TaskExecutionExecutor` 构造请求时没传三个开关，四组实验走同一条路径 ——
**消融实验不成立**。

### 2.3 no_rag 画像永远跑不出结果（`95767be`）

`retrieval_mode=none` 时 evidence 为空，但提示词要求「必须用 [E1] 标记引用证据」
—— **要求自相矛盾**。模型按训练习惯输出 `[E1]` → 判为越界 → 重试 3 次 → 转人工。

修法两层：提示词明确「无证据不引用」+ 校验在 `evidence_count == 0` 时不判越界
（但记 `citation_without_evidence` warning 留痕）。

### 2.4 知识库未初始化（`3328b5c`）

`run_experiments.py` 装配了 `KnowledgeSearchProvider` 但没调 `init_kb()`。
`no_rag` 两组不检索所以能过，**恰好只有检索两组失败** —— 看起来像「画像差异」，
极易被当成实验结论写进论文。

### 2.5 BM25 索引未重建（`ae1683b`，**最隐蔽**）

`init_kb()` 只建向量库客户端，不灌数据。BM25 是**进程内存**结构，必须显式
`rebuild_bm25()`。应用侧 lifespan 会做，独立脚本漏了。

**症状**：不报错。检索返回空列表，工作流把「没有证据」当正常情况，
正文写着「本次子任务未获得任何检索证据」—— **看起来像模型能力问题，
实际是索引没建**。

修复后同一查询：`bm25_hits=30 fused=15 results=5`。

---

## 3. 当前数据的可信边界

### 3.1 有效的部分

| 画像 | 完成数 | success | 平均耗时 | 重复段落比 |
| --- | --- | --- | --- | --- |
| A: 单 Agent 无 RAG | 15/15 | 1.000 | 39.0s | 0.000 |
| B: 多 Agent 无 RAG | 14/15 | 1.000 | 45.4s | 0.003 |
| C: 多 Agent + 向量 RAG | 0/15 | — | — | — |
| D: 多 Agent + 混合 RAG | 0/15 | — | — | — |

**A vs B 的对比是有效的**（同为无检索，差异来自 Agent 数量）：
- 成功率都 1.000；
- B 平均耗时高 16%（45.4s vs 39.0s）—— 多 Agent 的编排开销；
- B 的重试均值 0.214（A 为 0）—— 多 Agent 有更多节点，重试机会更多。

### 3.2 **不可用于论文结论的部分**

⚠️ **C/D 两组零数据，不能写「检索无效果」或「检索有提升」**。
必须先补齐再下结论。

### 3.3 已知的数据质量问题

| 问题 | 影响 | 状态 |
| --- | --- | --- |
| Chroma 集合只有 4 条记录（SQLite 有 28344 个分块） | `vector_hits=0`，检索靠 BM25 单路 | 未修复；不影响「有/无检索」对比，但「vector vs hybrid」差异会失真 |
| 实验用 `deepseek-chat`（生产默认 `gpt-6-luna`） | 绝对值不可与生产对比 | 已记录；四组之间仍可比（同一模型） |
| 单次重复（`--repetitions 1`） | 无组内方差，显著性检验样本不足 | 已记录；pilot 曾用 2 次重复验证过稳定性 |

---

## 4. 画像差异已验证（pilot-8，AI 领域单主题 × 2 重复）

这是「画像开关真的生效」的**直接证据**（引用标记数）：

| 画像 | retrieval | 引用标记数（rep-1 / rep-2） |
| --- | --- | --- |
| `multi_agent_hybrid_rerank` | hybrid_rerank | **165 / 122** |
| `multi_agent_vector_rag` | vector | **134 / 107** |
| `multi_agent_no_rag` | none | **0 / 0** |
| `single_agent_no_rag` | none | **0 / 0** |

有检索的两组产出 100+ 引用标记，无检索的两组为 0 —— **这正是消融实验需要的信号**。

---

## 5. 阻塞详情（余额）

```
DeepSeek /user/balance:
{"is_available": false,
 "balance_infos": [{"currency": "CNY", "total_balance": "-0.08", ...}]}
```

- 从第 30 个 run 起出现 402 `Insufficient Balance`；
- 日志中该错误出现 **452 次**；
- 前 29 个 run 正常完成。

**为什么选 DeepSeek**：gpt-6-luna 单 run 需 20-40 分钟（节点耗时 120-380s），
60 runs 需 20-40 小时；DeepSeek 单 run 约 1 分钟，60 runs 约 1 小时。
实测 685 tokens 长输出：DeepSeek 6.4s vs gpt-6-luna 120-380s（**20-50 倍**）。

---

## 6. 补齐步骤（充值后）

```bash
cd ~/projects/multi-agent-assistant

# 1) 充值后确认余额
docker compose exec -T backend python -c "..."

# 2) 续跑失败的 run（--retry-failed 只跑未完成的）
docker compose exec -T backend sh -c \
  'python -u -m scripts.run_experiments --profiles all --repetitions 1 \
   --run-set-id full-60 --retry-failed > /tmp/full60_retry.log 2>&1'

# 3) 重新生成报告
docker compose exec -T backend python -m evaluation.cli report --run-set full-60

# 4) 取回
docker compose cp backend:/app/evaluation/runs/full-60/report/. /tmp/report_full60/
```

预计耗时：31 个 run × 约 1.2 分钟 ≈ **40 分钟**。

---

## 7. 交付物清单

| 文件 | 说明 |
| --- | --- |
| `full-60/report.md` | 自动生成报告（含图表说明与显著性检验） |
| `full-60/summary.csv` | 逐 run 明细 |
| `full-60/aggregate.csv` | 按画像聚合（均值/中位数/95% CI） |
| `full-60/significance.csv` | 配对 Wilcoxon 检验结果 |
| `full-60/*.md` | 5 张图表的说明（图本身是 PNG，在服务器上） |

**图 PNG 未取回**：`figures/*.png` 在服务器容器内，本地只有说明 markdown。
需要时用 `docker compose cp` 单独取。
