<script setup lang="ts">
/**
 * Animated Sidebar（beUI 移植，https://beui.dev/components/motion/animated-sidebar）。
 *
 * 结构：品牌头 + 菜单 + 用户尾；桌面 16rem ↔ 4.25rem 图标轨，
 * 嵌套菜单用 clip-path + stagger 展开；移动端变成带遮罩的 sheet。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { AnimatePresence, Motion, useReducedMotion } from "motion-v";
import {
  ChevronRight,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  X,
} from "lucide-vue-next";

import { SPRING_MORPH } from "@/lib/ease";

import type { SidebarNavGroup, SidebarNavItem } from "./sidebar-types";

const props = defineProps<{
  leaves: SidebarNavItem[];
  groups: SidebarNavGroup[];
  current: string;
  collapsed: boolean;
  mobileOpen: boolean;
  username: string;
  email: string;
}>();

const emit = defineEmits<{
  (event: "update:collapsed", value: boolean): void;
  (event: "update:mobileOpen", value: boolean): void;
  (event: "openCommand"): void;
  (event: "logout"): void;
}>();

const reduced = useReducedMotion();

const openGroup = ref<string | null>(null);

function groupOfCurrent(): string | null {
  return props.groups.find((group) => group.items.some((item) => item.name === props.current))?.label ?? null;
}

watch(
  () => props.current,
  () => {
    const label = groupOfCurrent();
    if (label) openGroup.value = label;
  },
  { immediate: true },
);

const currentInGroup = (group: SidebarNavGroup) =>
  group.items.some((item) => item.name === props.current);

function toggleGroup(group: SidebarNavGroup) {
  if (props.collapsed) {
    emit("update:collapsed", false);
    openGroup.value = group.label;
    return;
  }
  openGroup.value = openGroup.value === group.label ? null : group.label;
}

function closeMobileIfOpen() {
  if (props.mobileOpen) emit("update:mobileOpen", false);
}

const sheet = ref<HTMLElement | null>(null);
let previousFocus: HTMLElement | null = null;

/** Motion 的 ref 是组件实例，取 $el；普通元素直接用。 */
function setSheet(el: unknown) {
  if (el && typeof el === "object" && "$el" in el) {
    sheet.value = (el as { $el?: HTMLElement }).$el ?? null;
  } else {
    sheet.value = (el as HTMLElement | null) ?? null;
  }
}

watch(
  () => props.mobileOpen,
  async (open) => {
    if (typeof document === "undefined") return;
    if (open) {
      previousFocus = document.activeElement as HTMLElement | null;
      document.body.style.overflow = "hidden";
      await nextTick();
      sheet.value?.querySelector<HTMLElement>("button, a")?.focus({ preventScroll: true });
    } else {
      document.body.style.overflow = "";
      previousFocus?.focus({ preventScroll: true });
    }
  },
);

onBeforeUnmount(() => {
  if (typeof document !== "undefined") document.body.style.overflow = "";
});

function onSheetKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    emit("update:mobileOpen", false);
    return;
  }
  if (event.key !== "Tab" || !sheet.value) return;
  const focusable = [...sheet.value.querySelectorAll<HTMLElement>("button, a[href]")].filter(
    (el) => !el.hasAttribute("disabled"),
  );
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

const width = computed(() => (props.collapsed ? "3.75rem" : "14.5rem"));
</script>

<template>
  <!-- 桌面侧栏（Linear / Notion 极简侧栏） -->
  <Motion
    as="aside"
    :initial="false"
    :animate="{ width: reduced ? (props.collapsed ? '3.75rem' : '14.5rem') : width }"
    :transition="reduced ? { duration: 0 } : SPRING_MORPH"
    class="sticky top-0 hidden h-svh shrink-0 flex-col overflow-hidden border-r border-sidebar-border bg-sidebar text-sidebar-foreground md:flex"
    :data-state="props.collapsed ? 'collapsed' : 'expanded'"
    data-test="app-sidebar"
  >
    <!-- 品牌头：h-11 与 TopBar 等高对齐 -->
    <div data-test="nav-brand" class="flex h-11 shrink-0 items-center gap-2 border-b border-sidebar-border px-3">
      <button
        v-if="props.collapsed"
        type="button"
        data-test="nav-expand"
        aria-label="展开侧栏"
        title="展开侧栏"
        class="group/brand relative mx-auto size-7 shrink-0 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring"
        @click="emit('update:collapsed', false)"
      >
        <img
          src="/logo.svg"
          alt=""
          aria-hidden="true"
          class="size-7 transition-opacity duration-150 group-hover/brand:opacity-0 group-focus-visible/brand:opacity-0"
        />
        <span
          class="absolute inset-0 grid place-items-center rounded-md text-muted-foreground opacity-0 transition-opacity duration-150 group-hover/brand:bg-sidebar-accent group-hover/brand:opacity-100 group-focus-visible/brand:bg-sidebar-accent group-focus-visible/brand:opacity-100"
        >
          <PanelLeftOpen :size="15" aria-hidden="true" />
        </span>
      </button>
      <img v-else src="/logo.svg" alt="" aria-hidden="true" class="size-6 shrink-0" />
      <div
        v-if="!props.collapsed"
        class="min-w-0 flex-1 overflow-hidden"
      >
        <p class="truncate text-[13px] font-semibold tracking-tight text-foreground">VeriPaper</p>
      </div>
      <button
        v-if="!props.collapsed"
        type="button"
        data-test="nav-collapse"
        aria-label="收起侧栏"
        title="收起侧栏 (⌘B)"
        class="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
        @click="emit('update:collapsed', true)"
      >
        <PanelLeftClose :size="14" aria-hidden="true" />
      </button>
    </div>

    <!-- 快速搜索触发条 -->
    <div class="px-2 pt-2.5 pb-1">
      <button
        type="button"
        aria-label="快速跳转或搜索命令"
        :title="props.collapsed ? '命令面板 (⌘K)' : undefined"
        class="flex h-7 w-full items-center gap-2 rounded-md border border-sidebar-border bg-background/70 px-2 text-xs text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
        :class="props.collapsed ? 'justify-center px-0' : 'justify-between'"
        @click="emit('openCommand')"
      >
        <span class="flex min-w-0 items-center gap-1.5">
          <Search :size="12" aria-hidden="true" class="shrink-0" />
          <span v-if="!props.collapsed" class="truncate">快速跳转…</span>
        </span>
        <kbd
          v-if="!props.collapsed"
          class="shrink-0 font-mono text-[10px] text-muted-foreground/80"
        >
          ⌘K
        </kbd>
      </button>
    </div>

    <!-- 菜单 -->
    <nav class="app-nav__links flex min-w-0 flex-1 flex-col gap-0.5 overflow-x-hidden overflow-y-auto px-2 py-2" aria-label="主导航">
      <RouterLink
        v-for="item in props.leaves"
        :key="item.name"
        :to="{ name: item.name }"
        :data-test="`nav-${item.name}`"
        :title="props.collapsed ? item.label : undefined"
        class="relative flex min-h-8 w-full items-center gap-2.5 overflow-hidden rounded-md px-2.5 text-left text-[13px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
        :class="
          props.current === item.name
            ? 'bg-sidebar-accent font-medium text-sidebar-accent-foreground'
            : 'text-sidebar-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground'
        "
        @click="closeMobileIfOpen"
      >
        <component :is="item.icon" :size="15" aria-hidden="true" class="shrink-0" />
        <span
          class="truncate whitespace-nowrap transition-all duration-200"
          :class="props.collapsed ? 'pointer-events-none max-w-0 opacity-0' : 'max-w-full opacity-100'"
          >{{ item.label }}</span
        >
      </RouterLink>

      <div
        v-for="group in props.groups"
        :key="group.label"
        class="mt-1 min-w-0"
        :data-test="`nav-group-${group.label}`"
      >
        <button
          type="button"
          :aria-expanded="!props.collapsed && openGroup === group.label"
          :title="props.collapsed ? group.label : undefined"
          class="relative flex min-h-8 w-full items-center gap-2.5 overflow-hidden rounded-md px-2.5 text-left text-[13px] font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
          :class="
            currentInGroup(group)
              ? 'text-foreground'
              : 'text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground'
          "
          @click="toggleGroup(group)"
        >
          <component :is="group.icon" :size="15" aria-hidden="true" class="shrink-0" />
          <span
            class="flex-1 truncate whitespace-nowrap transition-all duration-200"
            :class="props.collapsed ? 'pointer-events-none max-w-0 opacity-0' : 'max-w-full opacity-100'"
            >{{ group.label }}</span
          >
          <Motion
            v-if="!props.collapsed"
            as="span"
            :animate="{ rotate: openGroup === group.label ? 90 : 0 }"
            :transition="reduced ? { duration: 0 } : { duration: 0.15 }"
            class="shrink-0 text-muted-foreground"
          >
            <ChevronRight :size="12" aria-hidden="true" />
          </Motion>
        </button>

        <AnimatePresence :initial="false">
          <Motion
            v-if="!props.collapsed && openGroup === group.label"
            as="ul"
            :initial="{ opacity: 0, clipPath: 'inset(0 0 100% 0 round 6px)' }"
            :animate="{ opacity: 1, clipPath: 'inset(0 0 0% 0 round 6px)' }"
            :exit="{ opacity: 0, clipPath: 'inset(0 0 100% 0 round 6px)' }"
            :transition="{ duration: reduced ? 0.1 : 0.16 }"
            class="mt-0.5 ml-4 flex flex-col gap-0.5 border-l border-sidebar-border pl-2.5"
          >
            <Motion
              v-for="(item, index) in group.items"
              :key="item.name"
              as="li"
              :initial="{ opacity: 0, y: -4 }"
              :animate="{ opacity: 1, y: 0 }"
              :exit="{ opacity: 0, y: -4 }"
              :transition="{ duration: 0.14, delay: reduced ? 0 : index * 0.025 }"
            >
              <RouterLink
                :to="{ name: item.name }"
                :data-test="`nav-${item.name}`"
                class="flex min-h-7 w-full items-center gap-2 overflow-hidden rounded-md px-2 text-left text-xs outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
                :class="
                  props.current === item.name
                    ? 'bg-sidebar-accent font-medium text-sidebar-accent-foreground'
                    : 'text-sidebar-foreground/85 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground'
                "
                @click="closeMobileIfOpen"
              >
                <component :is="item.icon" :size="13" aria-hidden="true" class="shrink-0" />
                <span class="truncate">{{ item.label }}</span>
              </RouterLink>
            </Motion>
          </Motion>
        </AnimatePresence>
      </div>
    </nav>

    <!-- 用户尾 -->
    <div
      class="flex items-center gap-2 border-t border-sidebar-border py-2.5"
      :class="props.collapsed ? 'justify-center px-2' : 'px-2.5'"
    >
      <button
        v-if="props.collapsed"
        type="button"
        data-test="nav-avatar"
        :aria-label="`${props.username} · 展开侧栏`"
        :title="`${props.username} · 展开侧栏`"
        class="grid size-7 shrink-0 place-items-center rounded-md bg-foreground text-xs font-medium text-background outline-none focus-visible:ring-2 focus-visible:ring-ring"
        @click="emit('update:collapsed', false)"
      >
        {{ props.username.slice(0, 1) || "V" }}
      </button>
      <span
        v-else
        class="grid size-7 shrink-0 place-items-center rounded-md bg-foreground text-xs font-medium text-background"
        aria-hidden="true"
      >
        {{ props.username.slice(0, 1) || "V" }}
      </span>
      <div
        v-if="!props.collapsed"
        class="min-w-0 flex-1 overflow-hidden"
      >
        <p class="truncate text-xs font-medium text-foreground" :title="props.email">{{ props.username }}</p>
        <p class="truncate text-[10px] text-muted-foreground" :title="props.email">{{ props.email }}</p>
      </div>
      <button
        v-if="!props.collapsed"
        type="button"
        aria-label="退出登录"
        title="退出登录"
        class="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
        @click="emit('logout')"
      >
        <LogOut :size="13" aria-hidden="true" />
      </button>
    </div>

    <!-- 边缘轨道：点击折叠/展开 -->
    <button
      type="button"
      aria-label="切换侧栏"
      tabindex="-1"
      class="absolute inset-y-0 right-0 z-10 w-2 translate-x-1/2 outline-none after:absolute after:inset-y-0 after:left-1/2 after:w-px after:bg-transparent after:transition-colors hover:after:bg-border"
      @click="emit('update:collapsed', !props.collapsed)"
    />
  </Motion>

  <!-- 移动端 sheet -->
  <Teleport to="body">
    <AnimatePresence :initial="false">
      <div v-if="props.mobileOpen" class="fixed inset-0 z-50 md:hidden">
        <Motion
          as="button"
          aria-label="关闭导航"
          :initial="{ opacity: 0 }"
          :animate="{ opacity: 1 }"
          :exit="{ opacity: 0 }"
          :transition="{ duration: 0.2 }"
          class="absolute inset-0 bg-foreground/30"
          @click="emit('update:mobileOpen', false)"
        />
        <Motion
          as="div"
          :ref="setSheet"
          role="dialog"
          aria-modal="true"
          aria-label="导航"
          :initial="{ x: reduced ? 0 : '-100%', opacity: reduced ? 0 : 1 }"
          :animate="{ x: 0, opacity: 1 }"
          :exit="{ x: reduced ? 0 : '-100%', opacity: reduced ? 0 : 1 }"
          :transition="{ duration: reduced ? 0.12 : 0.28 }"
          class="absolute inset-y-0 left-0 flex w-[18rem] max-w-[88vw] flex-col border-r border-border bg-card shadow-raised"
          @keydown="onSheetKeydown"
        >
          <div class="flex min-h-16 items-center gap-2.5 px-4">
            <img src="/logo.svg" alt="" aria-hidden="true" class="size-8" />
            <div class="min-w-0 flex-1">
              <p class="text-sm font-semibold tracking-tight">VeriPaper</p>
              <p class="text-[8px] tracking-[0.16em] text-muted-foreground">EVIDENCE-LED WRITING</p>
            </div>
            <button
              type="button"
              aria-label="关闭导航"
              class="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              @click="emit('update:mobileOpen', false)"
            >
              <X :size="16" aria-hidden="true" />
            </button>
          </div>

          <nav class="app-nav__links flex flex-1 flex-col gap-1 overflow-y-auto px-3 py-2" aria-label="移动端导航">
            <RouterLink
              v-for="item in props.leaves"
              :key="item.name"
              :to="{ name: item.name }"
              class="flex min-h-10 items-center gap-2.5 rounded-xl px-3 text-left text-sm font-medium"
              :class="
                props.current === item.name
                  ? 'bg-accent text-accent-foreground'
                  : 'text-muted-foreground hover:bg-muted/60'
              "
              @click="closeMobileIfOpen"
            >
              <component :is="item.icon" :size="16" aria-hidden="true" />
              {{ item.label }}
            </RouterLink>
            <div v-for="group in props.groups" :key="group.label" class="mt-2">
              <p class="px-3 pb-1 text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
                {{ group.label }}
              </p>
              <RouterLink
                v-for="item in group.items"
                :key="item.name"
                :to="{ name: item.name }"
                class="flex min-h-9 w-full items-center gap-2 rounded-lg px-3 text-left text-xs"
                :class="
                  props.current === item.name
                    ? 'bg-muted/70 font-medium text-foreground'
                    : 'text-muted-foreground hover:bg-muted/60'
                "
                @click="closeMobileIfOpen"
              >
                <component :is="item.icon" :size="13" aria-hidden="true" />
                {{ item.label }}
              </RouterLink>
            </div>
          </nav>

          <div class="flex items-center gap-2 border-t border-border px-4 py-3">
            <span class="grid size-8 place-items-center rounded-full bg-foreground text-xs text-background">
              {{ props.username.slice(0, 1) || "V" }}
            </span>
            <div class="min-w-0 flex-1">
              <p class="truncate text-xs font-medium">{{ props.username }}</p>
              <p class="truncate text-[10px] text-muted-foreground">{{ props.email }}</p>
            </div>
            <button
              type="button"
              aria-label="退出登录"
              class="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted"
              @click="emit('logout')"
            >
              <LogOut :size="14" aria-hidden="true" />
            </button>
          </div>
        </Motion>
      </div>
    </AnimatePresence>
  </Teleport>
</template>
