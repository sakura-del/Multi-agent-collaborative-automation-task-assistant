<script setup lang="ts">
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
  LayoutDashboard,
  Library,
  ListChecks,
  Loader2,
  MessageSquare,
  Palette,
  Settings2,
  Users,
  X,
} from "lucide-vue-next";
import { AnimatePresence, Motion, useReducedMotion } from "motion-v";
import {
  computed,
  defineAsyncComponent,
  defineComponent,
  h,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { useRoute, useRouter } from "vue-router";

import AppSidebar from "@/components/layout/AppSidebar.vue";
import CommandPalette from "@/components/layout/CommandPalette.vue";
import TopBar from "@/components/layout/TopBar.vue";
import { DURATION, EASE_IN, EASE_OUT, OFFSET } from "@/lib/ease";
import { hasActiveOverlays } from "@/lib/overlay";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const router = useRouter();
const route = useRoute();
const reduced = useReducedMotion();

const chatOpenStored = ref(localStorage.getItem("vp:chat:open") === "1");

/**
 * 异步加载 ChatShell 期间的轻量化过渡骨架壳层：
 * 严禁占用 data-test="chat-shell"，避免 E2E 抢先命中占位壳。
 */
const ChatShellLoading = defineComponent({
  name: "ChatShellLoading",
  setup() {
    const isDock =
      typeof localStorage !== "undefined" &&
      localStorage.getItem("vp:chat:variant") === "dock";
    const panelCls = isDock
      ? "fixed right-0 top-0 z-40 flex h-svh w-full md:w-[440px] flex-col border-l border-border-strong bg-background shadow-2xl"
      : "fixed bottom-4 right-4 z-40 flex h-[min(580px,calc(100svh-2rem))] max-h-[min(580px,calc(100svh-2rem))] w-[min(380px,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border border-border bg-background shadow-2xl";
    return () =>
      h(
        "section",
        {
          "data-test": "chat-shell-loading",
          "aria-label": "AI 助手加载中",
          class: panelCls,
        },
        [
          h(
            "header",
            { class: "flex items-center justify-between gap-2 border-b border-border px-3 py-2.5" },
            [
              h("div", { class: "flex items-center gap-2 text-sm font-medium text-foreground" }, [
                h(Loader2, {
                  class: "size-3.5 animate-spin text-muted-foreground motion-reduce:animate-none",
                  "aria-hidden": "true",
                }),
                h("span", "AI 助手加载中…"),
              ]),
              h(
                "button",
                {
                  type: "button",
                  "aria-label": "收起加载占位",
                  class:
                    "inline-flex size-7 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                  onClick: () => {
                    localStorage.setItem("vp:chat:open", "0");
                    chatOpenStored.value = false;
                  },
                },
                [h(X, { class: "size-3.5", "aria-hidden": "true" })],
              ),
            ],
          ),
          h("div", { class: "flex flex-1 flex-col gap-3 p-4" }, [
            h("div", {
              class: "h-4 w-2/3 animate-pulse rounded bg-muted/60 motion-reduce:animate-none",
            }),
            h("div", {
              class: "h-16 w-full animate-pulse rounded-xl bg-muted/60 motion-reduce:animate-none",
            }),
            h("div", {
              class: "ml-auto h-12 w-3/4 animate-pulse rounded-xl bg-muted/60 motion-reduce:animate-none",
            }),
          ]),
        ],
      );
  },
});

// 异步加载：聊天模块（marked 扩展/katex/mermaid）任何加载期异常不得拖垮全站
const ChatShell = defineAsyncComponent({
  loader: () => import("@/components/chat/ChatShell.vue"),
  loadingComponent: ChatShellLoading,
  delay: 0,
});

// ===== 侧栏折叠 =====

const COLLAPSE_KEY = "vp:nav-collapsed";
const navCollapsed = ref(localStorage.getItem(COLLAPSE_KEY) === "1");
const mobileOpen = ref(false);
const commandOpen = ref(false);

watch(navCollapsed, (value) => {
  localStorage.setItem(COLLAPSE_KEY, value ? "1" : "0");
});

// 任意途径跳转路由（含 GlobalPromptEntry 与程序化导航）后自动收起移动端抽屉并释放滚动锁
watch(
  () => route.fullPath,
  () => {
    mobileOpen.value = false;
  },
);

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

function onGlobalKeydown(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return;
  if (event.key === "Escape") {
    if (event.defaultPrevented || hasActiveOverlays()) return;
    mobileOpen.value = false;
    commandOpen.value = false;
    return;
  }
  // Ctrl/Cmd+K：打开命令面板（若移动抽屉开启，先收起避免叠层冲突）
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    if (!commandOpen.value && mobileOpen.value) {
      mobileOpen.value = false;
    }
    commandOpen.value = !commandOpen.value;
    return;
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
      { name: "admin-console", label: "后台管理", icon: LayoutDashboard, adminOnly: true },
      { name: "admin-byok", label: "BYOK 看板", icon: Gauge, adminOnly: true },
    ],
  },
];

/** 导航过滤：adminOnly 项只对管理员展示。 */
function visibleItems(items: NavItem[]): NavItem[] {
  return items.filter((item) => !item.adminOnly || auth.user?.role === "admin");
}

function openChatShell() {
  localStorage.setItem("vp:chat:open", "1");
  chatOpenStored.value = true;
}

function handleLogout() {
  auth.logout();
  void router.push({ name: "login" });
}
</script>

<template>
  <div class="flex min-h-dvh bg-background text-foreground">
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
      @open-command="commandOpen = true"
      @logout="handleLogout"
    />

    <div class="flex min-w-0 flex-1 flex-col bg-background">
      <TopBar
        @open-mobile="mobileOpen = true"
        @open-command="commandOpen = true"
      />
      <main
        class="min-w-0 flex-1"
        :class="
          route.name === 'chat'
            ? 'px-4 py-0 md:px-8'
            : 'px-4 pt-5 pb-20 md:px-8 md:pt-6 md:pb-20'
        "
      >
        <div class="mx-auto w-full max-w-[1440px]">
          <RouterView v-slot="{ Component, route: childRoute }">
            <AnimatePresence mode="wait" :initial="false">
              <Motion
                v-if="Component"
                :key="childRoute.path"
                :initial="{ opacity: 0, y: reduced ? 0 : OFFSET.microY }"
                :animate="{
                  opacity: 1,
                  y: 0,
                  transition: { duration: reduced ? DURATION.micro : 0.18, ease: EASE_OUT },
                }"
                :exit="{
                  opacity: 0,
                  transition: { duration: reduced ? DURATION.micro : 0.1, ease: EASE_IN },
                }"
              >
                <component :is="Component" />
              </Motion>
            </AnimatePresence>
          </RouterView>
        </div>
      </main>
    </div>

    <CommandPalette
      v-model:open="commandOpen"
      @logout="handleLogout"
    />

    <!-- 全局会话小窗（Issue #8 P0）：默认收起时仅渲染轻量 FAB，避免首屏加载 292KB ChatShell 并抢占并发带宽 -->
    <template v-if="$route.name !== 'chat'">
      <ChatShell v-if="chatOpenStored" />
      <Teleport v-else to="body">
        <button
          type="button"
          data-test="chat-fab"
          class="fixed bottom-5 right-5 z-30 flex size-12 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg transition-transform hover:scale-105 active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none motion-reduce:hover:scale-100 motion-reduce:active:scale-100"
          aria-label="打开会话"
          title="打开会话"
          @click="openChatShell"
        >
          <MessageSquare class="size-5" />
        </button>
      </Teleport>
    </template>
  </div>
</template>
