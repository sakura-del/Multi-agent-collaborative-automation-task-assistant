import { createRouter, createWebHistory } from "vue-router";

import AppLayout from "@/layouts/AppLayout.vue";
import KnowledgeView from "@/views/KnowledgeView.vue";
import LoginView from "@/views/LoginView.vue";
import PasswordResetView from "@/views/PasswordResetView.vue";
import ReferenceImportView from "@/views/ReferenceImportView.vue";
import StyleView from "@/views/StyleView.vue";
import TaskDetailView from "@/views/TaskDetailView.vue";
import TaskListView from "@/views/TaskListView.vue";
import TemplateView from "@/views/TemplateView.vue";
import VerifyEmailView from "@/views/VerifyEmailView.vue";
import { useAuthStore } from "@/stores/auth";

// 页面：登录、任务列表、任务详情、知识库、引用样式、Word 模板、参考文献导入，
// 以及账号生命周期两页（邮箱验证 / 密码重置）；工作台页面统一挂在 AppLayout 下。
// 账号生命周期两页是 **public**：邮件链接进入时通常未登录。
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/login",
      name: "login",
      component: LoginView,
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
      path: "/",
      component: AppLayout,
      children: [
        { path: "", redirect: { name: "tasks" } },
        { path: "tasks", name: "tasks", component: TaskListView },
        {
          path: "tasks/:taskId",
          name: "task-detail",
          component: TaskDetailView,
          props: true,
        },
        { path: "knowledge", name: "knowledge", component: KnowledgeView },
        { path: "styles", name: "styles", component: StyleView },
        { path: "templates", name: "templates", component: TemplateView },
        { path: "references/import", name: "reference-import", component: ReferenceImportView },
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
  if (auth.authenticated && to.name === "login") {
    return { name: "tasks" };
  }
  return true;
});

export default router;
