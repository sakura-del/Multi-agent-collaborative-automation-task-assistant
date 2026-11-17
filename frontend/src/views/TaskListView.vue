<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { ChevronRight, Plus, Search } from "lucide-vue-next";

import BeTimeline from "@/components/motion/BeTimeline.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import StatusBadge from "@/components/common/StatusBadge.vue";
import TaskCreateForm from "@/components/tasks/TaskCreateForm.vue";
import TaskStatusIcon from "@/components/tasks/TaskStatusIcon.vue";
import { Button } from "@/components/ui/button";
import { Pagination } from "@/components/ui/pagination";
import { useTaskStore } from "@/stores/tasks";
import type { TaskStatusGroup } from "@/api/tasks";
import type { TaskCreate, TaskStatus, TaskSummary } from "@/api/types";
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
  void refresh(1);
});

/** 视图筛选值 → 后端状态分组（写法不同：needs-me vs needs_me）。 */
const GROUP_MAP: Record<"all" | "needs-me" | "active" | "done", TaskStatusGroup> = {
  all: "all",
  "needs-me": "needs_me",
  active: "active",
  done: "done",
};

/**
 * 拉取某一页 + 同步统计。
 *
 * 筛选与搜索都走服务端：分页之后本地筛选只能筛当前 20 条，
 * 「待我确认」会漏掉第 2 页之后的待办，搜索也搜不到。
 */
async function refresh(page = 1, query = keyword.value) {
  errorMessage.value = null;
  try {
    await Promise.all([
      store.loadList(page, { group: GROUP_MAP[filter.value], query }),
      store.loadCounts(query),
    ]);
  } catch (error) {
    errorMessage.value = (error as Error).message;
  }
}

// 切筛选：回到第 1 页并保留当前搜索词
watch(filter, () => {
  void refresh(1);
});

// 搜索防抖：每敲一个字都请求会在慢网下把结果打乱（后发先至）
let searchTimer: ReturnType<typeof setTimeout> | undefined;
watch(keyword, (value) => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    void refresh(1, value);
  }, 300);
});
onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer);
});

const totalPages = computed(() => Math.max(1, Math.ceil(store.listTotal / store.PAGE_SIZE)));

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

// ===== 时间线（beUI 风格，最近 8 个任务）=====

const TIMELINE_STATUS_LABELS: Record<string, string> = {
  created: "已创建",
  planning: "规划中",
  waiting_approval: "待确认",
  running: "执行中",
  retrying: "重试中",
  paused: "已暂停",
  reviewing: "复盘中",
  waiting_human: "待人工",
  completed: "已完成",
  failed: "已失败",
  cancelled: "已取消",
};

/** 完整类名查表，避免 Tailwind 扫描丢失（同 TERMINAL_TONE_CLASS 的做法）。 */
const TIMELINE_TONE_CLASS: Record<string, string> = {
  running: "bg-primary/8 text-primary",
  planning: "bg-muted text-muted-foreground",
  created: "bg-muted text-muted-foreground",
  reviewing: "bg-primary/8 text-primary",
  waiting_approval: "bg-warning/12 text-warning",
  waiting_human: "bg-warning/12 text-warning",
  retrying: "bg-warning/12 text-warning",
  paused: "bg-muted text-muted-foreground",
  completed: "bg-success/10 text-success",
  failed: "bg-destructive/10 text-destructive",
  cancelled: "bg-muted text-muted-foreground",
};

function timelineStatusLabel(status: string): string {
  return TIMELINE_STATUS_LABELS[status] ?? status;
}

function timelineToneClass(status: string): string {
  return TIMELINE_TONE_CLASS[status] ?? "bg-muted text-muted-foreground";
}

function timelineBarClass(status: TaskStatus): string {
  if (status === "completed") return "bg-success";
  if (status === "failed") return "bg-destructive";
  if (needsUserAction(status)) return "bg-warning";
  if (isTerminal(status)) return "bg-muted-foreground/40";
  return "bg-primary";
}

function timelineTask(id: string): TaskSummary | undefined {
  return store.items.find((task) => task.task_id === id);
}

/** 最近 8 个任务，按创建时间升序铺进时间线（左旧右新）。 */
const timelineTasks = computed(() =>
  store.items
    .filter((task) => task.created_at)
    .slice()
    .sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""))
    .slice(-8),
);

// ===== 汇总统计 =====

/** 顶部四个数字来自服务端全量统计，不是当前页条数。 */
const counts = computed(() => store.counts);

const FILTERS = [
  { value: "all", label: "全部", key: "all" },
  { value: "needs-me", label: "待我确认", key: "needs_me" },
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
            counts.needs_me > 0 ? 'text-warning' : 'text-muted-foreground',
          ]"
          >{{ counts.needs_me }}</span
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
        <span class="font-display text-3xl leading-none tabular-nums">{{ counts.all }}</span>
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
        {{ counts.needs_me }}
      </span>
      <span class="min-w-0 flex-1">
        <span class="block text-sm font-semibold">
          {{ counts.needs_me > 0 ? `有 ${counts.needs_me} 项任务等待你的判断` : "当前没有待处理任务" }}
        </span>
        <span class="mt-1 block text-xs leading-5 text-muted-foreground">
          {{
            counts.needs_me > 0
              ? "确认规划或处理暂停原因后，任务才会继续推进。"
              : "可以从下方研究索引继续查看已有成果。"
          }}
        </span>
      </span>
      <span class="shrink-0 text-xs font-medium text-primary">
        {{ filter === "needs-me" ? "查看全部" : "查看待办" }} →
      </span>
    </button>

    <!-- beUI 时间线：最近 8 个任务（左旧右新）。点击进详情；列表保留完整浏览与 E2E 契约。 -->
    <div v-if="timelineTasks.length && store.listPage === 1"
      class="mb-8 rounded-3xl border border-border bg-card p-2">
      <p class="px-4 pt-2 pb-1 text-xs text-muted-foreground">
        最近 {{ timelineTasks.length }} 个任务 · 点击卡片打开，拖拽 / 滚轮 / ←→ 浏览
      </p>
      <BeTimeline
        :events="
          timelineTasks.map((task) => ({
            id: task.task_id,
            date: task.created_at as string,
            title: task.title,
          }))
        "
        :height="400"
        :item-width="320"
        aria-label="最近任务时间线"
        @activate="openTask"
      >
        <template #media="{ event }">
          <div
            v-if="timelineTask(event.id)"
            class="flex h-full w-full flex-col justify-between p-3"
            :class="timelineToneClass(timelineTask(event.id)!.status)"
          >
            <div class="flex items-center justify-between text-xs font-medium">
              <span>{{ timelineStatusLabel(timelineTask(event.id)!.status) }}</span>
              <span class="tabular-nums">
                {{ taskProgress(timelineTask(event.id)!.status) }}%
              </span>
            </div>
            <div>
              <p class="text-[11px] font-medium tracking-wide uppercase opacity-80">研究任务</p>
              <div class="mt-1.5 h-1.5 rounded-full bg-foreground/10">
                <div
                  class="h-full rounded-full"
                  :class="timelineBarClass(timelineTask(event.id)!.status)"
                  :style="{ width: `${taskProgress(timelineTask(event.id)!.status)}%` }"
                />
              </div>
            </div>
          </div>
        </template>
        <template #meta="{ event }">
          <span>{{ formatDateTime(timelineTask(event.id)?.updated_at) }}</span>
        </template>
      </BeTimeline>
    </div>

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

    <!-- 分页：后端默认只回 20 条，没有它第 21 个任务永远看不到 -->
    <div
      v-if="store.listTotal > store.PAGE_SIZE"
      class="mt-4 flex flex-wrap items-center justify-between gap-2"
    >
      <span class="text-xs text-muted-foreground" data-test="task-page-summary">
        共 {{ store.listTotal }} 个任务 · 第 {{ store.listPage }} / {{ totalPages }} 页
      </span>
      <Pagination
        :model-value="store.listPage"
        :total-pages="totalPages"
        aria-label="任务分页"
        @update:model-value="refresh"
      />
    </div>
  </section>
</template>
