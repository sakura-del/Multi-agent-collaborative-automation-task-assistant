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

/** 行键盘可达：Enter / Space 等价于点击（无障碍硬要求）。 */
function handleRowKeydown(event: KeyboardEvent, taskId: string) {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    openTask(taskId);
  }
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
        <h1 class="text-xl font-semibold tracking-tight">任务</h1>
        <p class="mt-1 text-sm text-muted-foreground">
          共 {{ counts.total }} 个任务<template v-if="counts.needsMe > 0">
            ，其中
            <span class="font-medium text-warning">{{ counts.needsMe }} 个待你确认</span>
          </template>
        </p>
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

    <!-- 汇总卡：四项计数。第一项「待我确认」用警示色强调，因为它是唯一
         需要用户动手的状态；其余三项是中性信息。 -->
    <div v-if="store.items.length" class="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
      <button
        type="button"
        data-test="stat-needs-me"
        :class="[
          'rounded-lg border p-3 text-left transition-colors',
          counts.needsMe > 0
            ? 'border-warning/30 bg-warning/8 hover:bg-warning/12'
            : 'border-border bg-card hover:bg-muted/40',
        ]"
        @click="filter = filter === 'needs-me' ? 'all' : 'needs-me'"
      >
        <p class="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          待我确认
        </p>
        <p
          :class="[
            'mt-1 text-2xl font-semibold tabular-nums',
            counts.needsMe > 0 ? 'text-warning' : 'text-muted-foreground',
          ]"
        >
          {{ counts.needsMe }}
        </p>
      </button>

      <button
        type="button"
        data-test="stat-active"
        :class="[
          'rounded-lg border border-border bg-card p-3 text-left transition-colors hover:bg-muted/40',
        ]"
        @click="filter = filter === 'active' ? 'all' : 'active'"
      >
        <p class="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          进行中
        </p>
        <p class="mt-1 text-2xl font-semibold text-foreground tabular-nums">{{ counts.active }}</p>
      </button>

      <button
        type="button"
        data-test="stat-done"
        :class="[
          'rounded-lg border border-border bg-card p-3 text-left transition-colors hover:bg-muted/40',
        ]"
        @click="filter = filter === 'done' ? 'all' : 'done'"
      >
        <p class="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          已完成
        </p>
        <p class="mt-1 text-2xl font-semibold text-foreground tabular-nums">{{ counts.done }}</p>
      </button>

      <div data-test="stat-total" class="rounded-lg border border-border bg-card p-3 text-left">
        <p class="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">全部</p>
        <p class="mt-1 text-2xl font-semibold text-foreground tabular-nums">{{ counts.total }}</p>
      </div>
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
            'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
            filter === option.value
              ? 'bg-foreground text-background'
              : 'bg-muted text-muted-foreground hover:text-foreground',
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

    <div v-else class="overflow-hidden rounded-lg border border-border bg-card">
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
            role="button"
            tabindex="0"
            :aria-label="`打开任务：${row.task.title}`"
            class="group cursor-pointer border-b border-border last:border-b-0 hover:bg-muted/40 focus-visible:bg-muted/40 max-md:block max-md:px-3 max-md:py-2.5"
            @click="openTask(row.task.task_id)"
            @keydown="handleRowKeydown($event, row.task.task_id)"
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

            <!-- 状态：图标（形状区分终态）+ 文字徽标 -->
            <td class="px-3 py-2.5 max-md:flex max-md:items-center max-md:justify-between max-md:gap-3 max-md:px-0 max-md:py-1">
              <span class="hidden text-xs text-muted-foreground max-md:inline">状态</span>
              <span class="inline-flex items-center gap-1.5">
                <TaskStatusIcon :status="row.task.status" />
                <StatusBadge :status="row.task.status" />
              </span>
            </td>

            <td
              class="px-3 py-2.5 text-xs text-muted-foreground max-lg:hidden"
              data-label="更新时间"
            >
              {{ formatDateTime(row.task.updated_at ?? row.task.created_at) }}
            </td>

            <!-- 行尾箭头：hover 才明显（替代 20 个重复的「打开」按钮）。
                 进行中的任务额外显示细进度条——此时百分比才真的有意义。 -->
            <td class="px-2 py-2.5 max-md:hidden">
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
                <ChevronRight
                  :size="15"
                  class="text-muted-foreground/40 transition-colors group-hover:text-foreground"
                  aria-hidden="true"
                />
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
