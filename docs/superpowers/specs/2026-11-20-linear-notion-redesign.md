# VeriPaper 全站视觉与信息架构重构规范（Linear / Notion 极简风）

> 日期：2026-11-20
> 范围：全站前端视图、布局壳层、UI 基础组件与设计 tokens（Vue 3 + Tailwind v4 + shadcn-vue）

## 1. 设计目标与原则

将 VeriPaper 从当前「beUI 纯白 + 钴蓝 + 24px 大圆角」重塑为 **Linear / Notion 极简工作台**：
1. **克制中性灰阶 + 浅/深双色一等公民**：
   - 默认浅色（Notion 风）：侧栏略带微暖灰（`#F7F7F5` / `oklch(0.974 0.002 90)`），主画布纯净白（`#FFFFFF`），1px 极细实线边框（`oklch(0 0 0 / 7%)`），高对比墨色正文（`oklch(0.18 0 0)`）。
   - 深色模式（Linear Dark）：深炭黑底色（`oklch(0.145 0.004 260)`），略亮面板（`oklch(0.18 0.004 260)`），1px 微亮发丝线（`oklch(1 0 0 / 8%)`）。
   - 点缀色收紧：低饱和度专业蓝灰/靛蓝（`oklch(0.51 0.16 262)`），仅用于主按钮、焦点环与激活态，不滥用大面积渐变或装饰阴影。
2. **紧凑、锐利、去装饰**：
   - 全局圆角收紧至 `--radius: 0.5rem`（8px），内联控件 `rounded-md`（6px），卡片无投影、无多余缩放按压。
   - 工作台 UI 统一干净无衬线字体 + `tabular-nums` 等宽数字；仅在综述正文阅读区（`.md-body`）保留学术编辑排版。
3. **信息架构与键盘效率**：
   - **Linear 紧凑侧栏（224px ↔ 56px 折叠轨）**：顶部「品牌/工作区标识 + `⌘K` 命令面板搜索框」，主导航精简分组（核心工作区 / 素材 / 协作 / 设置），底部紧凑用户条与主题切换。
   - **Notion 顶部面包屑栏（44px）**：所有工作台页面顶部展示 `VeriPaper / 模块 / 当前上下文`，右侧提供 `⌘K` 快捷键与快速主题切换（浅色 / 深色 / 跟随系统）。
   - **全局命令面板（`Cmd/Ctrl + K`）**：支持模糊搜索路由跳转、快速新建综述任务、切换主题。

## 2. 分阶段实施路线

- **Phase 1（设计系统与全局壳层）**：
  - `main.css` tokens 与浅/深双色主题映射 + `useTheme` composable（`light` / `dark` / `system` 持久化 + 启动初始化）。
  - `src/components/ui/` 基础组件（`Card`、`StatCard`、`Button`、`Badge`、`Input`、`Alert`、`Pagination`）统一切换至紧凑微圆角极简风。
  - `AppSidebar.vue`、`TopBar.vue`、`CommandPalette.vue`、`AppLayout.vue` 全新壳层重构与对应测试更新。
- **Phase 2（核心高频工作流）**：
  - 认证域：`LoginView.vue`、`PasswordResetView.vue`、`VerifyEmailView.vue` 极简居中重构。
  - 任务列表：`TaskListView.vue` 默认改为 Linear 高密度状态分组列表，保留 `BeTimeline` 为右上角可选视图切换。
  - 任务详情：`TaskDetailView.vue` 重构为「左侧主内容区（规划/DAG/结果与核验/日志）+ 右侧固定属性检查器（Inspector）」双栏架构（窄屏自适应堆叠）。
  - PRISMA 筛选：`ScreeningView.vue` 重构为紧凑漏斗条 + 左右分栏筛选工作台。
- **Phase 3（素材、协作与设置域全站统一）**：
  - `KnowledgeView.vue`、`ReferenceLibraryView.vue`、`ReferenceImportView.vue`（Notion Database 风格表格与过滤条）。
  - `StyleView.vue`、`TemplateView.vue`（紧凑列表与预览分栏）。
  - `ModelSettingsView.vue`、`ApiKeyView.vue`、`PlanView.vue`、`ObservabilityView.vue`、`WorkspaceView.vue`、`WorkspaceDetailView.vue`、`AdminByokView.vue`（Linear Settings 统一窄栏/网格规范）。

## 3. 质量与验证纪律

- 严格遵守 `AGENTS.md` §6.5（Vue 3 + Tailwind v4 + shadcn-vue + Lucide + motion-v，不新增 scoped CSS）。
- 严格遵守 `AGENTS.md` §5 铁律 #0：本机仅运行 `vue-tsc -b` 与 `npm run build` 检查，所有 `vitest` 测试全部在 `10.0.10.2` 执行。
