# Academic Editorial Phase D Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 先修复第一批独立审查发现的溢出、对比度与键盘入口问题，再把已上线的学术编辑语言一致应用到其余工作台页面。

**Architecture:** 不改变任何 API、Pinia 数据流或业务状态。用一个无业务逻辑的 `PageHeader` 统一标题、说明与右侧操作区；页面内容继续由原组件负责。颜色继续使用全局语义 tokens，正文溢出和焦点可见性在全局基础层修复。

**Tech Stack:** Vue 3 + TypeScript、Tailwind CSS v4、shadcn-vue、Lucide、Vitest、Playwright。

---

## 文件地图

- 前置修复：`main.css`、`TaskListView.vue`、`LoginView.vue`、对应测试与 `m2-result-preview.spec.ts`。
- 新建 `frontend/src/components/common/PageHeader.vue` 与 `PageHeader.test.ts`。
- 使用共享标题：Knowledge、ReferenceLibrary、ReferenceImport、Style、Template、Workspace、Observability、Plan、ApiKey、Screening。
- 特殊页保留自定义结构：WorkspaceDetail、VerifyEmail、PasswordReset；只统一展示标题字体和间距。
- 证据：生产逐页桌面/移动截图、validation JSON、execution-notes。

### Task 0: 修复第一批审查问题

- [x] **Step 1: 用真实浏览器复现长 URL/宽表溢出**

在 TEMP Playwright 脚本中向真实 `.md-body` 注入无空格长 URL、长行内代码和 8 列表格，390px 视口测量 `documentElement.scrollWidth`、表格局部 `scrollWidth/clientWidth`。当前实现必须表现为页面级溢出，作为红灯证据。

- [x] **Step 2: 修复正文断词与表格局部滚动**

```css
.md-body {
  overflow-wrap: anywhere;
}
.md-body table {
  display: block;
  max-width: 100%;
  overflow-x: auto;
}
```

只让表格自身横向滚动，不把整篇正文重新关进内滚动容器。重跑 Step 1，页面 `scrollWidth === innerWidth`，宽表可在自身区域滚动。

- [x] **Step 3: 测量并修复语义文字与焦点环对比度**

用 canvas 把 OKLCH 转 sRGB，测 completed/failed/warning 淡底文字、登录示例文字、ring 在 paper/card 上的比值。调整语义 token 的明度，ring 使用不透明主色：

```css
:root {
  --success: oklch(0.46 0.12 155);
  --warning: oklch(0.47 0.12 70);
  --destructive: oklch(0.48 0.19 27.325);
  --ring: oklch(0.44 0.065 180);
}
```

如实测仍不足，继续降低明度而不是只改背景。目标普通文字 ≥4.5:1，焦点环 ≥3:1；dark tokens 单独复核，不用浅色数值推断。

- [x] **Step 4: 修正待办说明与移动键盘入口**

待办说明同时覆盖规划确认和执行暂停：

```vue
确认规划或处理暂停原因后，任务才会继续推进。
```

移动端保留真正可聚焦的「打开」按钮：行尾单元格改为 `max-md:block`，而非 `max-md:hidden`。E2E 桌面使用按钮点击，移动端使用 `focus() + press("Enter")`，先证明当前移动按钮不可见导致失败，再实现并复验。

- [x] **Step 5: 回归与提交**

新增 waiting_human 文案测试；TaskList、TaskResult、AppLayout 测试全绿后提交：

```powershell
git commit -m "fix(ui): improve editorial accessibility and overflow"
```

### Task 1: 共享 PageHeader 与知识/引用页面

- [x] **Step 1: 先写组件失败测试**

```ts
const wrapper = mount(PageHeader, {
  props: { eyebrow: "Evidence library", title: "知识库", description: "文献综述的证据来源。" },
  slots: { default: '<button type="button">上传</button>' },
});
expect(wrapper.find("h1").text()).toBe("知识库");
expect(wrapper.find("h1").classes()).toContain("font-display");
expect(wrapper.text()).toContain("Evidence library");
expect(wrapper.text()).toContain("文献综述的证据来源。");
expect(wrapper.get("header").get("button").text()).toBe("上传");
```

- [x] **Step 2: 实现共享标题**

```vue
<script setup lang="ts">
defineProps<{ title: string; description?: string; eyebrow?: string }>();
</script>

<template>
  <header class="mb-5 flex flex-wrap items-start justify-between gap-3">
    <div class="min-w-0">
      <p v-if="eyebrow" class="mb-2 text-[10px] font-semibold tracking-[0.16em] text-primary uppercase">
        {{ eyebrow }}
      </p>
      <h1 class="font-display text-3xl font-medium tracking-[-0.035em]">{{ title }}</h1>
      <p v-if="description" class="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
        {{ description }}
      </p>
    </div>
    <div v-if="$slots.default" class="flex shrink-0 flex-wrap items-center gap-2">
      <slot />
    </div>
  </header>
</template>
```

- [x] **Step 3: 应用到知识库、引用库、导入、样式和模板**

每页保留原 h1 文案与 description 的真实动态值， eyebrow 分别为 `Evidence library`、`Reference library`、`Reference import`、`Citation styles`、`Document templates`。右侧操作仍放回原按钮；所有 `data-test`、错误区、表单、列表、预览和删除确认保持原样。

- [x] **Step 4: 运行五页既有测试并提交**

五页测试必须先跑基线，再因共享标题缺失而红，接入后全绿。提交：

```powershell
git commit -m "feat(ui): unify knowledge and reference page headers"
```

### Task 2: 团队、用量、套餐和 API Key

- [x] **Step 1: 保留行为契约并接入 PageHeader**

- Workspace：`Team workspaces`，右侧保留创建团队。
- Observability：`Usage & health`，右侧保留刷新按钮及 loading 状态。
- Plan：`Subscription & usage`，移除 h1 内装饰图标，不改变 usage/plan 数据。
- ApiKey：`Developer settings`，右侧保留生成 Key。
- Screening：`PRISMA screening`，保留任务关联文案和既有操作。

- [x] **Step 2: 运行这些页面的既有测试**

不得修改计数、席位、权限、用量周期、配额、PRISMA 筛选或 API Key 明文只展示一次的行为。若断言只针对旧装饰 DOM，按设计变更更新；若涉及业务行为则修实现。

- [x] **Step 3: 提交**

```powershell
git commit -m "feat(ui): unify collaboration and settings headers"
```

### Task 3: 特殊页面与整站细节一致化

- [x] **Step 1: WorkspaceDetail**

保留返回链接、工作区名 data-test、席位警示与危险操作。标题改 `font-display text-2xl`，不新增返回行为。

- [x] **Step 2: VerifyEmail / PasswordReset**

保留 token、重发和重置流程，标题使用 `font-display`，不引入工作台侧栏或新营销内容。

- [x] **Step 3: 全量测试、类型和构建**

```powershell
node node_modules/vitest/vitest.mjs run
node node_modules/vue-tsc/bin/vue-tsc.js -b
node node_modules/vite/bin/vite.js build
```

- [x] **Step 4: 提交**

```powershell
git commit -m "feat(ui): complete editorial consistency across remaining views"
```

### Task 4: 逐页真实浏览器验收、部署与 CI

- [x] **Step 1: 真实页面逐页截图**

使用一次登录保存会话，逐页检查 knowledge、references/library、references/import、styles、templates、workspaces、workspace detail、observability、plan、settings/api-keys、screening。桌面 1440×1000、移动 390×844，记录 h1、操作按钮、溢出和控制台错误；不能只截页面清单中的一个代表页。

- [x] **Step 2: 关键 E2E**

运行 phase1-pages、reference-library、team-workspace、observability-structure、plan 相关 E2E 的 chromium；移动至少覆盖导航、知识/引用、团队与套餐。登录限流时等待窗口或复用有效会话，不把 429 当页面缺陷。

- [x] **Step 3: 部署服务器并复验**

bundle → fetch → ff-only merge → build nginx → health ready → 正式环境重复截图和 E2E。服务器 HEAD 必须与本地一致。

- [ ] **Step 4: 文档、备份和 CI**

更新 execution-notes 与本计划，提交生产证据，`git push backup main`，查看最终 run 的全部作业。不得把工作区中他人的 evaluation 改动加入提交。
