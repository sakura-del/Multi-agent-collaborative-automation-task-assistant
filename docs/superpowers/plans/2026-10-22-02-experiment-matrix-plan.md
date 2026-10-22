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

## 3.5 实测进展（2026-10-22）

### 已跑通

`pilot-8`（1 主题 × 4 画像 × 2 重复 = 8 runs）在服务器稳定推进，
**每 run 约 7 分钟**。四组画像已开始产出差异数据。

### 跑通过程中抓到三个真实缺陷（全部已修复）

**缺陷 1：任务永远卡在 `waiting_approval`（`9dd5ea2`）**

`TaskExecutionExecutor` 首版：

```python
snapshot = await execution.get_snapshot(...)   # submit 后是 planning
while snapshot.status == WAITING_APPROVAL:     # 条件不成立 → 从不审批
    await execution.approve_plan(...)
    break
while ...:                                     # 只等终态，没人审批就永远等
```

规划是**异步**的，submit 返回时状态是 `planning`。首版只在「第一次取快照时
恰好已是 waiting_approval」才审批 —— 实际几乎不可能命中。
实测任务创建后卡 8 分钟不动。

修法：审批并入主轮询循环，见到 `waiting_approval` 就批、**只批一次**
（重复审批会因 revision 过期被拒）。

**缺陷 2：画像开关没传给请求（`9dd5ea2`）**

`TaskExecutionExecutor` 构造 `LiteratureReviewRequest` 时没传三个开关，
四组实验会走同一条路径 —— 消融失效。已从 `spec.profile` 透传。

**缺陷 3：no_rag 画像永远跑不出结果（`95767be`）**

`retrieval_mode=none` 时 evidence 为空，但提示词要求「必须用 [E1] 标记引用
证据」—— **要求自相矛盾**。模型按训练习惯输出 `[E1]`，`_resolve_citations`
判为越界 → 重试 3 次 → 转 `waiting_human`。实测 pilot 的 2 个
`single_agent_no_rag` run 全部卡在这里。

修法两层：

1. 提示词（`literature_executor_system.j2`）：无证据时明确「不要使用任何
   引用标记、不要编造出处、全部标注模型推断」；
2. 校验（`_resolve_citations`）：`evidence_count == 0` 时**不判越界**
   （没有范围可越），但记 `citation_without_evidence` warning 留痕。

**为什么不删掉标记**：篡改模型输出会让实验数据不可信。保留原文 + warning
更诚实（这也是「模型在无证据时仍会幻觉引用」的直接证据）。

**有证据时保持严格**：越界仍抛异常（模型编造不存在的文献必须拒绝）。

### 单 run 耗时与总时长推算

| 项 | 实测值 |
| --- | --- |
| 单 run 耗时 | **约 7 分钟**（9 个节点 + 复盘） |
| pilot 8 runs | 约 55 分钟 |
| **全量 120 runs** | **约 14 小时** |

⚠️ 全量必须**分段跑**（`--retry-failed` 支持续跑），不能指望一次跑完。

### 另一个观察：LLM 超时重试频繁

日志里 `llm_chat_retry attempt=1/2 error='Request timed out.'` 出现较多。
原因：`gpt-6-luna` 在服务器负载下响应慢（`request_timeout` 默认 30s）。
这会让单 run 耗时波动，但**不影响结果正确性**（重试后成功）。

若要压缩时长，可调大 `LLM__REQUEST_TIMEOUT`。当前不动：实验参数应保持
与生产一致，否则数据不可比。

---

## 3.6 第二轮实测：检索索引缺失（**最隐蔽的缺陷**）

### 现象

pilot 8/8 全部 completed（DeepSeek 提速后 8 分钟跑完），四组画像配置正确分流，
但**正文里一个 `[E` 引用标记都没有**，且 `hybrid_rerank`（最强检索画像）的正文
写着：

> 本次子任务未获得任何检索证据，以下全部内容均为模型推断

### 诊断链

1. 日志：`hybrid_search_done bm25_hits=0 fused=0 vector_hits=0` —— 检索零命中；
2. 库里：demo 用户 11 篇文档、2835 个分块、**全部 `index_status=ready`**；
3. Chroma 集合：只有 **4 条**记录（其他用户的），demo 的 2835 个分块不在里面。

### 根因

`init_kb()` 只建**向量库客户端**，不灌数据。BM25 索引是**进程内存**结构，
必须显式 `rebuild_bm25()` 才能从持久化分块恢复。

应用侧 lifespan 会做（`app/main.py:67`），但 `scripts/run_experiments.py`
只调了 `init_kb()` → 进程内 BM25 索引为空 → 检索返回空列表。

### 为什么这个缺陷最危险

**不报错**。检索返回空列表，工作流把「没有证据」当正常情况继续跑，
最终正文写着「未获得任何检索证据」—— **看起来像模型能力问题，
实际是索引没建**。

若不做这轮实测，四组画像的「检索差异」全是假的：有检索的两组和无检索的
两组产出完全一样（都是 0 引用），消融结论会写成「检索无效果」。

### 修法（`ae1683b`）

`init_kb()` 之后调 `rebuild_bm25()`（失败不阻塞，与 lifespan 同策略）。

### 验证

修复后同一查询：

```
bm25_rebuilt_from_db  chunks=28344
hybrid_search_done    bm25_hits=30 fused=15 vector_hits=0
kb_query_done         results=5
```

pilot 重跑结果（**引用标记差异清晰出现**）：

| 画像 | retrieval | 引用标记数（rep-1 / rep-2） |
| --- | --- | --- |
| `multi_agent_hybrid_rerank` | hybrid_rerank | **165 / 122** |
| `multi_agent_vector_rag` | vector | **134 / 107** |
| `multi_agent_no_rag` | none | **0 / 0** |
| `single_agent_no_rag` | none | **0 / 0** |

有检索的两组产出 100+ 引用标记，无检索的两组为 0 —— **这正是消融实验需要的信号**。

### 遗留：Chroma 向量侧数据缺失

`vector_hits=0` 是历史数据问题（Chroma 集合只有 4 条，SQLite 有 28344 个分块）。
当前靠 **BM25 单路**支撑检索，`hybrid_rerank` 实际退化为「BM25 + 重排」。

**影响评估**：不影响「有检索 vs 无检索」的消融对比（这是实验的核心），
但「vector vs hybrid」的差异会失真。已在报告里如实标注。

**修复路径**（不在本计划范围）：重新向量化 104 篇文档（`index_status` 置
`pending` 后重新入库），预计 30-60 分钟。需要单独评估是否值得在跑完实验后做。

### 时间账

| 阶段 | 实测耗时 |
| --- | --- |
| 单 run（DeepSeek） | **约 1 分钟**（8 runs / 8 分钟） |
| 全量 60 runs | 预计 **1-2 小时** |

对比：gpt-6-luna 单 run 20-40 分钟，60 runs 需 20-40 小时。
**换 DeepSeek 是让这个实验可行的关键决定**（685 tokens 长输出仅 6.4s，
gpt-6-luna 同量需 120-380s）。

⚠️ 报告里必须标注：实验用 `deepseek-chat`，与生产默认 `gpt-6-luna` 不同。
这是基础设施参数（不是实验变量），四组画像之间仍然可比。

---

## 4. Exit Gate

- [ ] 三个开关有默认值，既有全量测试绿
- [ ] 三种 `retrieval_mode` 产出**可测量的差异**（有测试）
- [ ] `validation_enabled=false` / `review_enabled=false` 确实**不调 LLM**（spy 断言 + 回退验证）
- [ ] CLI `--adapter live` 可跑通（缺依赖时报可读错误）
- [ ] 小样 8 runs 四组结果有差异（附实测数据）
- [ ] 单 run 耗时实测，120 runs 时长有据可依
- [ ] 全量 120 runs 完成 + 报告产出
