import { createRouter, createWebHistory } from "vue-router";

import { useAuthStore } from "@/stores/auth";

// 工作台壳子懒加载：避免未登录打开 /、/login、/password-reset 时提前下载侧栏、命令面板与动效依赖
const AppLayout = () => import("@/layouts/AppLayout.vue");

const AdminByokView = () => import("@/views/AdminByokView.vue");
const AdminConsoleView = () => import("@/views/AdminConsoleView.vue");
const DesignTimelineView = () => import("@/views/DesignTimelineView.vue");
const ApiKeyView = () => import("@/views/ApiKeyView.vue");
const PlanView = () => import("@/views/PlanView.vue");
const ExtractionTableView = () => import("@/views/ExtractionTableView.vue");
const KnowledgeView = () => import("@/views/KnowledgeView.vue");
const LandingView = () => import("@/views/LandingView.vue");
const LoginView = () => import("@/views/LoginView.vue");
const ChatView = () => import("@/views/ChatView.vue");
const ModelSettingsView = () => import("@/views/ModelSettingsView.vue");
const OAuthCallbackView = () => import("@/views/OAuthCallbackView.vue");
const ObservabilityView = () => import("@/views/ObservabilityView.vue");
const PasswordResetView = () => import("@/views/PasswordResetView.vue");
const ReferenceImportView = () => import("@/views/ReferenceImportView.vue");
const ReferenceLibraryView = () => import("@/views/ReferenceLibraryView.vue");
const ScreeningView = () => import("@/views/ScreeningView.vue");
const StyleView = () => import("@/views/StyleView.vue");
const TaskDetailView = () => import("@/views/TaskDetailView.vue");
const TaskListView = () => import("@/views/TaskListView.vue");
const TemplateView = () => import("@/views/TemplateView.vue");
const VerifyEmailView = () => import("@/views/VerifyEmailView.vue");
const WorkspaceDetailView = () => import("@/views/WorkspaceDetailView.vue");
const WorkspaceView = () => import("@/views/WorkspaceView.vue");

// 页面：登录、任务列表、任务详情、知识库、引用样式、Word 模板、参考文献导入，
// 以及账号生命周期两页（邮箱验证 / 密码重置）；工作台页面统一挂在 AppLayout 下。
// 账号生命周期两页是 **public**：邮件链接进入时通常未登录。
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    // 公开产品落地页（Elicit 风格）：未登录访问 / 展示交互式落地页；已登录访问 / 直达 /tasks
    // /landing 始终可访问，便于登录态下随时预览落地页
    {
      path: "/",
      name: "landing",
      component: LandingView,
      meta: { public: true },
    },
    {
      path: "/landing",
      name: "landing-preview",
      component: LandingView,
      meta: { public: true },
    },
    {
      path: "/login",
      name: "login",
      component: LoginView,
      meta: { public: true },
    },
    // 注册页签的独立地址（Issue #26-2）：对外分享的注册链接曾直接落回 /login?redirect=/tasks，
    // 收件人还要自己找到「注册」页签。两个路由共用同一个组件，页签由路由名决定。
    {
      path: "/register",
      name: "register",
      component: LoginView,
      meta: { public: true },
    },
    // 设计演示页（不进导航）：新配色 + beUI 时间线，确认后再往生产页面铺
    {
      path: "/design/timeline",
      name: "design-timeline",
      component: DesignTimelineView,
      meta: { public: true },
    },
    {
      path: "/verify-email",
      name: "verify-email",
      component: VerifyEmailView,
      props: (route) => ({ token: typeof route.query.token === "string" ? route.query.token : "" }),
      meta: { public: true },
    },
    {
      path: "/password-reset",
      name: "password-reset",
      component: PasswordResetView,
      props: (route) => ({ token: typeof route.query.token === "string" ? route.query.token : "" }),
      meta: { public: true },
    },
    {
      path: "/auth/callback",
      name: "oauth-callback",
      component: OAuthCallbackView,
      meta: { public: true },
    },
    // 工作台布局路由：必须保持 path: "/"（不能写 ""，否则 Vue Router 4 评分高于 "/" 会抢占首页）
    // 且排在 landing 之后，保证访问 "/" 精确命中公开落地页，访问 "/tasks" 等命中对应子路由
    {
      path: "/",
      component: AppLayout,
      children: [
        { path: "tasks", name: "tasks", component: TaskListView },
        // 会话全屏页（Issue #8 P0）：三形态之一，与小窗共用 store
        { path: "chat", name: "chat", component: ChatView },
        {
          path: "tasks/:taskId",
          name: "task-detail",
          component: TaskDetailView,
          props: true,
        },
        // PRISMA 筛选工作台：挂在任务之下（筛选数据跟着任务走）
        {
          path: "tasks/:taskId/screening",
          name: "task-screening",
          component: ScreeningView,
          props: true,
        },
        { path: "knowledge", name: "knowledge", component: KnowledgeView },
        {
          path: "extractions/:tableId?",
          name: "extractions",
          component: ExtractionTableView,
          props: true,
        },
        { path: "styles", name: "styles", component: StyleView },
        { path: "templates", name: "templates", component: TemplateView },
        { path: "references/import", name: "reference-import", component: ReferenceImportView },
        // 引用库（2026-10-26）：导入的文献存下来，可作为任务文献源
        {
          path: "references/library",
          name: "reference-library",
          component: ReferenceLibraryView,
        },
        // 用量与健康看板（Phase 2 可观测性）：owner-scoped，只显示自己的任务统计
        { path: "observability", name: "observability", component: ObservabilityView },
        // 团队工作区（Phase 2 协作）：列表 + 成员/席位管理
        { path: "workspaces", name: "workspaces", component: WorkspaceView },
        {
          path: "workspaces/:id",
          name: "workspace-detail",
          component: WorkspaceDetailView,
        },
        // 我的模型（Issue #15 BYOK）：用户自带 API Key
        { path: "settings/models", name: "models", component: ModelSettingsView },
        // API Key 管理（Phase 2 开放 API）
        { path: "settings/api-keys", name: "api-keys", component: ApiKeyView },
        // 我的套餐（Phase 2 计费套餐）
        { path: "settings/plan", name: "plan", component: PlanView },
        // 后台管理控制台（Issue #12）：全站 KPI + 跨用户任务/用户
        {
          path: "admin",
          name: "admin-console",
          component: AdminConsoleView,
          meta: { requiresAdmin: true },
        },
        // 管理员侧 BYOK 渗透率看板（Issue #15 P2）：仅 admin
        {
          path: "admin/byok",
          name: "admin-byok",
          component: AdminByokView,
          meta: { requiresAdmin: true },
        },
        // 登录后也可从这里重发验证邮件（无 token 时展示重发按钮）
        { path: "account/verify", name: "account-verify", component: VerifyEmailView },
      ],
    },
    { path: "/:pathMatch(.*)*", redirect: { name: "tasks" } },
  ],
});

// 鉴权守卫：等待会话恢复完成（ready）后再决定放行/跳转
router.beforeEach(async (to) => {
  const auth = useAuthStore();
  if (!auth.ready) {
    await auth.bootstrap();
  }
  if (!auth.authenticated && !to.meta.public) {
    return { name: "login", query: { redirect: to.fullPath } };
  }
  if (auth.authenticated && (to.name === "landing" || to.name === "login" || to.name === "register")) {
    return { name: "tasks" };
  }
  // 管理员页面：非 admin 直接回任务列表，不暴露「有这一页」的错误信息
  if (to.meta.requiresAdmin && auth.user?.role !== "admin") {
    return { name: "tasks" };
  }
  // 路由级并行业务预取：在下载懒加载页面 JS chunk 的同时启动接口请求
  if (auth.authenticated) {
    if (to.name === "tasks") {
      void import("@/stores/tasks").then(({ useTaskStore }) => useTaskStore().prefetchList());
    } else if (to.name === "task-detail" && typeof to.params.taskId === "string") {
      const taskId = to.params.taskId;
      void import("@/stores/tasks").then(({ useTaskStore }) => useTaskStore().prefetchDetail(taskId));
    }
  }
  return true;
});

export default router;
