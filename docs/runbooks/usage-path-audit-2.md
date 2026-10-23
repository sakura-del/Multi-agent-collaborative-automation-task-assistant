# 真实使用路径巡检 · 第二轮（2026-10-23）

> 上一轮：[usage-path-audit.md](usage-path-audit.md)（知识库 / 模板 / 工作区权限，54 项）
> 本轮覆盖剩余三条链路：**CSL 引用样式、批注段落锚点、开放 API + MCP**

---

## 结论先说

| 链路 | 检查项 | 结果 | 抓到缺陷 |
| --- | --- | --- | --- |
| CSL 自定义引用样式 | 28 + 8 | 全通过 | **2 个（已修）** |
| 批注段落指纹锚点 | 15 + 12 | 全通过 | 0 |
| 开放 API + MCP | 27 + 8 | 全通过 | **1 个（已修）** |
| **合计** | **98** | **全通过** | **3** |

三个缺陷都在 `1dcbb44` 修复，共同点是：**单测全绿，真实路径断裂**。

---

## 链路 4：CSL 自定义引用样式

`citation_style` 表上线后一直是 **0 行** —— M2 交付后从未被真实使用。

### 通过的检查（28 项）

| 检查 | 结果 |
| --- | --- |
| 上传合法 .csl → 201，`style_id` 由 `<info><id>` 规范化派生 | ✅ |
| 列表 = 预置（前）+ 自定义，`source` / `is_default` 正确 | ✅ |
| **同内容重复上传幂等**（同 style_id，不新增行） | ✅ |
| **同 `<id>` 不同内容 → 分配 `-2` 后缀**（不覆盖） | ✅ |
| 非 XML / 空文件 / 超 256KB → 400，带逐条 reasons | ✅ |
| 跨用户看不到、跨用户删除 404 | ✅ |
| 预置样式删除 → 400（不可删） | ✅ |
| 删除 204，重复删除 404 | ✅ |

### 缺陷 A：数字制的正文标记硬编码 `[n]`，自定义样式对正文无效

`renderer.render()` 里：

```python
if meta.numbering == "citation-number":
    marker = f"[{number}]"      # ← 完全绕过 CSL <citation>
```

后果：用户上传标记为 `〔1〕` 或 `(1)` 的样式后：

- **参考文献列表**按样式渲染（走引擎，生效）；
- **正文** `[[cite:x]]` 替换后永远是 `[1]`（不生效）。

即「样式只对列表生效、对正文不生效」。用户看正文最多，不一致非常明显。

**为什么测试没抓到**：GB/T 7714 顺序编码制**正好就是 `[1]`** —— 预置样式的
期望值与硬编码值相同，全部测试绿。只有真实上传的自定义样式才会暴露，
而 `citation_style` 表一直是 0 行。

**修法**：优先用 `_render_item` 已经算出的 `engine_marker`（citeproc 按样式产出），
为空才回退 `[n]`。

### 缺陷 B：多条文献的 marker 全部串成 `〔1〕`

`_render_item` 每次只把「当前条目」喂给 citeproc，它永远是第 1 个引用 →
`citation-number` 恒为 1。

实测：两条文献的 marker 都是 `〔1〕`。这是**串号**，导师会看到两条不同文献
指向同一个编号。

**修法**：传 `preceding`（前序条目）一起注册，序号才与全局一致。

### 渲染期解析验证（8 项）

HTTP 巡检只能证明「列表里有」。真正的问题是**上传的样式能不能被渲染器用**。
在容器内复现 `main.py::_restore_custom_styles` 的装配后验证：

| 检查 | 结果 |
| --- | --- |
| `_restore_custom_styles` 能把库里的样式恢复进注册表 | ✅ |
| `registry.resolve(style_id)` 成功（**重启后仍可用**） | ✅ |
| `registry.meta()` 读到正确 title | ✅ |
| `CitationRenderer` 渲染产出含自定义 MARKER | ✅ |
| 与预置样式产出**不同** | ✅ |
| 内联 `[[cite:x]]` 按自定义样式渲染 | ✅ |

---

## 链路 5：批注段落指纹锚点

`task_annotation` 表上线后**从未被真实使用**，而 `matched=False` 分支
（原文改写后锚点失配）**从未被验证过**。

### 为什么必须验证 `matched=False`

`matched=True` 跑通只证明「没改文字时能匹配」。真正危险的是原文被改写后：

- 若实现按「第 N 段」而不是「内容相同」匹配，导师意见会出现在**完全无关的段落**上；
- **比不显示更糟** —— 学生会对着错误段落改。

### 通过的检查

| 阶段 | 检查 | 结果 |
| --- | --- | --- |
| 基线 | 段落批注创建 201，服务端算指纹（16 位 hex） | ✅ |
| 基线 | `matched=True`，`paragraph_index` 正确 | ✅ |
| 空白差异 | 锚点前后加 `\n\n` 与空格 → 仍 `matched=True`（指纹先 strip） | ✅ |
| 任务级批注 | 不出现在 `paragraph-matches` 里 | ✅ |
| 跨用户 | 列批注 / 看匹配 / 建批注 全 404 | ✅ |
| **V2b 改写锚点所在段** | **`matched=False`** | ✅ |
| **失配时** | **`paragraph_index=None`**（不挂到别的段） | ✅ |
| V2b 其他段 | 未被牵连，仍 `matched=True` | ✅ |
| **V3 前插新段** | 索引从 3 → **4**（按内容匹配，不是按索引） | ✅ |
| V4 恢复原文 | 重新 `matched=True`（指纹是内容哈希，不是历史标记） | ✅ |

**无缺陷**。这条链路实现质量良好 —— 尤其是「内容哈希而非索引」的设计，
正好避免了「批注挂错段」这个最危险的失败模式。

---

## 链路 6：开放 API Key + 4 scope + MCP 5 工具

`api_key` 表上线后**从未被真实 Key 调用过**。

### A. Key 生命周期（7 项）

| 检查 | 结果 |
| --- | --- |
| 创建 201，明文 `key` 只出现一次，带 `maa_` 前缀 | ✅ |
| 列表**不含明文**，只有 `prefix` | ✅ |
| 吊销 204，之后 401，重复吊销幂等 | ✅ |

### B. 双通道隔离（4 项）

| 检查 | 结果 |
| --- | --- |
| 无凭证 → 401 | ✅ |
| **JWT 塞 `X-API-Key` → 401** | ✅ |
| **Key 塞 `Authorization: Bearer` → 401** | ✅ |
| Key 调 `tasks:read` → 200 | ✅ |

两条通道物理隔离，不能互相冒充 —— 这是开放 API 最关键的安全边界。

### C. scope 边界（2 项）

| 检查 | 结果 |
| --- | --- |
| 只有 `tasks:read` 的 Key `POST /tasks` → **403** | ✅ |
| 只有 `tasks:read` 的 Key 写批注 → **403（不是 404）** | ✅ |

为什么「403 而非 404」重要：scope 不足是**权限问题**，用户（和 MCP 模型）
需要知道「换一把有权限的 Key 就行」，而不是以为「任务不存在」。

### D. 套餐门禁（2 项）

| 检查 | 结果 |
| --- | --- |
| 免费档**能创建** Key（管理页不受限） | ✅ |
| 免费档**用** Key → 403 `PLAN_FEATURE_UNAVAILABLE` | ✅ |

设计正确：Key 管理是网页功能（免费档可用），Key 鉴权才是套餐功能。

### E. MCP 5 个工具（5 项，真实 HTTP）

| 工具 | 结果 |
| --- | --- |
| `create_literature_review` | ✅ 返回 task_id |
| `list_tasks` | ✅ 含刚建的任务 |
| `get_task_status` | ✅ 有 `has_output` |
| `get_task_output` | ✅ 字段完整 |
| `export_task` | ✅ 返回 download_path；非法格式给出可选列表 |

### 缺陷 C：`TaskSnapshot` 缺 `final_output` → MCP 取不到正文

执行层会**写入** `task.final_output`（复盘正文），但 `TaskSnapshot`
契约里**没有这个字段**（且 `extra="forbid"`），序列化时被静默丢弃。

后果：

```python
"markdown": snapshot.get("final_output"),   # ← 永远 None
"has_output": bool(snapshot.get("final_output")),  # ← 永远 False
```

**MCP 永远返回 `markdown: null`，永远提示「尚无产出」** ——
任务完成了，模型却被告知没结果，然后反复轮询。

**为什么既有测试全绿（假测试）**：`tests/test_mcp_tools.py` 用**手写 dict 替身**
伪造了 `final_output`：

```python
{"task_id": "t1", "status": "completed", "final_output": "很长的正文" * 500}
```

替身里有、真实契约里没有 —— **测的是替身，不是契约**。
这是本项目**第二次**踩这个坑（第一次是前端 `flushPromises()`）。

**修法**：

1. 契约加 `final_output: str | None = None`；
2. `_build_snapshot` 传入 `task.final_output`；
3. 新增 `tests/test_mcp_contract_alignment.py` —— payload 由
   `TaskSnapshot.model_dump()` **真实生成**，契约丢字段测试立刻红。

### 端到端验证（修复后，8 项）

给任务写入 80 字符正文，验证三层串通：

| 层 | 检查 | 结果 |
| --- | --- | --- |
| HTTP | `GET /tasks/{id}` 响应含 `final_output` 且内容一致 | ✅ |
| MCP | `get_task_output.markdown` 等于写入的正文 | ✅ |
| MCP | `get_task_status.has_output=True`，提示语指向取正文 | ✅ |
| MCP | 状态工具仍不返回全文（避免撑爆上下文） | ✅ |

---

## 三个缺陷的共同点

| 缺陷 | 为什么单测没抓到 |
| --- | --- |
| A. 正文标记硬编码 | 预置 GB/T 7714 正好是 `[1]`，期望值 = 硬编码值 |
| B. 多条目串号 | 单条渲染的测试覆盖不到；多条目只在真实使用出现 |
| C. 快照缺字段 | 测试用**手写 dict 替身**伪造了该字段 |

**共同模式**：测试验证的是「我以为的契约」，而不是「真实的契约」。
尤其是 C —— 手写 dict 替身让测试永远无法发现契约缺字段。

### 落地改进

新增两类测试，防止同类问题复发：

1. `test_mcp_contract_alignment.py` —— payload 由真实 Pydantic 模型生成；
2. `test_csl_custom_citation_marker.py` —— 用**自定义样式**（非预置）验证，
   避免「预置样式的期望值恰好等于硬编码值」。

---

## 测试诚实性（本轮 6 组回退验证）

| 回退项 | 结果 |
| --- | --- |
| marker 改回硬编码 `[n]` | 变红 ✅ |
| `preceding` 改回 `()` | 变红（多条目串号） ✅ |
| 删掉 `_build_snapshot` 的 `final_output` | 变红 ✅ |
| 契约删掉 `final_output` 字段 | 变红 ✅ |
| 一致性判定只比数量（上一轮） | 变红 ✅ |
| 端点改回 `service.search()`（上一轮） | 变红 ✅ |

---

## 遗留

| 项 | 说明 |
| --- | --- |
| 批注 `citation` 锚点类型未覆盖 | `ANNOTATION_ANCHOR_TYPES` 有 `citation`，本轮只验证了 `task` / `paragraph` |
| MCP 的 stdio / streamable-http 传输未验证 | 本轮直接调 `tools.py` 函数（业务逻辑层），未启动真实 MCP 协议会话 |
| 自定义样式在**真实任务导出**中的效果未验证 | 需要跑一个完整任务（受 LLM 余额阻塞）。当前用 `CitationRenderer` 直接验证 |
| `demo` 账号有 1 个未吊销的历史 Key | 非本轮探针（name=`q`，更早创建），**未动**以免误删用户数据 |
