<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { AnimatePresence, Motion, useReducedMotion } from "motion-v";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Command,
  Monitor,
  Moon,
  PanelLeftOpen,
  Sun,
} from "lucide-vue-next";

import { useTheme, type ThemeMode } from "@/composables/useTheme";
import { DURATION, EASE_IN, EASE_OUT, OFFSET } from "@/lib/ease";

// 注意：Vue 3 对未指定默认值的 boolean prop 会自动强转为 false，
// 必须用 withDefaults 显式设默认 true，否则移动端菜单触发按钮不会渲染（Issue #64）。
const props = withDefaults(
  defineProps<{
    showMobileTrigger?: boolean;
  }>(),
  {
    showMobileTrigger: true,
  },
);

const emit = defineEmits<{
  (event: "openMobile"): void;
  (event: "openCommand"): void;
}>();

const route = useRoute();
const reduced = useReducedMotion();
const { mode, isDark, setTheme, toggleTheme } = useTheme();

const themeMenuOpen = ref(false);
const themeMenuWrap = ref<HTMLElement | null>(null);
const themeModeTrigger = ref<HTMLButtonElement | null>(null);

const THEME_OPTIONS: { value: ThemeMode; label: string }[] = [
  { value: "light", label: "浅色" },
  { value: "dark", label: "深色" },
  { value: "system", label: "跟随系统" },
];

const themeModeLabel = computed(() => {
  if (mode.value === "system") return "跟随系统";
  return mode.value === "dark" ? "深色" : "浅色";
});

const toggleTitle = computed(() => {
  const currentText =
    mode.value === "system"
      ? `跟随系统（当前${isDark.value ? "深色" : "浅色"}）`
      : isDark.value
        ? "深色模式"
        : "浅色模式";
  const nextText = isDark.value ? "切换到浅色模式" : "切换到深色模式";
  return `${currentText} · ${nextText}`;
});

function selectThemeMode(next: ThemeMode) {
  setTheme(next);
  themeMenuOpen.value = false;
  themeModeTrigger.value?.focus({ preventScroll: true });
}

function onDocumentPointerDown(event: Event) {
  if (!themeMenuOpen.value) return;
  const target = event.target as Node | null;
  if (target && themeMenuWrap.value?.contains(target)) return;
  themeMenuOpen.value = false;
}

function onThemeMenuKeydown(event: KeyboardEvent) {
  if (!themeMenuOpen.value || event.isComposing || event.keyCode === 229) return;
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    themeMenuOpen.value = false;
    themeModeTrigger.value?.focus({ preventScroll: true });
  }
}

onMounted(() => {
  if (typeof document !== "undefined") {
    document.addEventListener("pointerdown", onDocumentPointerDown);
    window.addEventListener("keydown", onThemeMenuKeydown);
  }
});

onBeforeUnmount(() => {
  if (typeof document !== "undefined") {
    document.removeEventListener("pointerdown", onDocumentPointerDown);
    window.removeEventListener("keydown", onThemeMenuKeydown);
  }
});

interface Crumb {
  label: string;
  to?: { name: string; params?: Record<string, string> };
}

const ROUTE_CRUMBS: Record<string, Crumb[]> = {
  tasks: [{ label: "研究任务" }],
  chat: [{ label: "AI 助手" }],
  "task-detail": [{ label: "研究任务", to: { name: "tasks" } }, { label: "任务详情" }],
  "task-screening": [{ label: "研究任务", to: { name: "tasks" } }, { label: "PRISMA 筛选" }],
  knowledge: [{ label: "知识库" }],
  extractions: [{ label: "知识库", to: { name: "knowledge" } }, { label: "论文抽取表" }],
  styles: [{ label: "素材", to: { name: "styles" } }, { label: "引用样式" }],
  templates: [{ label: "素材", to: { name: "templates" } }, { label: "Word 模板" }],
  "reference-import": [{ label: "素材", to: { name: "reference-import" } }, { label: "导入文献" }],
  "reference-library": [{ label: "素材", to: { name: "reference-library" } }, { label: "引用库" }],
  workspaces: [{ label: "协作", to: { name: "workspaces" } }, { label: "团队" }],
  "workspace-detail": [{ label: "团队", to: { name: "workspaces" } }, { label: "工作区详情" }],
  observability: [{ label: "协作", to: { name: "observability" } }, { label: "用量与健康" }],
  models: [{ label: "设置", to: { name: "models" } }, { label: "我的模型" }],
  "api-keys": [{ label: "设置", to: { name: "api-keys" } }, { label: "API Key" }],
  plan: [{ label: "设置", to: { name: "plan" } }, { label: "我的套餐" }],
  "admin-console": [{ label: "管理", to: { name: "admin-console" } }, { label: "后台控制台" }],
  "admin-byok": [{ label: "管理", to: { name: "admin-console" } }, { label: "BYOK 看板" }],
  "account-verify": [{ label: "账号" }, { label: "邮箱验证" }],
};

const crumbs = computed<Crumb[]>(() => {
  const name = String(route.name ?? "tasks");
  return ROUTE_CRUMBS[name] ?? [{ label: "工作台" }];
});
</script>

<template>
  <header
    class="sticky top-0 z-20 flex h-11 shrink-0 items-center justify-between gap-3 border-b border-border bg-background/90 px-4 backdrop-blur-sm md:px-6"
  >
    <div class="flex min-w-0 items-center gap-2">
      <button
        v-if="props.showMobileTrigger"
        type="button"
        data-test="nav-mobile-trigger"
        aria-label="打开导航"
        class="inline-flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground md:hidden"
        @click="emit('openMobile')"
      >
        <PanelLeftOpen :size="15" aria-hidden="true" />
      </button>

      <nav aria-label="面包屑" class="flex min-w-0 items-center gap-1 text-xs">
        <RouterLink
          :to="{ name: 'tasks' }"
          class="hidden shrink-0 rounded px-1.5 py-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground sm:inline-block"
        >
          VeriPaper
        </RouterLink>
        <template v-for="(crumb, index) in crumbs" :key="`${crumb.label}-${index}`">
          <ChevronRight
            :size="12"
            aria-hidden="true"
            class="shrink-0 text-muted-foreground/60 first:hidden sm:first:inline-block"
          />
          <RouterLink
            v-if="crumb.to && index < crumbs.length - 1"
            :to="crumb.to"
            class="truncate rounded px-1.5 py-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            {{ crumb.label }}
          </RouterLink>
          <span v-else class="truncate px-1.5 py-0.5 font-medium text-foreground">
            {{ crumb.label }}
          </span>
        </template>
      </nav>
    </div>

    <div class="flex shrink-0 items-center gap-1.5">
      <button
        type="button"
        data-test="open-command-palette"
        aria-label="打开命令面板"
        class="inline-flex h-7 items-center gap-2 rounded-md border border-border bg-muted/40 px-2.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        @click="emit('openCommand')"
      >
        <Command :size="12" aria-hidden="true" />
        <span class="hidden sm:inline">命令</span>
        <kbd class="hidden rounded border border-border bg-background px-1 font-mono text-[10px] text-muted-foreground sm:inline">
          ⌘K
        </kbd>
      </button>

      <!-- RECHECK-B2：保留两态快速切换守卫 AppLayout.test.ts:231-238 -->
      <button
        type="button"
        data-test="theme-toggle"
        :aria-label="toggleTitle"
        :title="toggleTitle"
        class="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        @click="toggleTheme"
      >
        <AnimatePresence mode="wait" :initial="false">
          <Motion
            v-if="isDark"
            key="sun"
            as="span"
            class="inline-flex items-center justify-center"
            :initial="{ opacity: 0, scale: reduced ? 1 : 0.85 }"
            :animate="{ opacity: 1, scale: 1 }"
            :exit="{ opacity: 0, scale: reduced ? 1 : 0.85 }"
            :transition="{ duration: DURATION.micro, ease: EASE_OUT }"
          >
            <Sun :size="14" aria-hidden="true" />
          </Motion>
          <Motion
            v-else
            key="moon"
            as="span"
            class="inline-flex items-center justify-center"
            :initial="{ opacity: 0, scale: reduced ? 1 : 0.85 }"
            :animate="{ opacity: 1, scale: 1 }"
            :exit="{ opacity: 0, scale: reduced ? 1 : 0.85 }"
            :transition="{ duration: DURATION.micro, ease: EASE_OUT }"
          >
            <Moon :size="14" aria-hidden="true" />
          </Motion>
        </AnimatePresence>
      </button>

      <!-- 三态主题模式选择：浅色 / 深色 / 跟随系统 -->
      <div ref="themeMenuWrap" class="relative">
        <button
          ref="themeModeTrigger"
          type="button"
          data-test="theme-mode-select"
          aria-haspopup="listbox"
          :aria-expanded="themeMenuOpen"
          :aria-label="`主题模式：${themeModeLabel}`"
          :title="`主题模式：${themeModeLabel}`"
          class="inline-flex h-7 items-center gap-1 rounded-md border border-border bg-muted/40 px-2 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          @click="themeMenuOpen = !themeMenuOpen"
        >
          <Monitor v-if="mode === 'system'" :size="12" aria-hidden="true" />
          <Moon v-else-if="mode === 'dark'" :size="12" aria-hidden="true" />
          <Sun v-else :size="12" aria-hidden="true" />
          <span class="hidden sm:inline">{{ themeModeLabel }}</span>
          <ChevronDown :size="11" aria-hidden="true" />
        </button>

        <AnimatePresence :initial="false">
          <Motion
            v-if="themeMenuOpen"
            key="theme-menu"
            as="ul"
            role="listbox"
            aria-label="选择主题模式"
            :initial="{ opacity: 0, y: reduced ? 0 : -OFFSET.microY }"
            :animate="{
              opacity: 1,
              y: 0,
              transition: { duration: reduced ? DURATION.micro : DURATION.fast, ease: EASE_OUT },
            }"
            :exit="{
              opacity: 0,
              y: reduced ? 0 : -OFFSET.microY,
              transition: { duration: reduced ? DURATION.micro : DURATION.micro, ease: EASE_IN },
            }"
            class="absolute right-0 top-full z-40 mt-1.5 w-32 rounded-lg border border-border-strong bg-popover p-1 text-popover-foreground shadow-raised"
          >
            <li
              v-for="option in THEME_OPTIONS"
              :key="option.value"
              role="option"
              :aria-selected="mode === option.value"
            >
              <button
                type="button"
                :data-test="`theme-mode-${option.value}`"
                class="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-xs transition-colors"
                :class="
                  mode === option.value
                    ? 'bg-accent font-medium text-accent-foreground'
                    : 'text-foreground hover:bg-muted/70'
                "
                @click="selectThemeMode(option.value)"
              >
                <span>{{ option.label }}</span>
                <Check v-if="mode === option.value" :size="12" aria-hidden="true" />
              </button>
            </li>
          </Motion>
        </AnimatePresence>
      </div>
    </div>
  </header>
</template>
