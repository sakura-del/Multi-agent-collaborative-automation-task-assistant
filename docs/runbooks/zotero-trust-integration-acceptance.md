# 方案 A 验收集：OpenAlex 撤稿状态 + Zotero 本机只读导入

> 日期：2026-10-29 ｜ 分支：`codex/zotero-trust-integration` ｜ 状态：代码与自动化验收通过，已 ff 合并到 `main`（本地），未推送/未部署
>
> 设计：[方案 A 设计](../superpowers/specs/2026-10-29-zotero-trust-integration-design.md) ·
> 计划：[实施计划](../superpowers/plans/2026-10-29-zotero-trust-integration-plan.md) ·
> 使用手册：[Zotero 本机只读导入](zotero-local-import.md)

## 1. 交付结果

| 能力 | 状态 | 证据 |
| --- | --- | --- |
| OpenAlex `is_retracted` 三态解析 | 完成 | `MetadataRecord.is_retracted`、OpenAlex `_parse_work`；44 条 OpenAlex 测试 |
| 撤稿状态独立于四态核验 | 完成 | `ReferenceVerification.is_retracted`；`blocked_keys` 仍只按四态判定 |
| v2 缓存隔离 | 完成 | `refverify:v2:`；旧键只作为「不再命中」的负向断言 |
| 复盘/导出显示三态 | 完成 | `（核验：<四态>；已撤稿/未标记撤稿/撤稿状态未知）` |
| 默认 CSL 导出不丢标记 | 完成 | `test_csl_rendered_bibliography_keeps_retraction_marks` |
| 前端真实结果页接线 | 完成 | `TaskResult.vue` → `ReferenceVerificationPanel` 映射测试 |
| Zotero 本机只读导出 | 完成 | CLI + `ZoteroLocalClient`；分页、403、空集合、1 MB、不覆盖测试 |
| 复用现有引用库 | 完成 | 同一 `ReferenceLibraryService.save()`；`created=1` → `skipped=1`；缺年份被拒 |
| 真实 Zotero 验证 | **未执行** | 本机 `localhost:23119` 返回 `000`，未安装 Zotero |

## 2. 提交记录

| 提交 | 内容 |
| --- | --- |
| `a756c0c` | OpenAlex 撤稿字段解析 |
| `4677cf5` | 核验结果传播与 v2 缓存 |
| `8960968` | DOI 确认但标题不符时仍保留撤稿状态 |
| `c42722a` | 复盘、导出、前端撤稿徽标 |
| `2afd0e1` | `TaskResult.vue` 真实接线 |
| `30a6b44` | CSL 默认导出保留撤稿标记 |
| `cf6cb5e` | Zotero 条目 → BibTeX 纯转换 |
| `9bbcc8b` | Local API、CLI、现有引用库集成与手册 |
| `e7784dc` | 跳过笔记/附件/批注，CLI 打印导出条数 |
| `da8c715` | 最终静态检查修正 |

（设计与计划文档提交略，见 `git log --oneline`。）

## 3. 验收门禁与实测结果

### 3.1 后端定向回归

```powershell
cd backend
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' `
  -m pytest tests/test_openalex_client.py tests/test_reference_verifier.py `
  tests/test_reviewer_references.py tests/test_exporter_references.py `
  tests/test_reference_library_service.py tests/test_reference_library_dedup.py `
  tests/test_zotero_export.py tests/test_csl_integration.py tests/test_csl_renderer.py `
  -q --no-cov
```

结果：**317 passed in 23.23s**。

### 3.2 前端全量单测与类型检查

```powershell
cd frontend
& 'C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe' `
  node_modules/vitest/vitest.mjs run
& 'C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe' `
  node_modules/vue-tsc/bin/vue-tsc.js -b
```

结果：**41 个测试文件、476 passed、6 skipped；`vue-tsc -b` exit 0**。

### 3.3 格式、Lint、类型

```powershell
cd backend
python -m black --check app tests                         # 383 files unchanged
python -m black --check --config pyproject.toml ../scripts  # 1 file unchanged
python -m ruff check app tests                            # All checks passed
python -m ruff check --config pyproject.toml ../scripts    # All checks passed
python -m mypy app ../scripts                             # Success: 200 files
```

结果：全部 exit 0。

## 4. 验收项对照

| # | 验收项 | 结果 | 具体测试/证据 |
| --- | --- | --- | --- |
| A1 | OpenAlex 撤稿三态 | 通过 | `test_lookup_doi_parses_is_retracted`（True/False/缺字段） |
| A2 | v2 缓存语义 | 通过 | `test_cache_uses_v2_namespace_and_preserves_unknown_retraction`、`test_cached_verdict_keeps_retracted_true` |
| A3 | BibTeX 可直接复用 | 通过 | `test_export_is_parseable_by_existing_importer`；特殊字符 round-trip |
| A4 | 去重/准入不重复实现 | 通过 | `test_zotero_bibtex_uses_existing_save_and_idempotency`；缺年份 `rejected` 且无 1970 |
| A5 | 端到端展示 | 通过（mock 链路） | Local API mock → BibTeX → 解析 → 引用库 → 撤稿三态 → Markdown/DOCX；真实 Zotero 未执行 |
| A6 | 降级诚实 | 通过 | OpenAlex 不可用 → 未核验/撤稿状态未知；`False` 不显示“安全” |
| A7 | 本地边界 | 通过 | 无远程 localhost、无 Zotero 写操作、无新迁移、无新 Alembic head |

## 5. 真实 Zotero 探测记录

```text
zotero_local_api_status=000
zotero_installed=false
```

结论：**真实 Zotero 验证未执行**。本次只能声称：

- MockTransport 覆盖了成功、403、空集合、分页、1 MB、不覆盖文件；
- `parse_bibtex` 与 `ReferenceLibraryService.save()` 的集成测试通过；
- **不能**声称“真实 Zotero 端到端通过”。

真实环境复测步骤见 [Zotero 本机只读导入](zotero-local-import.md) 与其中的记录模板。

## 6. 已知边界（本轮不包含）

- 不接 Scite Smart Citations、不显示 supporting/contrasting；
- 不做 Zotero 云端 OAuth 同步、不回写、不做实时同步；
- 不做 OpenAlex 引文图谱；
- 不做 RoB/NOS 偏倚评估；
- 撤稿默认只提示，不自动拦截任务；
- 不新增数据库迁移；
- 真实 OpenAlex 网络请求未在本次验收中执行，映射由 fake payload 单测锁定。

## 7. 复现入口

- 设计：`docs/superpowers/specs/2026-10-29-zotero-trust-integration-design.md`
- 计划：`docs/superpowers/plans/2026-10-29-zotero-trust-integration-plan.md`
- Zotero 使用：`docs/runbooks/zotero-local-import.md`
- 提交：`git log --oneline 9caa766..HEAD`

## 8. 未完成项

1. 真实 Zotero 端到端冒烟（本机未安装/未启用 Local API）。
2. 推送与服务器部署（按要求本轮不执行）。
3. Scite/引文图谱等后续扩展（需另立设计）。
