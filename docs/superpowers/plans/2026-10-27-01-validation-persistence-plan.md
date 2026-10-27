# 校验结论落库：`validation_result` 表从空到有

> 日期：2026-10-27 ｜ 状态：**已实施**
> 前序：[2026-10-27-00-review-length-limit-plan.md](2026-10-27-00-review-length-limit-plan.md)

---

## 1. 缺陷

`validation_result` 表有完整的列结构：

```
id / subtask_id / passed / score / rule_results / llm_report / report / validated_at
```

但**一条记录都没有**。

### 1.1 根因：模型定义了，没人用

```bash
$ grep -r "ValidationResult(" backend/app
# （无结果）
```

全 `app/` 里**没有任何代码实例化 `ValidationResult`**。表建了、迁移跑了、
模型导出了（`app/db/models/__init__.py` 里在 `__all__` 中），但**没有写入方**。

校验结论实际去了哪里？`persistence.py::on_result`：

```python
subtask.output_data = {
    "output": result.output,
    "validation": result.validation,   # ← 只进 JSON，不进表
    "error": result.error,
}
```

`subtask.output_data` 是一个 JSON 列。校验结论被塞进 JSON 里，
**不可查询、不可聚合、不可统计**。

### 1.2 后果（都是本轮实际踩到的）

| 场景 | 影响 |
| --- | --- |
| 排查「为什么转人工」 | 只能逐个任务拉 API 快照，无法按失败原因聚合 |
| 统计校验通过率 | 做不到（`passed` 列形同虚设） |
| 量化缺陷影响面 | 做不到 |

**上一轮修 review 长度上限时，正是因为这张表是空的，无法量化「这个缺陷影响过多少任务」。**
这是排查校验类缺陷的基础设施缺口，不是可选优化。

### 1.3 一个自相矛盾的注释

`reviewer.py` 的 docstring 写着「把**已持久化的**校验结果规范为 `ValidationReport`」，
但校验结果从未持久化到表里。注释描述了一个不存在的事实 ——
这类「文档说做了、代码没做」的漂移，比没有注释更误导。

---

## 2. 设计

### 2.1 新增 `ValidationRepository`

`backend/app/db/repositories/validation.py`，与 `EventRepository` 同构：

- 只 `flush` 不 `commit`（事务由调用方控制，与全项目约定一致）；
- `subtask` 不存在时返回 `None`，不抛异常；
- 报告形状不对时**缺省从严**（`passed=False`、`score=0.0`）。

### 2.2 字段映射

| 契约 `ValidationReport` | 表列 | 转换 |
| --- | --- | --- |
| `passed: bool` | `passed` | 只有 `is True` 才算通过 |
| `score: float`（**0–100**） | `score`（**0–1**） | **必须除以 100 并夹紧** |
| `checks: list` | `rule_results`（JSON，默认 list） | 直接存 |
| `semantic_check`（可选） | `llm_report`（JSON，可空） | 无则 `None` |
| `feedback: str` | `report`（Text，可空） | 直接存 |
| — | `validated_at` | `datetime.now(UTC)` |

### 2.3 `score` 归一化是最容易做错的一处

契约是 **0–100**（`ValidationReport.score: float = Field(ge=0, le=100)`），
而列注释写的是 **0–1**（`comment="质量评分0-1"`）。

直接把 `85.5` 存进去，下游按 0–1 解读会得到「8550% 通过率」。
**这类单位不一致不会报错，只会静默算错**，所以必须显式转换并夹紧：

```python
def normalize_score(raw) -> float:
    value = float(raw)                    # 非数字 → 0.0
    return max(0.0, min(value / 100.0, 1.0))   # 夹紧到 [0, 1]
```

### 2.4 接入点：`on_result`

```python
if result.validation is not None:
    try:
        await ValidationRepository(session).save(subtask.id, result.validation)
    except Exception:
        logger.exception("validation_result_persist_failed", ...)
```

**三个决定**：

1. **`output_data` 里的校验保持不动** —— 落库是新增，不删旧路径，
   否则读 `output_data` 的既有代码（前端快照、复盘 Agent）会拿不到数据；
2. **`validation is None` 时不建行** —— search 等节点没有校验，
   建空行会污染统计（「校验通过率」的分母里混进没校验的节点）；
3. **落库失败只记日志** —— 与既有失败策略一致（持久化问题不该中断执行）。

---

## 3. 验收标准

| # | 标准 | 验证 |
| --- | --- | --- |
| A1 | 校验报告写入表，字段正确 | `test_saves_one_row` |
| A2 | `score` 从 0–100 归一化到 0–1 | `test_score_is_normalized_to_zero_one` |
| A3 | `checks` → `rule_results` | `test_checks_become_rule_results` |
| A4 | `feedback` → `report` | `test_feedback_goes_to_report_text` |
| A5 | `validated_at` 写入 | `test_validated_at_is_set` |
| A6 | **失败的校验也要落库** | `test_failed_validation_is_recorded` |
| A7 | `semantic_check` → `llm_report`，无则为 None | `test_llm_report_*` |
| A8 | 空报告不崩、缺省从严 | `test_empty_report_does_not_crash` |
| A9 | subtask 不存在返回 None | `test_unknown_subtask_id_does_not_crash` |
| A10 | 越界分数夹紧 | `test_score_out_of_range_is_clamped` |
| A11 | **`on_result` 真的调用它** | `test_validation_report_written_to_table` |
| A12 | 无校验的节点不建行 | `test_no_validation_means_no_row` |
| A13 | `output_data` 向后兼容 | `test_output_data_still_keeps_validation` |
| A14 | 落库失败不中断执行 | `test_persist_failure_does_not_break_result` |

**A11 是关键**（AGENTS §5 #34）：Repository 单测全绿，但 `on_result` 不调用它，
表照样是空的 —— 缺陷原样存在。必须有接线测试。

---

## 4. 风险

| 风险 | 缓解 |
| --- | --- |
| 每次校验多一次 INSERT，影响任务耗时 | 单行插入，与既有的 `ExecutionLog` 同量级；失败不阻塞主流程 |
| `validation_result` 无限增长 | 已有 `subtask_id` 外键 + `ondelete="CASCADE"`，删任务级联清理 |
| `score` 单位搞反（0–1 存成 0–100） | 显式 `normalize_score()` + 专项测试 + 夹紧 |
| 历史数据仍然缺失（过去的校验没落库） | **不伪造历史**：表从今天起有数据，不回溯补写（没有数据可补） |

---

## 5. 执行顺序

1. 取证：确认无写入方（已做）
2. 失败测试（11 + 4 条，红）
3. 实现 `ValidationRepository` + 接入 `on_result`
4. 绿 + 无回归（`test_scheduler.py` 19 条）
5. 诚实性验证：回退接线 → **1 条变红**
6. 门禁（逐个跑）→ 提交 → 部署
7. 真实任务验证（表里真的出现记录）→ 文档 → push → **查 CI**

---

## 6. 实施结果

（见文末，实施完成后填写）
