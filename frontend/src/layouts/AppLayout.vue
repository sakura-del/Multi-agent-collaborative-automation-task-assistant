<script setup lang="ts">
import {
  Activity,
  BookMarked,
  Cpu,
  CreditCard,
  FileInput,
  FileText,
  KeyRound,
  Library,
  ListChecks,
  LogOut,
  Palette,
  Users,
} from "lucide-vue-next";
import { useRouter } from "vue-router";

import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/stores/auth";

// 工作台布局：安静侧栏 + 分组导航（beUI 视觉语言，见 main.css 头注释）。
// 样式走 Tailwind 原子类，不写 scoped CSS（AGENTS.md §6.5）。
//
// ## 为什么分组
//
// 旧版 9 项平铺，同权重 —— 但「任务」是每天用的，「API Key」配一次就不动。
// 平铺让日常路径淹没在设置项里。按使用频率分三组：
//   工作台  = 每天用
//   素材    = 每周用（配知识库与样式）
//   协作    = 按需（团队场景）
//   设置    = 一次性（配完不碰，沉到底部）
//
// ⚠️ 契约：窄屏导航容器必须保留 `app-nav__links` 类名——
// `e2e/phase1-pages.spec.ts` 的 resetScroll 依赖它把导航的横向滚动归位。
const auth = useAuthStore();
const router = useRouter();

type NavItem = { name: string; label: string; icon: typeof ListChecks };

/** 导航分组：`label` 为空表示不显示分组标题（第一组省掉，避免视觉噪音）。 */
const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "",
    items: [
      { name: "tasks", label: "任务", icon: ListChecks },
      { name: "knowledge", label: "知识库", icon: Library },
    ],
  },
  {
    label: "素材",
    items: [
      { name: "styles", label: "引用样式", icon: Palette },
      { name: "templates", label: "Word 模板", icon: FileText },
      { name: "reference-import", label: "导入文献", icon: FileInput },
      { name: "reference-library", label: "引用库", icon: BookMarked },
    ],
  },
  {
    label: "协作",
    items: [
      { name: "workspaces", label: "团队", icon: Users },
      { name: "observability", label: "用量", icon: Activity },
    ],
  },
  {
    label: "设置",
    items: [
      { name: "models", label: "我的模型", icon: Cpu },
      { name: "api-keys", label: "API Key", icon: KeyRound },
      { name: "plan", label: "我的套餐", icon: CreditCard },
    ],
  },
];

function handleLogout() {
  auth.logout();
  void router.push({ name: "login" });
}
</script>

<template>
  <div class="grid min-h-dvh grid-cols-1 bg-background md:grid-cols-[14.5rem_1fr]">
    <!-- 侧栏（窄屏折成单行横向滚动导航） -->
    <aside
      class="flex flex-col gap-6 border-b border-border bg-card px-3 py-4 max-md:gap-2 md:border-b-0 md:border-r md:py-6"
    >
      <!-- 品牌：引用书标 + 中英文两级字标，强调「证据驱动的学术写作」。 -->
      <div class="hidden items-center gap-2.5 px-2 md:flex">
        <span
          class="flex size-8 shrink-0 items-center justify-center rounded-lg bg-foreground text-background"
          aria-hidden="true"
        >
          <BookMarked :size="16" :stroke-width="1.8" />
        </span>
        <span class="min-w-0">
          <span class="block truncate text-sm font-semibold tracking-tight">文献综述工作台</span>
          <span class="mt-0.5 block text-[8px] tracking-[0.16em] text-muted-foreground">
            EVIDENCE-LED WRITING
          </span>
        </span>
      </div>

      <nav
        class="app-nav__links flex min-w-0 flex-1 items-center gap-x-4 gap-y-5 max-md:flex-nowrap max-md:overflow-x-auto max-md:pb-1 md:flex-col md:flex-nowrap md:items-stretch md:gap-6"
        aria-label="主导航"
      >
        <div v-for="group in NAV_GROUPS" :key="group.label || 'main'" class="shrink-0 md:w-full">
          <!-- 分组标题：微缩大写 + 字距放开（beUI 的导航语言）。
               窄屏不显示 —— 横向滚动条里放不下分组标签，
               且窄屏优先保证"一眼看到全部入口"。 -->
          <p
            v-if="group.label"
            class="mb-1.5 hidden px-2 text-[11px] font-medium tracking-wider text-muted-foreground uppercase md:block"
          >
            {{ group.label }}
          </p>
          <div class="flex items-center gap-1 md:flex-col md:items-stretch">
            <RouterLink
              v-for="item in group.items"
              :key="item.name"
              :to="{ name: item.name }"
              class="group flex items-center gap-2.5 whitespace-nowrap rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              active-class="!bg-accent !text-accent-foreground font-medium"
            >
              <component :is="item.icon" :size="15" aria-hidden="true" class="shrink-0" />
              <span>{{ item.label }}</span>
            </RouterLink>
          </div>
        </div>
      </nav>

      <!-- 当前用户：宽屏在侧栏底部（分隔线 + 用户名 + 登出）。
           窄屏**不绝对定位**（会和滚动中的导航项重叠），
           而是跟在导航行之后单独一行、右对齐。 -->
      <div
        class="flex items-center justify-between gap-2 border-t border-border px-2 pt-3 max-md:justify-end max-md:border-0 max-md:px-0 max-md:pt-0 md:mt-auto"
      >
        <div class="hidden min-w-0 items-center gap-2 md:flex">
          <span
            class="flex size-7 shrink-0 items-center justify-center rounded-md bg-foreground font-display text-xs text-background"
            aria-hidden="true"
          >
            {{ auth.user?.username?.slice(0, 1) ?? "访" }}
          </span>
          <div class="min-w-0">
            <p class="truncate text-xs font-medium text-foreground" :title="auth.user?.email">
              {{ auth.user?.username ?? "未登录" }}
            </p>
            <p class="truncate text-[10px] text-muted-foreground" :title="auth.user?.email">
              {{ auth.user?.email ?? "" }}
            </p>
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="退出登录"
          title="退出登录"
          @click="handleLogout"
        >
          <LogOut :size="15" />
        </Button>
      </div>
    </aside>

    <main class="min-w-0 px-4 py-5 md:px-8 md:py-7 lg:px-10">
      <div class="mx-auto w-full max-w-[1600px]">
        <RouterView />
      </div>
    </main>
  </div>
</template>
