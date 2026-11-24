<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
  Activity,
  BookMarked,
  Cpu,
  CreditCard,
  FileInput,
  FileText,
  Gauge,
  KeyRound,
  Library,
  ListChecks,
  LogOut,
  Moon,
  Palette,
  Search,
  Sun,
  Table2,
  Users,
} from "lucide-vue-next";

import { useTheme } from "@/composables/useTheme";
import { useAuthStore } from "@/stores/auth";

const props = defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  (event: "update:open", value: boolean): void;
  (event: "logout"): void;
}>();

interface CommandItem {
  id: string;
  label: string;
  group: string;
  keywords: string;
  icon: typeof ListChecks;
  run: () => void;
}

const router = useRouter();
const auth = useAuthStore();
const { isDark, toggleTheme } = useTheme();

const query = ref("");
const activeIndex = ref(0);
const inputEl = ref<HTMLInputElement | null>(null);

const commands = computed<CommandItem[]>(() => {
  const navItems: CommandItem[] = [
    {
      id: "nav-tasks",
      label: "研究任务",
      group: "页面跳转",
      keywords: "tasks list review 任务 综述",
      icon: ListChecks,
      run: () => void router.push({ name: "tasks" }),
    },
    {
      id: "nav-chat",
      label: "AI 助手对话",
      group: "页面跳转",
      keywords: "chat ai assistant 对话 助手",
      icon: ListChecks,
      run: () => void router.push({ name: "chat" }),
    },
    {
      id: "nav-knowledge",
      label: "知识库",
      group: "页面跳转",
      keywords: "knowledge docs 知识库 文档",
      icon: Library,
      run: () => void router.push({ name: "knowledge" }),
    },
    {
      id: "nav-extractions",
      label: "论文抽取表",
      group: "页面跳转",
      keywords: "extractions table csv 抽取 论文抽取表 对比表 表格",
      icon: Table2,
      run: () => void router.push({ name: "extractions" }),
    },
    {
      id: "nav-reference-library",
      label: "引用库",
      group: "页面跳转",
      keywords: "reference library 引用库 文献",
      icon: BookMarked,
      run: () => void router.push({ name: "reference-library" }),
    },
    {
      id: "nav-reference-import",
      label: "导入文献",
      group: "页面跳转",
      keywords: "import bib ris doi 导入",
      icon: FileInput,
      run: () => void router.push({ name: "reference-import" }),
    },
    {
      id: "nav-styles",
      label: "引用样式",
      group: "页面跳转",
      keywords: "styles csl gbt 引用样式",
      icon: Palette,
      run: () => void router.push({ name: "styles" }),
    },
    {
      id: "nav-templates",
      label: "Word 模板",
      group: "页面跳转",
      keywords: "templates docx word 模板",
      icon: FileText,
      run: () => void router.push({ name: "templates" }),
    },
    {
      id: "nav-workspaces",
      label: "团队工作区",
      group: "页面跳转",
      keywords: "workspaces team 团队 协作",
      icon: Users,
      run: () => void router.push({ name: "workspaces" }),
    },
    {
      id: "nav-observability",
      label: "用量与健康",
      group: "页面跳转",
      keywords: "observability usage metrics 用量 看板",
      icon: Activity,
      run: () => void router.push({ name: "observability" }),
    },
    {
      id: "nav-models",
      label: "我的模型（BYOK）",
      group: "设置",
      keywords: "models byok llm key 模型",
      icon: Cpu,
      run: () => void router.push({ name: "models" }),
    },
    {
      id: "nav-api-keys",
      label: "API Key 管理",
      group: "设置",
      keywords: "api keys token 密钥",
      icon: KeyRound,
      run: () => void router.push({ name: "api-keys" }),
    },
    {
      id: "nav-plan",
      label: "我的套餐",
      group: "设置",
      keywords: "plan billing quota 套餐 配额",
      icon: CreditCard,
      run: () => void router.push({ name: "plan" }),
    },
  ];

  if (auth.user?.role === "admin") {
    navItems.push(
      {
        id: "nav-admin-console",
        label: "后台管理控制台（管理）",
        group: "管理",
        keywords: "admin console 后台 控制台",
        icon: Gauge,
        run: () => void router.push({ name: "admin-console" }),
      },
      {
        id: "nav-admin-byok",
        label: "BYOK 渗透率看板（管理）",
        group: "管理",
        keywords: "admin byok dashboard 管理",
        icon: Gauge,
        run: () => void router.push({ name: "admin-byok" }),
      },
    );
  }

  navItems.push(
    {
      id: "action-theme",
      label: isDark.value ? "切换到浅色主题" : "切换到深色主题",
      group: "快捷操作",
      keywords: "theme dark light 主题 浅色 深色",
      icon: isDark.value ? Sun : Moon,
      run: () => toggleTheme(),
    },
    {
      id: "action-logout",
      label: "退出登录",
      group: "快捷操作",
      keywords: "logout signout 退出 登出",
      icon: LogOut,
      run: () => emit("logout"),
    },
  );

  return navItems;
});

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return commands.value;
  return commands.value.filter(
    (item) =>
      item.label.toLowerCase().includes(q) ||
      item.group.toLowerCase().includes(q) ||
      item.keywords.toLowerCase().includes(q),
  );
});

watch(
  () => props.open,
  async (val) => {
    if (val) {
      query.value = "";
      activeIndex.value = 0;
      await nextTick();
      inputEl.value?.focus();
    }
  },
);

watch(filtered, () => {
  activeIndex.value = 0;
});

function close() {
  emit("update:open", false);
}

function execute(item: CommandItem) {
  close();
  item.run();
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    close();
    return;
  }
  if (event.key === "ArrowDown") {
    event.preventDefault();
    if (filtered.value.length > 0) {
      activeIndex.value = (activeIndex.value + 1) % filtered.value.length;
    }
    return;
  }
  if (event.key === "ArrowUp") {
    event.preventDefault();
    if (filtered.value.length > 0) {
      activeIndex.value = (activeIndex.value - 1 + filtered.value.length) % filtered.value.length;
    }
    return;
  }
  if (event.key === "Enter") {
    const target = filtered.value[activeIndex.value];
    if (target) {
      event.preventDefault();
      execute(target);
    }
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="props.open"
      data-test="command-palette"
      class="fixed inset-0 z-50 flex items-start justify-center bg-foreground/25 px-4 pt-[15vh] backdrop-blur-[1px]"
      role="dialog"
      aria-modal="true"
      aria-label="命令面板"
      @click.self="close"
      @keydown="onKeydown"
    >
      <div
        class="w-full max-w-lg overflow-hidden rounded-lg border border-border-strong bg-popover text-popover-foreground shadow-raised"
      >
        <div class="flex items-center gap-2.5 border-b border-border px-3.5 py-2.5">
          <Search :size="15" aria-hidden="true" class="shrink-0 text-muted-foreground" />
          <input
            ref="inputEl"
            v-model="query"
            type="text"
            placeholder="输入页面名称或操作（如：任务、知识库、主题）…"
            class="w-full bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
          />
          <kbd class="shrink-0 rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
            ESC
          </kbd>
        </div>

        <ul class="max-h-72 overflow-y-auto p-1.5" role="listbox">
          <li v-if="filtered.length === 0" class="px-3 py-6 text-center text-xs text-muted-foreground">
            没有匹配的页面或命令
          </li>
          <li v-for="(item, index) in filtered" :key="item.id" role="option" :aria-selected="index === activeIndex">
            <button
              type="button"
              class="flex w-full items-center justify-between gap-3 rounded-md px-2.5 py-2 text-left text-sm transition-colors"
              :class="
                index === activeIndex
                  ? 'bg-accent text-accent-foreground'
                  : 'text-foreground hover:bg-muted/70'
              "
              @mouseenter="activeIndex = index"
              @click="execute(item)"
            >
              <span class="flex min-w-0 items-center gap-2.5">
                <component :is="item.icon" :size="14" aria-hidden="true" class="shrink-0 text-muted-foreground" />
                <span class="truncate">{{ item.label }}</span>
              </span>
              <span class="shrink-0 text-[11px] text-muted-foreground">{{ item.group }}</span>
            </button>
          </li>
        </ul>
      </div>
    </div>
  </Teleport>
</template>
