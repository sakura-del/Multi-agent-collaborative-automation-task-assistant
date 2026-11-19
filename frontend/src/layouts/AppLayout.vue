<script setup lang="ts">
// 异步加载：聊天模块（marked 扩展/katex/mermaid）任何加载期异常
// 都不得拖垮全站（2026-11-19 白屏事故的直接加固）
const ChatShell = defineAsyncComponent(() => import("@/components/chat/ChatShell.vue"));
import {
  Activity,
  BookMarked,
  Cpu,
  CreditCard,
  FileInput,
  FileText,
  Folder,
  Gauge,
  KeyRound,
  Library,
  ListChecks,
  LogOut,
  Palette,
  PanelLeftOpen,
  Settings2,
  Users,
} from "lucide-vue-next";
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import AppSidebar from "@/components/layout/AppSidebar.vue";
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
const route = useRoute();

// ===== 侧栏折叠 =====

const COLLAPSE_KEY = "vp:nav-collapsed";
const navCollapsed = ref(localStorage.getItem(COLLAPSE_KEY) === "1");
const mobileOpen = ref(false);

watch(navCollapsed, (value) => {
  localStorage.setItem(COLLAPSE_KEY, value ? "1" : "0");
});

const GROUP_ICONS: Record<string, typeof ListChecks> = {
  素材: Palette,
  协作: Users,
  设置: Settings2,
};

const leafItems = computed(() =>
  visibleItems(NAV_GROUPS.find((group) => !group.label)?.items ?? []),
);
const sidebarGroups = computed(() =>
  NAV_GROUPS.filter((group) => group.label).map((group) => ({
    label: group.label,
    icon: GROUP_ICONS[group.label] ?? Folder,
    items: visibleItems(group.items),
  })),
);
const currentNav = computed(() => String(route.name ?? "tasks"));

function toggleCollapse() {
  navCollapsed.value = !navCollapsed.value;
}

function onGlobalKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    mobileOpen.value = false;
  }
  // Ctrl/Cmd+B：桌面折叠侧栏，移动端开关 sheet
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "b") {
    event.preventDefault();
    if (window.matchMedia("(max-width: 767px)").matches) {
      mobileOpen.value = !mobileOpen.value;
    } else {
      navCollapsed.value = !navCollapsed.value;
    }
  }
}

onMounted(() => window.addEventListener("keydown", onGlobalKeydown));
onBeforeUnmount(() => window.removeEventListener("keydown", onGlobalKeydown));

type NavItem = {
  name: string;
  label: string;
  icon: typeof ListChecks;
  /** 仅管理员可见（服务端仍会鉴权，这里只是不展示入口） */
  adminOnly?: boolean;
};

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
      { name: "admin-byok", label: "BYOK 看板", icon: Gauge, adminOnly: true },
    ],
  },
];

/** 导航过滤：adminOnly 项只对管理员展示。 */
function visibleItems(items: NavItem[]): NavItem[] {
  return items.filter((item) => !item.adminOnly || auth.user?.role === "admin");
}

function handleLogout() {
  auth.logout();
  void router.push({ name: "login" });
}
</script>

<template>
  <div class="flex min-h-dvh bg-background">
    <AppSidebar
      :leaves="leafItems"
      :groups="sidebarGroups"
      :current="currentNav"
      :collapsed="navCollapsed"
      :mobile-open="mobileOpen"
      :username="auth.user?.username ?? '未登录'"
      :email="auth.user?.email ?? ''"
      @update:collapsed="navCollapsed = $event"
      @update:mobile-open="mobileOpen = $event"
      @logout="handleLogout"
    />

    <main class="min-w-0 flex-1 px-4 py-5 md:px-8 md:py-7 lg:px-10">
      <div class="mb-4 flex items-center gap-2 md:hidden">
        <button
          type="button"
          data-test="nav-mobile-trigger"
          aria-label="打开导航"
          class="inline-flex size-9 items-center justify-center rounded-lg border border-border bg-card text-muted-foreground"
          @click="mobileOpen = true"
        >
          <PanelLeftOpen :size="16" aria-hidden="true" />
        </button>
        <span class="text-sm font-semibold tracking-tight">VeriPaper</span>
      </div>
      <RouterView />
    </main>

    <!-- 全局会话小窗（Issue #8 P0）：/chat 全屏页由页面自己渲染，避免双壳 -->
    <ChatShell v-if="$route.name !== 'chat'" />
  </div>
</template>
