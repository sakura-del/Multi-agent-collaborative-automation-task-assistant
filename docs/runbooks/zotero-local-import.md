# Zotero 本机只读导入

> 相关：[实施计划](../superpowers/plans/2026-10-29-zotero-trust-integration-plan.md) ·
> 脚本 [`scripts/zotero_export.py`](../../scripts/zotero_export.py)

这条链路把 **Zotero 桌面库**里的条目，经**本机只读 Local API** 导出成 BibTeX，
再走**现有导入页与引用库**，最终作为任务的文献源：

```text
Zotero 桌面库
  → (本机 Local API，只读 GET) scripts/zotero_export.py
  → zotero.bib
  → /references/import 解析预览
  → 保存到引用库 /references/library
  → 创建任务时选择「我的引用库」
```

## 1. 明确边界（先看这一节）

| 事项 | 本方案的行为 |
| --- | --- |
| 读 / 写 Zotero | **只读**：只发 GET，不写回、不改条目、不删集合 |
| 网络范围 | 只连本机 `http://localhost:23119/api`；后端与容器**不会**访问用户电脑的 localhost |
| 同步方式 | **手动快照**：每次重新导出；不是实时同步 |
| 去重 / 准入 | **复用**现有 `ReferenceLibraryService.save()`，不另写一套 |
| 数据库 | 不新增表、不做迁移 |
| 真实 Zotero | 自动化测试全部用 mock；**未执行真实 Zotero 验证时不得宣称端到端通过** |

## 2. 前置条件

1. Zotero 桌面客户端与本仓库运行在**同一台机器**。
2. 在 Zotero 设置中启用 Local API：
   - 打开 Zotero → 设置（Preferences）→ 高级（Advanced）；
   - 勾选允许本机其他应用与 Zotero 通信的选项
     （不同版本措辞略有差异，以实际界面为准）。
3. 脚本依赖 `httpx`；推荐直接用后端虚拟环境的解释器（无需激活）。

先确认 Local API 真的起来了（`--noproxy '*'` 避免系统代理把本机请求转走）：

```powershell
curl.exe --noproxy '*' -s -D - http://localhost:23119/api/ -o NUL
```

期望看到 `HTTP/1.1 200` 且响应头含 `Zotero-API-Version: 3`。
返回 `403` 表示 Local API 未启用（或 Zotero 没运行），见 §5。

## 3. 导出 BibTeX

在仓库根目录执行：

```powershell
& 'backend\.venv\Scripts\python.exe' scripts\zotero_export.py `
  --collection <COLLECTION_KEY> `
  --output zotero.bib
```

- `--collection`：集合 key。**省略时导出全部条目**（`/users/0/items`）。
- 查集合 key（只读本机）：

  ```powershell
  curl.exe --noproxy '*' -s http://localhost:23119/api/users/0/collections
  ```

  返回 JSON 数组，每个集合的 `key` 字段就是可传给 `--collection` 的值。
- `--output`：输出文件。已存在时脚本**拒绝覆盖**（以 `x` 模式打开），
  换一个文件名或先自行移走旧文件。
- 退出码 `0` = 已写出，stdout 会打印实际导出条数；**非 0 = 没有生成输出文件**（错误信息在 stderr）。
- Zotero 集合里的**笔记、附件、批注**以及**无标题条目**会被跳过，不生成文献条目；
  若集合里只有这些内容，脚本会以非 0 退出并提示「没有可导出的文献条目」。

生成的 `.bib` 是本地中间产物，可能含个人文献库信息：**不要提交到仓库**，
导入完成后可删除。

### 本机代理变量导致 `InvalidURL` 时

某些 Windows 环境的 `NO_PROXY` 含非法条目（如 `[::1]`），httpx 会报
`InvalidURL: Invalid port ':1]'`。改用仓库自带的包装器剔除非法代理条目：

```powershell
& 'backend\.venv\Scripts\python.exe' backend\scripts\run_without_proxy.py `
  scripts\zotero_export.py --collection <COLLECTION_KEY> --output zotero.bib
```

## 4. 导入现有引用库

1. 打开前端的**导入页** `/references/import`。
2. 用编辑器打开 `zotero.bib`，**复制全文**，粘贴到页面的文本框
   （该页目前是**粘贴式**导入，没有文件上传控件）；格式选「自动识别」或「BibTeX」。
3. 点「解析预览」，先核对条数与标题。
4. 点「保存到引用库」。页面会分别显示：
   - **新增**：真正入库的条数；
   - **合并**：库里已有同一篇文献（归一化 DOI 相同，例如 `10.1234/A` 与
     `https://doi.org/10.1234/a`）；
   - **已存在**：`ref_id` 完全相同（重复导入同一份文件）；
   - **未能入库**：带逐条原因（缺年份、缺 URL 且缺 DOI）。
5. 到引用库页 `/references/library` 核对；创建任务时选「我的引用库」即可作为文献源。

> 缺年份或缺链接的条目会被**现有准入**拒绝，这是有意的：它们无法构造合法的
> `LiteratureRecord`。请回 Zotero 补元数据后重新导出，**不要手工编年份（尤其不要写 1970）**。

## 5. 故障处理

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 403 / 提示「Zotero Local API 未启用」 | Zotero 未运行，或设置里未允许本机应用访问 | 启动 Zotero，按 §2 启用 Local API，再跑一次握手命令 |
| 退出码 1，提示「集合为空或不存在」，并列出可选集合 | 集合 key 写错，或集合确实没有条目 | 按提示里的集合 key 重试；或省略 `--collection` 导出全部 |
| 退出码 1，提示「导出超过 1 MB，请按集合拆分」 | 单次导出超过 1 MB 上限 | 按子集合分批导出，分别导入；不要为了绕过限制删字段 |
| 退出码 1，提示文件已存在（FileExistsError） | `--output` 指向已有文件 | 换新文件名；脚本不会覆盖 |
| 退出码 1，提示「仅支持 Zotero API v3」 | 桌面客户端过旧 | 升级 Zotero 桌面客户端 |
| 退出码 1，提示「返回 5xx」 | Zotero 内部错误或未完成启动 | 重启 Zotero 后重试；仍失败就记录原始错误 |
| 全部条目「未能入库」 | 整批缺年份或缺 URL/DOI | 回 Zotero 补元数据；补不齐的条目无法当文献源 |

### OpenAlex 不可用时

Zotero 导出、BibTeX 解析、保存到引用库**都不依赖 OpenAlex**，这条链路仍然可用。
受影响的只是**引用核验与撤稿状态展示**：

- 核验结论显示「**未核验**」（`unverified` 是降级态，不会据此判失败）；
- 撤稿状态显示「**撤稿状态未知**」，**不会**显示「未标记撤稿」或「安全」；
- 该降级结果**不写缓存**，OpenAlex 恢复后重新核验即可。

## 6. 测试与真实验收记录

自动化测试（全部使用 `httpx.MockTransport`，零真实网络、零真实 Zotero）：

```powershell
cd backend
& '.\.venv\Scripts\python.exe' -m pytest tests/test_zotero_export.py -q
```

覆盖：导出成功且可被 `parse_bibtex()` 解析、403 非零退出且不落盘、空集合非零退出且
提示集合 key、`start`/`limit` 分页断言、复用现有 `ReferenceLibraryService.save()`
的幂等（`created=1` → `skipped=1`）与缺年份 `rejected`（不伪造 1970）。

### 真实 Zotero 未验证时的记录方式

如果本机没有可用 Zotero，或没有启用 Local API，只能在交付记录里写：

```text
- 真实 Zotero 验证：未执行（原因：<未安装 / 未启用 Local API / 无桌面环境>）
- 已验证范围：MockTransport 单测（成功、403、空集合、分页）
  + parse_bibtex / ReferenceLibraryService 集成测试
- 结论：不得宣称「真实 Zotero 端到端通过」
```

**不要把 fixture 通过写成真实 Zotero 验收通过。**

### 真实执行过时的记录模板

```text
- 日期：YYYY-MM-DD
- Zotero 桌面版本：<version>；Local API 响应头 Zotero-API-Version：<3>
- 命令：python scripts/zotero_export.py --collection <KEY> --output <FILE>
- 条目数：<N>；退出码：<0>
- 导入结果：新增 <a> · 合并 <b> · 已存在 <c> · 未能入库 <d>
- 备注：<失败条目的原因摘要>
```

记录里不要粘贴密钥、Cookie 或完整个人文献库内容。
