<script setup lang="ts">
import {
  Activity,
  FileInput,
  FileText,
  ListChecks,
  Library,
  LogOut,
  CreditCard,
  KeyRound,
  Palette,
  Users,
} from "lucide-vue-next";
import { useRouter } from "vue-router";

import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/stores/auth";

// 工作台布局：安静左侧导航 + 当前用户 + 图标登出，不做装饰性卡片。
// 样式走 shadcn-vue / Nova 规范（AGENTS.md §6.5）。
//
// ⚠️ 契约：窄屏导航容器必须保留 `app-nav__links` 类名——
// `e2e/phase1-pages.spec.ts` 的 resetScroll 依赖它把导航的横向滚动归位。
const auth = useAuthStore();
const router = useRouter();

// 导航项集中定义：新增页面只改这一处，避免模板里重复结构
const NAV_ITEMS = [
  { name: "tasks", label: "任务", icon: ListChecks },
  { name: "knowledge", label: "知识库", icon: Library },
  { name: "styles", label: "引用样式", icon: Palette },
  { name: "templates", label: "Word 模板", icon: FileText },
  { name: "reference-import", label: "导入文献", icon: FileInput },
  { name: "workspaces", label: "团队", icon: Users },
  { name: "observability", label: "用量", icon: Activity },
  { name: "api-keys", label: "API Key", icon: KeyRound },
  { name: "plan", label: "我的套餐", icon: CreditCard },
] as const;

function handleLogout() {
  auth.logout();
  void router.push({ name: "login" });
}
</script>

<template>
  <div class="grid min-h-screen grid-cols-1 md:grid-cols-[13rem_1fr]">
    <!-- 侧栏（窄屏折成顶部横向导航） -->
    <aside
      class="flex flex-col gap-5 border-b border-border bg-card px-3 py-3 md:border-b-0 md:border-r md:py-5"
    >
      <div class="hidden px-2 text-sm font-semibold md:block">文献综述工作台</div>

      <nav
        class="app-nav__links flex min-w-0 flex-1 flex-wrap items-center gap-1 md:flex-col md:items-stretch"
        aria-label="主导航"
      >
        <RouterLink
          v-for="item in NAV_ITEMS"
          :key="item.name"
          :to="{ name: item.name }"
          class="flex items-center gap-2 whitespace-nowrap rounded-md px-2.5 py-2 text-sm text-foreground transition-colors hover:bg-muted"
          active-class="bg-muted font-medium"
        >
          <component :is="item.icon" :size="16" aria-hidden="true" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="flex items-center justify-between gap-2 px-2 md:mt-auto">
        <span
          class="hidden truncate text-xs text-muted-foreground md:block"
          :title="auth.user?.email"
        >
          {{ auth.user?.username ?? "未登录" }}
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="退出登录"
          title="退出登录"
          @click="handleLogout"
        >
          <LogOut :size="16" />
        </Button>
      </div>
    </aside>

    <main class="min-w-0 p-4 md:px-8 md:py-6">
      <RouterView />
    </main>
  </div>
</template>
