# 引用一致性判失败：根因与修复记录（2026-11-27）

**现场**：生产任务 `b79d0e43-ee30-4fa6-8a62-d6e2dbd95144`（多智能体协作的任务编排）两次失败，错误为
`引用一致性校验未通过：正文引用的 [E4]、[E5]、[E6]、[E10]、[E13] 在参考文献中不存在`（v1）
与 `[E3]、[E4] 在参考文献中不存在`（v2）。用户重新生成一次仍失败。

## 判失败的位置（不是缺陷本身）

`backend/app/orchestration/validation/citation_consistency.py` 的
`audit_citation_alignment` 要求正文 `[E#]` 集合与 bibliography 的 `index` 集合双向完全对齐；
不一致即由 `task_execution.py` 的 `_guard_citation_consistency` → `_fail_review` 判失败，
且**失败不落库 review_report**，所以从任务详情看不到成稿。

## 根因

`ReviewerAgent._polish_literature_review`（`backend/app/orchestration/agents/reviewer.py`）：

1. 把各节点**原始 content**（含节点局部编号，如 n6 的 `[E3]/[E4]`）直接 JSON 喂给 LLM 做摘要/局限性润色；
2. 把 LLM 返回的 `abstract` / `limitations` **原样**拼进终稿（`_build_literature_review`），
   没有按最终 bibliography 做编号全局化，也没有过滤不存在的编号。

LLM 复述局部编号后，正文出现 bibliography 中不存在的 `[E#]`，落库前闸门判失败。
这解释了两次失败缺号不同：每次 LLM 复述的是不同节点的局部编号。

生产日志可复现该结论：

```
reviewer_literature_done citations=2
citation_consistency_audit ok=false cited=4 indexed=2 missing_entries=[3, 4]
```

## 修复（commit `ffa6e8c`）

- 送润色前用 `_renumber_citations(content, node_id, local_to_global)` 把每个节点的 content 全局化
  （payload 由 `result.model_dump(mode="json")` 组装）；
- 润色返回后以最终 bibliography 的 index 集合为 `allowed`，对 `abstract` 与每条 `limitations`
  调 `_strip_unknown_citations`，剔除悬空 `[E#]`（连同前导空白）；确定性 limitations 同样过滤；
- 回归测试：`backend/tests/test_reviewer_polish_citations.py`（2 条；修前 2 failed，修后随套件 42 passed）。

## 验证（生产）

用同一失败任务 replan（与用户「重新生成」同路径）：

- `POST /tasks/{id}/control {"command":"replan"}` → plan v3（7 节点）→ approve → running → reviewing → **completed**
- `MD_CHARS 1862 BIB 2 CITED [1,2] INDEXED [1,2] MISSING [] ORPHAN [] VERDICT PASS`
- 日志：`citation_consistency_audit ok=true cited=2 indexed=2 missing_entries=[] orphan_entries=[]`
- 部署镜像：`sha256:2d3c4c5dda950c3526423e4bef966cdc861b3d334324a44d732420cd67565090`

## 顺带发现的既有缺陷（未修，另行立项）

`retry_node`（如重跑复盘节点 n8）会返回 `校验未通过` + `warnings=[missing_dependency_output]`：
调度器恢复时只恢复已完成节点 **id**，不恢复其输出（`scheduler.py` 的 `_completed_outputs = {}`），
于是依赖汇总类节点（validate/review）拿不到上游内容。与本次引用修复无关，属独立问题。
