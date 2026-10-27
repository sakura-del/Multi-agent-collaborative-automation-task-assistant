# Academic Editorial UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在不改变现有任务流程和 API 契约的前提下，把登录页、任务列表和综述结果页升级为已确认的「学术编辑感」设计，并用真实桌面/移动浏览器验收。

**Architecture:** 继续使用单仓库现有 Vue 3 页面与 shadcn-vue 组件，只调整设计 tokens、模板层级和 Tailwind 类。任务数据仍来自 Pinia/API；结果页继续使用 `marked + DOMPurify`，所有核验、评分、文献与下载状态都由真实 props 驱动。测试锁信息架构、可访问名称和真实数据状态，不锁易变的装饰类。

**Tech Stack:** Vue 3 `<script setup>` + TypeScript、Tailwind CSS v4、shadcn-vue/Reka UI、Lucide、Pinia、Vitest、Playwright。

---

## 文件地图

- 修改 `frontend/src/assets/main.css`：暖纸色、墨色、青蓝 tokens，衬线展示字体与论文正文排版。
- 修改 `frontend/src/layouts/AppLayout.vue`：品牌标记、分组导航与当前用户区；保留 10 个目的地和 `app-nav__links`。
- 修改 `frontend/src/layouts/AppLayout.test.ts`：登录页新文案与证据场景断言，保留表单契约。
- 修改 `frontend/src/views/LoginView.vue`：编辑式双栏与证据示例卡，保留登录/注册流程和字段 id。
- 修改 `frontend/src/views/TaskListView.vue`：汇总带、待办行动区、研究索引列表与可见终态文字。
- 修改 `frontend/src/views/TaskListView.test.ts`：行动区行为与可见终态文字，保留筛选/搜索/打开契约。
- 修改 `frontend/src/views/TaskDetailView.vue`：编辑式任务头部与 Tab 间距，不改批注、PRISMA 和四个 Tab。
- 修改 `frontend/src/components/tasks/TaskResult.vue`：论文纸页、证据栏与真实评分条，保留核验 Panel 和下载。
- 修改 `frontend/src/components/tasks/TaskResult.test.ts`：结果结构、真实评分可访问值与既有空态。
- 新建 `$env:TEMP/maa_academic_visual.cjs`：一次性真实浏览器截图与几何检查，**不放仓库**。
- 修改 `docs/superpowers/plans/2026-08-09-00-execution-notes.md`：记录实测证据、偏差与服务器结果。

基线：2026-10-27 受影响三文件 Vitest **42/42 passed**。设计说明见 `docs/superpowers/specs/2026-10-27-academic-editorial-ui-design.md`，概念图见 `docs/design/2026-10-27-premium-redesign/`。

### Task 0: 固化已批准的设计与计划

**Files:**
- Create: `docs/superpowers/specs/2026-10-27-academic-editorial-ui-design.md`
- Create: `docs/superpowers/plans/2026-10-27-academic-editorial-ui-plan.md`
- Create: `docs/design/2026-10-27-premium-redesign/*`

- [x] **Step 1: 检查设计文件与占位符**

Run: `git status --short` 并搜索 `TBD|TODO|Lorem`。允许出现「待用户确认」等状态文字，但不允许未填写实现段落。

- [x] **Step 2: 提交已批准的设计**

```powershell
git add docs/design/2026-10-27-premium-redesign docs/superpowers/specs/2026-10-27-academic-editorial-ui-design.md docs/superpowers/plans/2026-10-27-academic-editorial-ui-plan.md
git commit -m "docs: add academic editorial ui design"
```

Expected: 一次 `docs:` Conventional Commit；不把 TEMP 验证脚本加入提交。

### Task 1: 全局学术编辑 tokens、工作台外壳与登录页

**Files:**
- Modify: `frontend/src/assets/main.css`
- Modify: `frontend/src/layouts/AppLayout.vue`
- Modify: `frontend/src/views/LoginView.vue`
- Test: `frontend/src/layouts/AppLayout.test.ts`

- [x] **Step 1: 先写失败测试**

把登录页身份断言改为新价值主张，并增加证据场景及表单契约：

```ts
const text = wrapper.text();
expect(text).toContain("让每一句论断");
expect(text).toContain("有迹可循");
expect(text).toContain("证据约束");
expect(text).toContain("GB/T 7714—2025");
expect(wrapper.find('[data-test="login-evidence-proof"]').exists()).toBe(true);
expect(wrapper.find('[data-test="login-evidence-proof"]').text()).toContain("引用核验通过");
expect(wrapper.find("#account").exists()).toBe(true);
expect(wrapper.find("#password").exists()).toBe(true);
expect(wrapper.find('[data-test="forgot-password"]').exists()).toBe(true);
```

- [x] **Step 2: 运行并确认红灯**

```powershell
node node_modules/vitest/vitest.mjs run src/layouts/AppLayout.test.ts
```

Expected: 新价值主张或 `login-evidence-proof` 断言失败；既有 10 个导航目的地仍通过。

- [x] **Step 3: 实现 tokens 与字体**

在 `:root` 用 OKLCH 等价色替换现有背景/主色，保留变量名，深色只同步中性色与主色，不新增第二套类：

```css
:root {
  --background: oklch(0.964 0.008 120);
  --foreground: oklch(0.235 0.018 160);
  --card: oklch(0.988 0.006 105);
  --card-foreground: oklch(0.235 0.018 160);
  --muted: oklch(0.94 0.008 120);
  --muted-foreground: oklch(0.49 0.015 160);
  --primary: oklch(0.44 0.065 180);
  --primary-foreground: oklch(0.985 0.005 120);
  --accent: oklch(0.925 0.018 170);
  --accent-foreground: oklch(0.37 0.055 180);
  --surface-brand: oklch(0.955 0.012 145);
  --font-family-display: "Noto Serif SC", "Source Han Serif SC", "Songti SC", STSong, SimSun, serif;
}
@theme inline {
  --font-display: var(--font-family-display);
}
```

在 `LoginView.vue` 保留 script 中认证逻辑，只把 `CAPABILITIES` 与模板改成双栏编辑结构。新证据卡必须是明确标注的静态产品示例，不读取或伪造任务数据：

```vue
<div
  data-test="login-evidence-proof"
  class="mt-10 hidden max-w-xl rounded-xl border border-border-strong/60 bg-card/85 p-5 shadow-raised md:block"
>
  <div class="flex items-center justify-between border-b border-border pb-3 text-xs">
  <span class="font-medium text-muted-foreground">正文证据片段 · 示例</span>
    <span class="inline-flex items-center gap-1 text-success">
      <Check :size="13" aria-hidden="true" />引用核验通过
    </span>
  </div>
  <p class="mt-4 font-display text-sm leading-7 text-card-foreground">
    检索增强生成的评估正从单一答案准确率，转向对忠实度、证据完整性与上下文利用效率的综合判断。
  </p>
  <p class="mt-3 text-xs text-muted-foreground">GB/T 7714—2025 · 顺序编码制</p>
</div>
```

主标题使用 `font-display`，原 `handleSubmit`、模式切换、字段 id 与忘记密码链接保持原样。AppLayout 保留所有 RouterLink、`app-nav__links` 与登出按钮，只替换品牌标记、分组标签和间距类；不增加工作区选择器或新导航行为。

- [x] **Step 4: 运行测试并确认绿灯**

Run: 同 Step 2。Expected: AppLayout/Login **8/8 passed**，无字段或导航回归。

- [x] **Step 5: 真实浏览器检查第一阶段**

用 Playwright 打开 `/login`，截取 1440×1000 与 390×844。断言：

```js
const geometry = await page.evaluate(() => ({
  overflow: document.documentElement.scrollWidth > innerWidth,
  h1: document.querySelector('h1')?.getBoundingClientRect().toJSON(),
  account: document.querySelector('#account')?.getBoundingClientRect().toJSON(),
}));
if (geometry.overflow) throw new Error('登录页横向溢出');
```

人工 `read_image` 检查：桌面证据卡完整、移动表单不被挤出首屏、字体没有乱码。

- [x] **Step 6: 提交**

```powershell
git add frontend/src/assets/main.css frontend/src/layouts/AppLayout.vue frontend/src/layouts/AppLayout.test.ts frontend/src/views/LoginView.vue
git commit -m "feat(ui): establish academic editorial shell"
```

### Task 2: 任务列表改为研究索引

**Files:**
- Modify: `frontend/src/views/TaskListView.vue`
- Test: `frontend/src/views/TaskListView.test.ts`

- [x] **Step 1: 先写行动区与设计变更测试**

新增行动区点击行为测试（一次点击，不额外触发事件）：

```ts
it("待办行动区只筛选需要我处理的任务", async () => {
  const wrapper = await mountWith([
    task({ task_id: "wait", status: "waiting_approval" }),
    task({ task_id: "done", status: "completed" }),
  ]);
  const action = wrapper.find('[data-test="attention-needs-me"]');
  expect(action.exists()).toBe(true);
  expect(action.text()).toContain("1");
  await action.trigger("click");
  expect(wrapper.find('[data-test="task-row-wait"]').exists()).toBe(true);
  expect(wrapper.find('[data-test="task-row-done"]').exists()).toBe(false);
});
```

把旧「终态文字必须 sr-only」用例明确改为设计变更：终态现在**可见**且仍保留形状图标：

```ts
const doneRow = wrapper.find('[data-test="task-row-done"]');
const visibleLabel = doneRow.find('[data-test="terminal-status-label"]');
expect(visibleLabel.exists()).toBe(true);
expect(visibleLabel.classes()).not.toContain("sr-only");
expect(visibleLabel.text()).toBe("已完成");
expect(doneRow.find('[data-test="status-icon"]').attributes("data-state")).toBe("completed");
```

- [x] **Step 2: 运行并确认红灯**

```powershell
node node_modules/vitest/vitest.mjs run src/views/TaskListView.test.ts
```

Expected: `attention-needs-me` 不存在、可见终态标签不存在；其余既有筛选与搜索不应失败。

- [x] **Step 3: 实现汇总带、行动区和终态标签**

保留 `counts`、`FILTERS`、`visibleTasks` 和全部 `data-test`。汇总区改为同一容器中的四个按钮/项，仍保留 `stat-*`。新增行动区直接复用现有筛选状态：

```vue
<button
  v-if="store.items.length"
  type="button"
  data-test="attention-needs-me"
  class="mb-5 flex w-full items-center gap-4 rounded-xl border border-primary/15 bg-accent/45 px-5 py-4 text-left transition-colors hover:bg-accent/70"
  :aria-pressed="filter === 'needs-me'"
  @click="filter = filter === 'needs-me' ? 'all' : 'needs-me'"
>
  <span class="font-display text-4xl leading-none text-primary">{{ counts.needsMe }}</span>
  <span class="min-w-0 flex-1">
    <span class="block text-sm font-semibold">有两项规划等待你的判断</span>
    <span class="mt-1 block text-xs text-muted-foreground">确认研究路径后，智能体才会开始检索与写作。</span>
  </span>
  <span class="text-xs font-medium text-primary">{{ filter === 'needs-me' ? '查看全部' : '查看待办' }} →</span>
</button>
```

文案不能硬编码「两项」；正式模板用插值：

```vue
<span class="block text-sm font-semibold">
  {{ counts.needsMe > 0 ? `有 ${counts.needsMe} 项任务等待你的判断` : "当前没有待处理任务" }}
</span>
```

终态单元格改成：

```vue
<span class="inline-flex items-center gap-1.5 rounded-md bg-success/7 px-2 py-1 text-success">
  <TaskStatusIcon :status="row.task.status" />
  <span data-test="terminal-status-label" class="text-xs">{{ terminalLabel(row.task.status) }}</span>
</span>
```

失败/取消不能都用绿色成功底：用查表式字面量映射 `completed/failed/cancelled` 到完整类名；进行中仍保留原进度条，行尾 `button[aria-label="打开"]` 不删除。

- [x] **Step 4: 运行测试并确认绿灯**

Run: 同 Step 2。Expected: 所有 TaskListView 用例通过，包括进行中 1 个 progressbar、终态无进度条、真实搜索和整行打开。

- [x] **Step 5: 浏览器测量与移动检查**

登录后进入 `/tasks`，读取：

```js
const report = await page.evaluate(() => ({
  rows: document.querySelectorAll('[data-test^="task-row-"]').length,
  terminalLabels: document.querySelectorAll('[data-test="terminal-status-label"]').length,
  overflow: document.documentElement.scrollWidth > innerWidth,
}));
if (!report.rows || report.overflow) throw new Error(JSON.stringify(report));
```

桌面看行动区与列表层级；390px 下筛选项和搜索可换行、任务标题不被状态列挤出。若有缺陷先修再重新量。

- [x] **Step 6: 提交**

```powershell
git add frontend/src/views/TaskListView.vue frontend/src/views/TaskListView.test.ts
git commit -m "feat(ui): turn task list into research index"
```

### Task 3: 综述结果页改为论文纸页与证据边栏

**Files:**
- Modify: `frontend/src/views/TaskDetailView.vue`
- Modify: `frontend/src/components/tasks/TaskResult.vue`
- Modify: `frontend/src/assets/main.css`
- Test: `frontend/src/components/tasks/TaskResult.test.ts`

- [x] **Step 1: 先写结构与真实评分测试**

```ts
it("places the reading sheet before the evidence rail", () => {
  const wrapper = mountResult();
  const paper = wrapper.find('[data-test="result-paper"]');
  const rail = wrapper.find('[data-test="evidence-rail"]');
  expect(paper.exists()).toBe(true);
  expect(rail.exists()).toBe(true);
  expect(paper.element.compareDocumentPosition(rail.element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it("exposes only real score values on the meter elements", () => {
  const wrapper = mountResult();
  const meters = wrapper.findAll('[role="meter"]');
  expect(meters).toHaveLength(2);
  expect(meters[0].attributes("aria-valuenow")).toBe("96");
  expect(meters[1].attributes("aria-valuenow")).toBe("88");
});
```

既有「无核验时显示明确空态、无统计」用例必须原样保留。

- [x] **Step 2: 运行并确认红灯**

```powershell
node node_modules/vitest/vitest.mjs run src/components/tasks/TaskResult.test.ts
```

Expected: `result-paper` / `evidence-rail` / meter 不存在；引用核验与下载基线仍绿。

- [x] **Step 3: 实现纸页与真实证据栏**

外层保持 `data-test="task-result"`，桌面网格改为 `lg:grid-cols-[minmax(0,1fr)_17rem]`，窄屏单列。正文容器：

```vue
<div
  v-if="!showSource"
  data-test="result-markdown"
  class="md-body max-h-none overflow-visible rounded-lg border border-border bg-card px-5 py-6 font-display sm:px-8 lg:px-10"
  v-html="renderedMarkdown"
/>
```

在 `<article>` 上加 `data-test="result-paper"`，在 `<aside>` 上加 `data-test="evidence-rail"`。保留源码视图及其原 class，不把 `v-html` 改为纯文本，也不改变消毒函数。

评分行使用真实 `score.percent`，动态宽度是唯一允许的内联样式：

```vue
<li v-for="score in scores" :key="score.key" class="grid grid-cols-[5rem_1fr_2rem] items-center gap-2 text-xs">
  <span class="text-muted-foreground">{{ score.label }}</span>
  <span
    class="h-1 overflow-hidden rounded-full bg-muted"
    role="meter"
    :aria-label="score.label"
    :aria-valuenow="score.percent"
    aria-valuemin="0"
    aria-valuemax="100"
  >
    <span class="block h-full rounded-full bg-primary" :style="{ width: `${score.percent}%` }" />
  </span>
  <span class="text-right font-medium tabular-nums">{{ score.percent }}</span>
</li>
```

右栏顺序：引用核验 Panel → 质量评分 → 参考文献 → 待改进 → 下载。无评分/无文献使用现有空数组行为，不补 0 或虚构条目；无核验继续由 `ReferenceVerificationPanel` 显示「本次任务未做外部引用核验」。TaskDetail 只改标题字体、头部/Tab 间距，保留 `tab-*`、`toggle-annotation`、`open-screening` 和抽屉。

- [x] **Step 4: 调整 `.md-body` 阅读排版**

仅在 `.md-body` 内增大正文行高与标题层级，所有颜色继续引用 tokens：

```css
.md-body {
  font-size: 0.9375rem;
  line-height: 2;
  text-wrap: pretty;
}
.md-body h1 { font-size: 1.75rem; line-height: 1.45; letter-spacing: -0.025em; }
.md-body h2 { font-size: 1.2rem; margin-top: 2rem; }
.md-body p, .md-body li { max-width: 72ch; }
```

不添加虚构页码、固定核验数或正文引用高亮；Markdown 里没有可靠结构时不臆造证据关联。

- [x] **Step 5: 运行测试并确认绿灯**

Run: 同 Step 2。Expected: TaskResult 全部通过，下载 URL 回收、http(s) 白名单、核验 null 与四态徽标均无回归。

- [x] **Step 6: 真实结果页验证**

从任务列表逐条找「已完成」且结果 Tab 可用的任务；不能把 disabled Tab 当成已验证。截取桌面与 390px 图并测量：

```js
const result = await page.locator('[data-test="task-result"]').boundingBox();
const paper = await page.locator('[data-test="result-paper"]').boundingBox();
const rail = await page.locator('[data-test="evidence-rail"]').boundingBox();
if (!result || !paper || !rail) throw new Error('结果页结构缺失');
```

检查真实核验摘要是否与 Panel 一致、无核验任务是否仍显示空态、长英文标题是否换行、移动端正文先于证据栏。

- [x] **Step 7: 提交**

```powershell
git add frontend/src/views/TaskDetailView.vue frontend/src/components/tasks/TaskResult.vue frontend/src/components/tasks/TaskResult.test.ts frontend/src/assets/main.css
git commit -m "feat(ui): refine review reading and evidence layout"
```

### Task 4: 全量前端门禁与真实浏览器验收

**Files:**
- Create only outside repo: `$env:TEMP/maa_academic_visual.cjs`
- Modify when design contract requires: `frontend/e2e/*.spec.ts`（仅在本批明确设计变更导致失败时）

- [x] **Step 1: 全量单测与类型分别运行**

```powershell
node node_modules/vitest/vitest.mjs run
node node_modules/vue-tsc/bin/vue-tsc.js -b
```

Expected: Vitest 全绿；vue-tsc 0 错误。两条命令分开看完整输出，不串行截尾。

- [x] **Step 2: 构建**

```powershell
node node_modules/vite/bin/vite.js build
```

Expected: 构建成功，无 Tailwind 类扫描或 Vue 模板错误。

- [x] **Step 3: 关键 E2E**

设置 `$env:E2E_BASE_URL='http://10.0.10.2:48080'`，在服务器部署后运行 `m2-preview`、`m2-result-preview`、`team-workspace`、`phase1-pages` 的 chromium 项目。每个文件只安排一次登录；总登录次数计入 5 次/小时限流，必要时复用视觉脚本会话而不是重复盲试。

Expected: 新建任务、结果页、批注、模板/导入页面契约全绿。若是数据原因（首个完成任务无报告），记录并换一个真实有结果任务验证，不能改测试硬编码绕过。

- [x] **Step 4: 桌面与移动最终截图**

TEMP 脚本一次登录，保存登录态，在同一 context 中依次截取登录页、任务页、真实结果页；`setViewportSize` 后重复任务与结果页。输出每页 `scrollWidth/innerWidth`、主要区域 `boundingBox`、控制台错误。脚本和截图不放仓库根目录。

Expected: 三个核心页面均人工查看；无横向溢出、无文字遮挡、无不可见操作；尺寸调整后重新测量。

- [x] **Step 5: 诚实性与最终 diff 检查**

对新增的「待办行动区」和「真实评分 meter」各做一次临时回退：对应测试必须变红，恢复后再变绿。新文件先备份到 TEMP，不用 PowerShell 改写 UTF-8 文件。最后：

```powershell
git status --short
git diff --check
git diff HEAD~3 --stat
```

Expected: 无 TEMP 脚本、无无关后端改动、无空白错误。

### Task 5: 同步服务器、复验与文档

**Files:**
- Modify: `docs/superpowers/plans/2026-08-09-00-execution-notes.md`
- Modify: `docs/superpowers/plans/2026-10-27-academic-editorial-ui-plan.md`（勾选项与证据）

- [x] **Step 1: 按标准 bundle 流程同步**

本地创建 `maa.bundle` 并 scp 到服务器。服务器操作写入 TEMP `.sh` 后执行，避免 ssh 一行命令里的 glob 被 zsh 拒绝；脚本必须输出同步后的 `git log --oneline -1`，再构建 `nginx` 前端镜像并重启对应容器，不动其他项目容器。

- [x] **Step 2: 服务器健康与真实页面复验**

```bash
curl -s http://127.0.0.1:48080/api/v1/health/ready
```

Expected: `status=ready`。随后从本机访问 `http://10.0.10.2:48080`，重复 Task 4 的真实页面检查与关键 E2E；服务器 HEAD 必须等于本地提交，不能只凭本地截图宣称部署完成。

- [x] **Step 3: 更新执行记录并提交文档**

记录：基线 42/42、最终 Vitest/vue-tsc/build 数字、E2E 结果、桌面/移动截图路径、服务器 HEAD、真实数据空态和任何偏差。提交：

```powershell
git add docs/superpowers/plans/2026-08-09-00-execution-notes.md docs/superpowers/plans/2026-10-27-academic-editorial-ui-plan.md
git commit -m "docs: record academic editorial ui verification"
```

- [x] **Step 4: 推送备份并检查 CI**

```powershell
git push backup main
gh run list --limit 5
```

Expected: backup 已推送；查看最新 run，失败时 `gh run view --log-failed` 定位，不把未检查写成「未复验」。
