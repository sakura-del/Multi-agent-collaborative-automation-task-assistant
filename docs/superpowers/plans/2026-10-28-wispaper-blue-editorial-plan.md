# WisPaper Blue Editorial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 仅把登录页与任务首页升级成已确认的 WisPaper 蓝色编辑感，保留暖纸衬线及全部业务流程。

**Architecture:** 在现有 tokens 上追加两页专用 air 色彩，不改全站主色。模板只调整视觉容器、圆角和背景；`task-search` 与 `new-task` 仍执行原有搜索和创建流程。

**Tech Stack:** Vue 3、Tailwind CSS v4、shadcn-vue、Lucide、Vitest、Playwright。

---

## 文件地图

- `frontend/src/assets/main.css`：air token 与 Tailwind 映射。
- `frontend/src/views/LoginView.vue`：浅蓝背景、证据卡和表单控件。
- `frontend/src/layouts/AppLayout.test.ts`：登录设计签名与既有认证契约。
- `frontend/src/views/TaskListView.vue`：大搜索框、统计/待办/任务索引圆角。
- `frontend/src/views/TaskListView.test.ts`：搜索容器设计签名与既有任务行为。
- `docs/design/wispaper-reference/`：生产截图与验证 JSON。
- `docs/superpowers/plans/2026-08-09-00-execution-notes.md`：实测证据。

基线：AppLayout + TaskListView **26/26 passed**。

### Task 1: 登录页蓝色编辑感

- [x] **Step 1: 写失败的设计签名测试**

在 AppLayout 登录页测试中新增：

```ts
const root = wrapper.find('[data-test="login-air-surface"]');
expect(root.exists()).toBe(true);
expect(root.classes()).toContain("bg-air-surface");
expect(wrapper.find('[data-test="login-evidence-proof"]').classes()).toContain("rounded-2xl");
```

- [x] **Step 2: 确认红灯**

Run: `node node_modules/vitest/vitest.mjs run src/layouts/AppLayout.test.ts`
Expected: `login-air-surface` 不存在，证据卡仍为 rounded-xl；其他认证契约不变。

- [x] **Step 3: 添加 air tokens**

```css
:root {
  --air-surface: oklch(0.955 0.025 220);
  --air-primary: oklch(0.48 0.085 225);
  --air-primary-foreground: oklch(0.99 0 0);
  --air-accent: oklch(0.91 0.028 220);
  --air-accent-foreground: oklch(0.4 0.07 225);
}
@theme inline {
  --color-air-surface: var(--air-surface);
  --color-air-primary: var(--air-primary);
  --color-air-primary-foreground: var(--air-primary-foreground);
  --color-air-accent: var(--air-accent);
  --color-air-accent-foreground: var(--air-accent-foreground);
}
```

- [x] **Step 4: 实现登录页视觉**

根节点加 `data-test="login-air-surface" class="... bg-air-surface"`；标题局部改 `text-air-primary`；证据卡改 `rounded-2xl border-air-accent bg-card/85`；tabs 和提交按钮改胶囊形，保留原 id、提交函数和静态「示例」说明。不得改全局 primary。

- [x] **Step 5: 确认绿灯并提交**

Run: 同 Step 2，Expected: **9/9 passed**（新增一条）。提交 `feat(ui): add blue editorial login`。

### Task 2: 任务首页蓝色编辑感

- [x] **Step 1: 写失败的设计签名测试**

```ts
const shell = wrapper.find('[data-test="task-search-shell"]');
expect(shell.exists()).toBe(true);
expect(shell.classes()).toContain("rounded-full");
expect(wrapper.find('[data-test="task-search"]').attributes("aria-label")).toBe("搜索任务标题");
```

- [x] **Step 2: 确认红灯**

Run: `node node_modules/vitest/vitest.mjs run src/views/TaskListView.test.ts`
Expected: `task-search-shell` 不存在；既有 18 条业务测试不修改。

- [x] **Step 3: 实现大搜索与卡片层级**

在现有搜索父容器上加 `data-test="task-search-shell"`，改为全宽大圆角；搜索 input 保留 `v-model="keyword"`、data-test 和 aria-label。新建按钮与统计/待办/任务索引改浅蓝胶囊和大圆角，所有 click handler 原样保留。

- [x] **Step 4: 确认绿灯并提交**

Run: 同 Step 2，Expected: **19/19 passed**。提交 `feat(ui): add blue editorial task home`。

### Task 3: 正式环境验收与交付

- [x] **Step 1: 全量门禁**

分别运行 Vitest、`vue-tsc -b`、Vite build；不得用 `tsc --noEmit` 替代。

- [x] **Step 2: 部署与双视口截图**

bundle → ff-only → build nginx → health ready。截取两页 1440×1000 与 390×844 图，逐张人工检查并记录 scrollWidth、搜索框和打开按钮几何。

- [x] **Step 3: 颜色与流程验证**

用 canvas 将 OKLCH 转 sRGB，验证 air-primary 白字按钮 ≥4.5:1、焦点环 ≥3:1。跑 `m2-preview` 与 `m2-result-preview` 双视口，覆盖登录、搜索、创建、按钮和移动键盘入口。

- [ ] **Step 4: 文档、备份与 CI**

保存 production-validation.json，更新 execution-notes 和本计划，提交证据，`git push backup main`，查看最终 CI 全作业。不得包含其他 evaluation 改动或 .superpowers 会话文件。
