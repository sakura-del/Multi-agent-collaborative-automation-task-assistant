<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import { ChevronRight, Command, Moon, PanelLeftOpen, Sun } from "lucide-vue-next";

import { useTheme } from "@/composables/useTheme";

defineProps<{
  showMobileTrigger?: boolean;
}>();

const emit = defineEmits<{
  (event: "openMobile"): void;
  (event: "openCommand"): void;
}>();

const route = useRoute();
const { isDark, toggleTheme } = useTheme();

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
  "admin-byok": [{ label: "管理", to: { name: "admin-byok" } }, { label: "BYOK 看板" }],
  "account-verify": [{ label: "账号", to: { name: "account-verify" } }, { label: "邮箱验证" }],
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
        v-if="showMobileTrigger !== false"
        type="button"
        data-test="nav-mobile-trigger"
        aria-label="打开导航"
        class="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground md:hidden"
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

      <button
        type="button"
        data-test="theme-toggle"
        :aria-label="isDark ? '切换到浅色模式' : '切换到深色模式'"
        :title="isDark ? '切换到浅色模式' : '切换到深色模式'"
        class="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        @click="toggleTheme"
      >
        <Sun v-if="isDark" :size="14" aria-hidden="true" />
        <Moon v-else :size="14" aria-hidden="true" />
      </button>
    </div>
  </header>
</template>
