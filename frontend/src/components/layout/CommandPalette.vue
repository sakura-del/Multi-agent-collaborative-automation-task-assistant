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

import { MotionDialog } from "@/components/ui/dialog";
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

interface CommandGroup {
  group: string;
  items: Array<{ item: CommandItem; flatIndex: number }>;
}

const router = useRouter();
const auth = useAuthStore();
const { isDark, toggleTheme } = useTheme();

const query = ref("");
const activeIndex = ref(0);
const inputEl = ref<HTMLInputElement | null>(null);
const optionRefs = new Map<number, HTMLElement>();

function setOptionRef(index: number, el: unknown) {
  if (el instanceof HTMLElement) {
    optionRefs.set(index, el);
  } else {
    optionRefs.delete(index);
  }
}

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

const groupedCommands = computed<CommandGroup[]>(() => {
  const groups: CommandGroup[] = [];
  const byName = new Map<string, CommandGroup>();
  filtered.value.forEach((item, flatIndex) => {
    let bucket = byName.get(item.group);
    if (!bucket) {
      bucket = { group: item.group, items: [] };
      byName.set(item.group, bucket);
      groups.push(bucket);
    }
    bucket.items.push({ item, flatIndex });
  });
  return groups;
});

watch(
  () => props.open,
  async (val) => {
    if (val) {
      query.value = "";
      activeIndex.value = 0;
      await nextTick();
      inputEl.value?.focus();
    } else {
      optionRefs.clear();
    }
  },
);

watch(filtered, () => {
  activeIndex.value = 0;
  optionRefs.clear();
});

function close() {
  emit("update:open", false);
}

function execute(item: CommandItem) {
  close();
  item.run();
}

async function moveActive(delta: number) {
  const total = filtered.value.length;
  if (total === 0) return;
  activeIndex.value = (activeIndex.value + delta + total) % total;
  await nextTick();
  optionRefs.get(activeIndex.value)?.scrollIntoView?.({ block: "nearest" });
}

function onKeydown(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return;
  if (event.key === "ArrowDown") {
    event.preventDefault();
    void moveActive(1);
    return;
  }
  if (event.key === "ArrowUp") {
    event.preventDefault();
    void moveActive(-1);
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
  <MotionDialog
    :open="props.open"
    data-test="command-palette"
    aria-label="命令面板"
    initial-focus="input"
    backdrop-class="items-start bg-foreground/25 px-4 pt-[15vh]"
    class="max-w-lg overflow-hidden border-border-strong bg-popover p-0 text-popover-foreground"
    @update:open="emit('update:open', $event)"
  >
    <div @keydown="onKeydown">
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
        <template v-for="group in groupedCommands" :key="group.group">
          <li
            role="presentation"
            class="px-2.5 pt-2 pb-1 text-[11px] font-medium text-muted-foreground first:pt-1"
          >
            {{ group.group }}
          </li>
          <li
            v-for="entry in group.items"
            :key="entry.item.id"
            :ref="(el) => setOptionRef(entry.flatIndex, el)"
            role="option"
            :aria-selected="entry.flatIndex === activeIndex"
          >
            <button
              type="button"
              class="flex w-full items-center justify-between gap-3 rounded-md px-2.5 py-2 text-left text-sm transition-colors"
              :class="
                entry.flatIndex === activeIndex
                  ? 'bg-accent text-accent-foreground'
                  : 'text-foreground hover:bg-muted/70'
              "
              @mouseenter="activeIndex = entry.flatIndex"
              @focus="activeIndex = entry.flatIndex"
              @click="execute(entry.item)"
            >
              <span class="flex min-w-0 items-center gap-2.5">
                <component
                  :is="entry.item.icon"
                  :size="14"
                  aria-hidden="true"
                  class="shrink-0 text-muted-foreground"
                />
                <span class="truncate">{{ entry.item.label }}</span>
              </span>
              <span class="shrink-0 text-[11px] text-muted-foreground">{{ entry.item.group }}</span>
            </button>
          </li>
        </template>
      </ul>
    </div>
  </MotionDialog>
</template>
