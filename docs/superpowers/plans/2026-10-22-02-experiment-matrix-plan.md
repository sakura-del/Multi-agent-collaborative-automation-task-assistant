# 实验矩阵可运行化（120-run）实现计划

> 创建：2026-10-22 ｜ 状态：**待执行**
> 上游：[产品路线图 §6.2 论文相关](../../product/2026-10-18-demo-to-product-roadmap.md)
> 背景：Phase 2 五项已全部交付；本项是毕业设计的**硬指标**

---

## 0. 现状盘点（已核实）

### 已有什么

| 组件 | 状态 |
| --- | --- |
| 实验矩阵定义 | ✅ `validate` 输出 **4 画像 × 15 主题 × 2 重复 = 120 runs** |
| 冻结参数 | ✅ provider/model/temperature/seed/prompt_version/corpus_version 全部冻结并互相校验 |
| 语料 | ✅ 三个领域各 10 篇（`evaluation/datasets/corpora/*.json`） |
| 指标与报告 | ✅ `evaluation/metrics.py` + `reporting.py`（CSV + 图 + markdown） |
| 断点续跑 | ✅ `run_is_complete` / `--retry-failed` |
| 真实执行器 | ✅ `TaskExecutionExecutor`（runner.py:376，已实现） |
| 离线执行器 | ✅ `FixedAdapterExecutor`（CI 用） |

### 两个阻塞（**必须先解决**）

**阻塞 1：CLI 的 `--adapter live` 是未实现的占位**

```python
def _build_executor(adapter: str, args):
    if adapter == "fixed":
        return FixedAdapterExecutor()
    if adapter != "live":
        raise SystemExit(...)
    raise SystemExit("live 适配器需要在实验环境中装配 TaskExecution ...")
```

`TaskExecutionExecutor` 已经写好，但 CLI 没有装配它（缺数据库、语料、模型客户端、
演示用户）。

**阻塞 2（更严重）：四个画像跑不出差异**

`LiteratureReviewRequest` 只有 `topic` / `keywords` / `max_documents` 等字段，
**没有** `retrieval_mode` / `validation_enabled` / `review_enabled`；
`LiteratureReviewWorkflow` 也不读这些开关。

后果：`multi_agent_no_rag` 与 `multi_agent_hybrid_rerank` 会走**同一条**工作流，
产出的差异只来自模型采样噪声。实验章节无法写「消融」—— 结论不成立。

**这是本计划的核心任务。**

---

## 1. 设计决策：开关放在哪一层

### 决策：`LiteratureReviewRequest` 加三个字段，由**工作流**消费

```python
class LiteratureReviewRequest(BaseModel):
    ...
    retrieval_mode: Literal["none", "vector", "hybrid_rerank"] = "hybrid_rerank"
    validation_enabled: bool = True
    review_enabled: bool = True
```

### 为什么放请求契约而不是全局配置

| 方案 | 问题 |
| --- | --- |
| 全局配置文件 | 120 次运行要改 120 次配置重启进程；且并发跑不同画像会互相污染 |
| 环境变量 | 同上，且不可追溯（`config.json` 存不下来） |
| **请求契约**（选定） | 每个 run 的 `config.json` 天然记录了开关；可并发；可复现 |

**默认值必须是「全开」**（`hybrid_rerank` + 校验 + 复盘）：
网页用户不传这些字段时得到完整能力，与现状一致（**不改变既有行为**）。

### 三个开关的语义

| 开关 | `none` | `vector` | `hybrid_rerank` |
| --- | --- | --- | --- |
| `retrieval_mode` | 不检索，用冻结清单 | 向量检索 | 向量 + 关键词混合 + 重排 |

| 开关 | 关闭时 | 开启时 |
| --- | --- | --- |
| `validation_enabled` | 跳过 `validate` 节点 | 执行引用校验与完整性检查 |
| `review_enabled` | 跳过 `review` 节点 | 执行复盘并生成最终正文 |

**关键约束**：`validate` / `review` 节点在 `REQUIRED_NODE_TYPES` 里，规划器必须
仍然生成它们（契约校验依赖）。关闭 = **执行时跳过**，不是「不规划」。
这样 DAG 结构一致，只改变执行行为 —— 实验对比才干净。

---

## 2. 实现任务

### Task 1：契约扩展（`LiteratureReviewRequest` + 三开关）

- 加三个字段（默认全开）
- `to_contract()` 透传（`app/schemas/task.py`）
- 测试：默认值、非法值拒绝、既有 400+ 测试不受影响

### Task 2：工作流消费开关

- `LiteratureReviewWorkflow` 读 `task.retrieval_mode` / `validation_enabled` / `review_enabled`
- `retrieval_mode=none` → 不调用 `knowledge_query`，evidence 为空
- `validation_enabled=false` → `validate` 节点直接返回通过（不调 LLM）
- `review_enabled=false` → `review` 节点用 `write` 节点输出作最终正文（不调 LLM）
- 测试：三种 retrieval_mode 的 evidence 数量差异；两个 enabled=false 不调 LLM（用 spy 断言）

### Task 3：CLI 装配 live 适配器

- `_build_executor("live", args)` 装配：
  - `AsyncSessionLocal`（真实数据库）
  - `RuntimeContainer`（真实 LLM，从 `get_settings()`）
  - `TaskExecution`
  - 演示用户（按 username 查，不存在则报错并给创建指引）
- 测试：装配函数可注入假依赖；缺用户/缺语料时给出可读错误

### Task 4：小样验证（8 runs）

- 服务器跑：1 主题 × 4 画像 × 2 重复 = 8 runs
- 验证：四组结果**有差异**（evidence 数量、token 用量、节点耗时）
- 记录实际单 run 耗时 → 推算 120 runs 总时长

### Task 5：全量 120 runs + 报告

- 服务器后台跑（预计数小时）
- `report` 生成 CSV + 图 + markdown
- 结果写入 `docs/experiments/`

---

## 3. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 改契约破坏既有测试 | 三个字段全部有默认值（全开），既有调用方无需改动；跑全量门禁 |
| 关掉 validate/review 后 DAG 契约校验失败 | **规划仍生成**这些节点，只在执行期跳过 |
| `retrieval_mode=none` 时 evidence 为空导致 write 节点失败 | write 节点在无证据时降级为「基于主题常识」模式（已有逻辑，需确认） |
| 120 runs 跑一半服务器 OOM | `max_concurrency` 保持 1（实验必须串行，否则互相干扰指标） |
| 模型限流 | 已配 `fallback_chain`（DeepSeek 保底）；单 run 失败可 `--retry-failed` |
| 耗时过长 | 先小样 8 runs 实测单 run 耗时再决定 |

---

## 4. Exit Gate

- [ ] 三个开关有默认值，既有全量测试绿
- [ ] 三种 `retrieval_mode` 产出**可测量的差异**（有测试）
- [ ] `validation_enabled=false` / `review_enabled=false` 确实**不调 LLM**（spy 断言 + 回退验证）
- [ ] CLI `--adapter live` 可跑通（缺依赖时报可读错误）
- [ ] 小样 8 runs 四组结果有差异（附实测数据）
- [ ] 单 run 耗时实测，120 runs 时长有据可依
- [ ] 全量 120 runs 完成 + 报告产出
