# 向量库一致性修复（2026-10-22）

> 状态：**待执行**
> 发现来源：实验矩阵实测（检索画像 `vector_hits=0`）

---

## 1. 缺陷（**产品级，影响所有用户**）

### 现象

| 存储 | 记录数 |
| --- | --- |
| SQLite `knowledge_doc` | **104**（全部 `index_status=ready`） |
| SQLite `doc_chunk` | **28344** |
| SQLite `vector_index` | **28344** |
| **Chroma 实际向量** | **4** |

样本 `vector_index.chroma_id` 在 Chroma 里**查不到**；Chroma 里那 4 条的
`doc_id` 在 SQLite 里**也不存在**（孤儿数据）。Chroma 卷只有 **152K**。

### 影响

**向量检索对所有用户完全失效**。当前检索靠 BM25 单路（`hybrid_search_done
bm25_hits=30 vector_hits=0`）。

- 网页用户搜不到语义相关内容（只能用关键词命中）；
- 实验的「vector vs hybrid」对比失真；
- `index_status=ready` **撒谎** —— 数据库说索引就绪，实际向量不存在。

### 根因

Chroma 数据被清空过（卷重置/迁移），但 SQLite 的 `knowledge_doc` /
`doc_chunk` / `vector_index` 保留。上传链路本身正确（`ingest()` 会写 Chroma），
所以这是**历史数据漂移**，不是代码缺陷。

**但缺一个「一致性校验 + 重建」工具** —— 这是代码缺陷：系统没有任何手段
发现和修复这种漂移。

---

## 2. 设计

### 2.1 为什么不能用现有 `rebuild_all()`

`IndexManager.rebuild_all(sources)` 按**文件路径**重新解析文档：

```python
self.clear()                    # 清空 Chroma + BM25
for source in sources:
    self.add_document(source)   # 重新分块 → 生成**新的** chunk id
```

后果：Chroma 里的 id 与 SQLite `doc_chunk.id` **不一致** →
`_attach_storage_ids()` 无法回溯证据 → 引用追溯断裂。

### 2.2 正确做法：按现有 `DocChunk` 重建向量

```
读 SQLite 的 DocChunk（content 已在库，不需重新解析文件）
  → 用同一 chunk_id 作为 Chroma id
  → 向量化 content
  → 写入 Chroma
  → 校验：Chroma count == DocChunk count
```

**关键**：chunk_id 保持不变 → `vector_index.chroma_id` 映射继续有效。

### 2.3 一致性校验（**这是本次的核心交付**）

一个只读检查，回答「向量库与数据库是否一致」：

```
期望：Chroma count == Σ(ready 文档的 chunk 数)
实测：Chroma count
偏差 → 报错并列出受影响文档
```

**为什么需要它**：这次漂移是「偶然发现」的（实验跑不出引用标记才追查）。
应该有一个可随时运行的检查，而不是靠碰运气。

---

## 3. 实现任务

### Task 1：一致性校验（只读）

- `app/knowledge/services/consistency.py::check_vector_consistency()`
- 返回：`expected` / `actual` / `missing_doc_ids` / `orphan_chroma_ids`
- CLI：`python -m scripts.check_kb_consistency`
- 测试：一致/不一致/空库三种场景

### Task 2：按 chunk 重建向量

- `app/knowledge/services/reindex.py::reindex_from_chunks(user_id=None)`
- 分批（每批 500 条，避免一次性 28344 条打爆内存/超时）
- 进度日志 + 失败不中断（单批失败记录并继续）
- CLI：`python -m scripts.reindex_vectors [--user-id X] [--dry-run]`
- 测试：mock 向量库，断言「id 与 chunk_id 一致」「分批」「失败不中断」

### Task 3：服务器执行 + 验证

- 跑校验（应报告 4 vs 28344）
- 跑重建（28344 条，预计 10-30 分钟）
- 再跑校验（应一致）
- 验证检索：`vector_hits > 0`

---

## 4. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 重建耗时（28344 条） | 分批 + 进度日志；可在后台跑 |
| Mock embedding 维度不匹配 | 先读现有 `vector_index` 的维度；Mock 固定维度 |
| 重建中服务在用 | 重建是**幂等 upsert**（同 id 覆盖），不影响读 |
| 4 条孤儿数据 | 重建后单独清理（它们的 doc_id 在 SQLite 不存在） |
| 磁盘/内存 | 分批 500，内存占用可控 |

---

## 5. Exit Gate

- [ ] 一致性校验能准确报告当前偏差（4 vs 28344）
- [ ] 重建后校验通过（Chroma count == 期望）
- [ ] `vector_hits > 0`（检索真的能命中向量）
- [ ] 证据能回溯到 `DocChunk`（`_attach_storage_ids` 有效）
- [ ] 有测试 + 回退验证（校验逻辑回退后必须变红）
- [ ] 孤儿数据已清理并记录
