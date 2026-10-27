<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ChevronRight, Plus, Search } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import StatusBadge from "@/components/common/StatusBadge.vue";
import TaskCreateForm from "@/components/tasks/TaskCreateForm.vue";
import TaskStatusIcon from "@/components/tasks/TaskStatusIcon.vue";
import { Button } from "@/components/ui/button";
import { useTaskStore } from "@/stores/tasks";
import type { TaskCreate, TaskSummary } from "@/api/types";
import {
  formatDateTime,
  isActive,
  isTerminal,
  needsUserAction,
  splitTaskTitle,
  taskProgress,
} from "@/utils/task-status";

// 任务列表：汇总 + 筛选 + 搜索 + 列表（桌面表格 / 移动单列）。
//
// ⚠️ 测试契约（不要改）：`data-test="new-task"` / `data-test="empty-state"` /
// `data-test="task-row-<id>"`（e2e 里按前缀 `task-row-` 定位）。
//
// ## 这次重设计解决什么（2026-10-23）
//
// 1. **同质化墙**：20 行里 19 行标题几乎一样，唯一区分（`Issue #6 复验 #3`）
//    和主题同权重。改为「主标题 + 次级灰字」，信息不缺、层级清楚。
// 2. **进度条说谎**：旧进度条是从状态映射的常量（失败也是 100% 满格蓝），
//    与完成无法区分。终态改用形状图标；只有进行中才显示百分比条。
// 3. **没有汇总**：「待我确认」是唯一需要用户行动的状态，必须单独突出。
// 4. **没有筛选/搜索**：20 条以上就找不到东西。
// 5. **20 个「打开」按钮**：整行可点击，省掉一整列。
const store = useTaskStore();
const router = useRouter();

const showForm = ref(false);
const submitting = ref(false);
const errorMessage = ref<string | null>(null);
const keyword = ref("");
/** 筛选：`all` / `needs-me` / `active` / `done`。 */
const filter = ref<"all" | "needs-me" | "active" | "done">("all");

onMounted(() => {
  void refresh();
});

async function refresh() {
  errorMessage.value = null;
  try {
    await store.loadList();
  } catch (error) {
    errorMessage.value = (error as Error).message;
  }
}

async function handleCreate(payload: TaskCreate) {
  submitting.value = true;
  errorMessage.value = null;
  try {
    const created = await store.create(payload);
    showForm.value = false;
    await router.push({ name: "task-detail", params: { taskId: created.task_id } });
  } catch (error) {
    errorMessage.value = (error as Error).message;
  } finally {
    submitting.value = false;
  }
}

function openTask(taskId: string) {
  void router.push({ name: "task-detail", params: { taskId } });
}

/**
 * 终态的中文标签（只给屏幕阅读器 / 页面内搜索用，视觉上不显示）。
 *
 * 为什么终态不显示徽标却仍要文字：
 * - 图标靠形状区分终态（✓/✕/⊖），视觉更干净，但读屏软件读不出 SVG 语义；
 * - 既有 E2E（m2-result-preview）按行文本含「已完成」来定位已完成任务。
 */
const TERMINAL_LABELS: Record<string, string> = {
  completed: "已完成",
  failed: "已失败",
  cancelled: "已取消",
};

function terminalLabel(status: string): string {
  return TERMINAL_LABELS[status] ?? status;
}

// 终态文字恢复可见后，色调必须仍按语义区分；用完整类名查表，避免 Tailwind 扫描丢失。
const TERMINAL_TONE_CLASS: Record<string, string> = {
  completed: "bg-success/7 text-success",
  failed: "bg-destructive/7 text-destructive",
  cancelled: "bg-muted text-muted-foreground",
};

function terminalToneClass(status: string): string {
  return TERMINAL_TONE_CLASS[status] ?? "bg-muted text-muted-foreground";
}

// ===== 汇总统计 =====

const counts = computed(() => {
  const items = store.items;
  return {
    needsMe: items.filter((item) => needsUserAction(item.status)).length,
    active: items.filter((item) => isActive(item.status)).length,
    done: items.filter((item) => item.status === "completed").length,
    total: items.length,
  };
});

const FILTERS = [
  { value: "all", label: "全部", key: "total" },
  { value: "needs-me", label: "待我确认", key: "needsMe" },
  { value: "active", label: "进行中", key: "active" },
  { value: "done", label: "已完成", key: "done" },
] as const;

// ===== 筛选 + 搜索 =====

const visibleTasks = computed<TaskSummary[]>(() => {
  const text = keyword.value.trim().toLowerCase();
  return store.items.filter((task) => {
    if (filter.value === "needs-me" && !needsUserAction(task.status)) {
      return false;
    }
    if (filter.value === "active" && !isActive(task.status)) {
      return false;
    }
    if (filter.value === "done" && task.status !== "completed") {
      return false;
    }
    // 搜索匹配整条标题（含被拆到次级标记里的后缀），用户按哪种都能搜到
    if (text && !task.title.toLowerCase().includes(text)) {
      return false;
    }
    return true;
  });
});

/** 是否处于「筛选/搜索后无结果」——区别于「真的一个任务都没有」。 */
const isFiltered = computed(() => filter.value !== "all" || keyword.value.trim().length > 0);

// 每行标题拆成两级（用户已确认的拆分方案）
const rows = computed(() =>
  visibleTasks.value.map((task) => ({ task, ...splitTaskTitle(task.title) })),
);
</script>

<template>
  <section>
    <header class="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div>
        <p class="mb-2 text-[10px] font-semibold tracking-[0.16em] text-primary uppercase">
          Research workspace
        </p>
        <h1 class="font-display text-3xl font-medium tracking-[-0.035em]">研究任务</h1>
        <p class="mt-2 text-sm text-muted-foreground">检索、核验与写作，都从证据开始。</p>
      </div>
      <Button data-test="new-task" @click="showForm = !showForm">
        <Plus :size="16" aria-hidden="true" />
        <span>{{ showForm ? "收起" : "新建任务" }}</span>
      </Button>
    </header>

    <ErrorBanner :message="errorMessage" />

    <TaskCreateForm
      v-if="showForm"
      class="mb-6"
      :pending="submitting"
      :error="errorMessage"
      @submit="handleCreate"
    />

    <!-- 轻量汇总带：保留全部真实计数与快捷筛选，不再用四张等宽卡重复抢占视线。 -->
    <div
      v-if="store.items.length"
      class="mb-5 grid grid-cols-2 gap-y-4 border-b border-border pb-5 sm:grid-cols-4"
    >
      <button
        type="button"
        data-test="stat-needs-me"
        class="flex items-baseline gap-2 border-r border-border pr-4 text-left"
        @click="filter = filter === 'needs-me' ? 'all' : 'needs-me'"
      >
        <span
          :class="[
            'font-display text-3xl leading-none tabular-nums',
            counts.needsMe > 0 ? 'text-warning' : 'text-muted-foreground',
          ]"
          >{{ counts.needsMe }}</span
        >
        <span class="text-xs text-muted-foreground">待我确认</span>
      </button>
      <button
        type="button"
        data-test="stat-active"
        class="flex items-baseline gap-2 border-border px-4 text-left sm:border-r"
        @click="filter = filter === 'active' ? 'all' : 'active'"
      >
        <span class="font-display text-3xl leading-none tabular-nums">{{ counts.active }}</span>
        <span class="text-xs text-muted-foreground">进行中</span>
      </button>
      <button
        type="button"
        data-test="stat-done"
        class="flex items-baseline gap-2 border-r border-border px-4 text-left"
        @click="filter = filter === 'done' ? 'all' : 'done'"
      >
        <span class="font-display text-3xl leading-none tabular-nums">{{ counts.done }}</span>
        <span class="text-xs text-muted-foreground">已完成</span>
      </button>
      <div data-test="stat-total" class="flex items-baseline gap-2 px-4 text-left">
        <span class="font-display text-3xl leading-none tabular-nums">{{ counts.total }}</span>
        <span class="text-xs text-muted-foreground">全部任务</span>
      </div>
    </div>

    <!-- 唯一需要用户行动的状态集中成一个入口；数量与行为都来自现有 filter/counts。 -->
    <button
      v-if="store.items.length"
      type="button"
      data-test="attention-needs-me"
      class="mb-6 flex w-full items-center gap-4 rounded-xl border border-primary/15 bg-accent/45 px-4 py-4 text-left transition-colors hover:bg-accent/70 sm:px-5"
      :aria-pressed="filter === 'needs-me'"
      @click="filter = filter === 'needs-me' ? 'all' : 'needs-me'"
    >
      <span class="min-w-10 font-display text-4xl leading-none text-primary tabular-nums">
        {{ counts.needsMe }}
      </span>
      <span class="min-w-0 flex-1">
        <span class="block text-sm font-semibold">
          {{ counts.needsMe > 0 ? `有 ${counts.needsMe} 项任务等待你的判断` : "当前没有待处理任务" }}
        </span>
        <span class="mt-1 block text-xs leading-5 text-muted-foreground">
          {{
            counts.needsMe > 0
              ? "确认规划或处理暂停原因后，任务才会继续推进。"
              : "可以从下方研究索引继续查看已有成果。"
          }}
        </span>
      </span>
      <span class="shrink-0 text-xs font-medium text-primary">
        {{ filter === "needs-me" ? "查看全部" : "查看待办" }} →
      </span>
    </button>

    <!-- 筛选 + 搜索 -->
    <div v-if="store.items.length" class="mb-4 flex flex-wrap items-center gap-2">
      <div class="flex flex-wrap gap-1" role="group" aria-label="按状态筛选">
        <button
          v-for="option in FILTERS"
          :key="option.value"
          type="button"
          :data-test="`filter-${option.value}`"
          :aria-pressed="filter === option.value"
          :class="[
            'relative px-1 py-1.5 text-xs font-medium transition-colors after:absolute after:right-0 after:-bottom-1 after:left-0 after:h-0.5 after:rounded-full',
            filter === option.value
              ? 'text-foreground after:bg-primary'
              : 'text-muted-foreground after:bg-transparent hover:text-foreground',
          ]"
          @click="filter = option.value"
        >
          {{ option.label }}
          <span class="ml-1 tabular-nums opacity-70">{{ counts[option.key] }}</span>
        </button>
      </div>

      <div class="relative ml-auto w-full sm:w-64">
        <Search
          :size="14"
          class="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <input
          v-model="keyword"
          data-test="task-search"
          type="search"
          placeholder="搜索标题…"
          aria-label="搜索任务标题"
          class="w-full rounded-md border border-input bg-card py-1.5 pr-2.5 pl-8 text-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring"
        />
      </div>
    </div>

    <p v-if="store.listLoading && !store.items.length" class="text-sm text-muted-foreground">
      加载中…
    </p>

    <!-- 真·空态（区别于筛选无结果） -->
    <EmptyState
      v-else-if="!store.items.length"
      testid="empty-state"
      title="还没有任务"
      description="新建一个文献综述任务，系统会先规划再执行，可随时确认与干预。"
    >
      <template #icon>
        <Plus class="mb-1 size-6 text-muted-foreground" aria-hidden="true" />
      </template>
    </EmptyState>

    <!-- 筛选/搜索无结果：给出「怎么回到有结果」的引导，不是空白表格 -->
    <div
      v-else-if="!rows.length"
      data-test="filtered-empty"
      class="rounded-lg border border-border bg-card px-4 py-10 text-center"
    >
      <p class="text-sm font-medium">没有符合条件的任务</p>
      <p class="mt-1 text-xs text-muted-foreground">
        {{
          filter === "needs-me"
            ? "当前没有需要你确认的任务，可以切回「全部」看看。"
            : "试试换个关键词，或切回「全部」。"
        }}
      </p>
      <Button
        variant="outline"
        size="sm"
        class="mt-3"
        data-test="reset-filters"
        @click="
          filter = 'all';
          keyword = '';
        "
      >
        清除筛选
      </Button>
    </div>

    <div v-else class="overflow-hidden rounded-xl border border-border bg-card/85">
      <!-- 桌面：表格。窄屏：卡片式（thead 隐藏，每行用 data-label 打标签） -->
      <table class="w-full border-collapse text-sm max-md:block">
        <thead class="max-md:hidden">
          <tr class="border-b border-border text-left">
            <th scope="col" class="px-3 py-2 text-xs font-medium text-muted-foreground">标题</th>
            <th scope="col" class="w-24 px-3 py-2 text-xs font-medium text-muted-foreground">
              状态
            </th>
            <th
              scope="col"
              class="w-32 px-3 py-2 text-xs font-medium text-muted-foreground max-lg:hidden"
            >
              更新时间
            </th>
            <th scope="col" class="w-8 px-2 py-2">
              <span class="sr-only">进入</span>
            </th>
          </tr>
        </thead>
        <tbody class="max-md:block">
          <tr
            v-for="row in rows"
            :key="row.task.task_id"
            :data-test="`task-row-${row.task.task_id}`"
            :class="[
              'group cursor-pointer border-y-0 border-r-0 border-b border-l-2 border-border last:border-b-0 hover:bg-muted/40 focus-within:bg-muted/40 max-md:block max-md:px-3 max-md:py-2.5',
              needsUserAction(row.task.status)
                ? 'border-l-warning bg-warning/5'
                : 'border-l-transparent',
            ]"
            @click="openTask(row.task.task_id)"
          >
            <!-- 标题：主题（主）+ 括号标记（次，灰字小号） -->
            <td
              class="px-3 py-2.5 max-md:block max-md:px-0 max-md:pt-0 max-md:pb-1"
              data-label="标题"
            >
              <div class="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span
                  data-test="task-title-main"
                  class="font-medium text-foreground"
                  :title="row.task.title"
                >
                  {{ row.main }}
                </span>
                <span
                  v-if="row.meta"
                  data-test="task-title-meta"
                  class="text-xs whitespace-nowrap text-muted-foreground"
                >
                  {{ row.meta }}
                </span>
              </div>
            </td>

            <!-- 状态：终态用形状图标 + 可见文字，长列表里不必只靠颜色猜测。
                 文字同时服务读屏、页面搜索与既有 E2E（m2-result-preview）。 -->
            <td
              class="px-3 py-2.5 max-md:flex max-md:items-center max-md:justify-between max-md:gap-3 max-md:px-0 max-md:py-1"
            >
              <span class="hidden text-xs text-muted-foreground max-md:inline">状态</span>
              <span class="inline-flex items-center gap-1.5">
                <span
                  v-if="isTerminal(row.task.status)"
                  :class="[
                    'inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs',
                    terminalToneClass(row.task.status),
                  ]"
                >
                  <TaskStatusIcon :status="row.task.status" />
                  <span data-test="terminal-status-label">{{ terminalLabel(row.task.status) }}</span>
                </span>
                <template v-else>
                  <TaskStatusIcon :status="row.task.status" />
                  <StatusBadge :status="row.task.status" />
                </template>
              </span>
            </td>

            <td
              class="px-3 py-2.5 text-xs text-muted-foreground max-lg:hidden"
              data-label="更新时间"
            >
              {{ formatDateTime(row.task.updated_at ?? row.task.created_at) }}
            </td>

            <!-- 行尾箭头：替代 20 个重复的「打开」按钮的外观。
                 进行中的任务额外显示细进度条——此时百分比才真的有意义。 -->
            <td class="px-2 py-2.5 max-md:block max-md:px-0 max-md:pt-1">
              <div class="flex items-center justify-end gap-2">
                <div
                  v-if="isActive(row.task.status)"
                  class="h-1 w-12 overflow-hidden rounded-full bg-secondary"
                  role="progressbar"
                  :aria-valuenow="taskProgress(row.task.status)"
                  aria-valuemin="0"
                  aria-valuemax="100"
                  :title="`${taskProgress(row.task.status)}%`"
                >
                  <div
                    class="h-full rounded-full bg-primary transition-[width] duration-300 motion-reduce:transition-none"
                    :style="{ width: `${taskProgress(row.task.status)}%` }"
                  />
                </div>
                <!-- 真正的按钮语义放这里：
                     E2E 用 `getByRole("button", { name: "打开" })` 定位（m2 系列的
                     既有契约），所以必须保留一个可访问名称是「打开」的按钮。
                     行本身可点，但**不再**是 role=button —— 否则同一行会出现
                     两个 button 角色，`getByRole` 命中多个导致测试二义性失败。 -->
                <button
                  type="button"
                  class="inline-flex size-10 items-center justify-center rounded-md text-muted-foreground/70 transition-colors hover:bg-muted hover:text-foreground group-hover:text-foreground"
                  aria-label="打开"
                  :title="`打开：${row.task.title}`"
                  @click.stop="openTask(row.task.task_id)"
                >
                  <ChevronRight :size="15" aria-hidden="true" />
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
