import { createRouter, createWebHistory } from "vue-router";

import AppLayout from "@/layouts/AppLayout.vue";
import KnowledgeView from "@/views/KnowledgeView.vue";
import LoginView from "@/views/LoginView.vue";
import TaskDetailView from "@/views/TaskDetailView.vue";
import TaskListView from "@/views/TaskListView.vue";
import { useAuthStore } from "@/stores/auth";

// 四个页面：登录、任务列表、任务详情、知识库；工作台页面统一挂在 AppLayout 下
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
