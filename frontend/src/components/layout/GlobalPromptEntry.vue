<script lang="ts">
import { ref } from "vue";

import { taskApi } from "@/api/tasks";
import type { TaskStatus } from "@/api/types";
import { needsUserAction } from "@/utils/task-status";

const STORAGE_KEY = "vp:global-prompt:active-tasks";
const MAX_STORED_TASKS = 3;
const POLL_MS = 4000;

/** 仅对已知的活跃推进态任务发起 4s 轮询，彻底屏蔽损坏或未知脏状态 */
const POLLABLE_STATUSES = new Set<string>([
  "created",
  "planning",
  "running",
  "retrying",
  "paused",
  "reviewing",
]);

/** 进行中任务条（最多保留 3 条，新的在前） */
interface ActiveTask {
  task_id: string;
  title: string;
  status: string;
}

const sharedActiveTasks = ref<ActiveTask[]>([]);
let pollTimer: number | null = null;
let mountedCount = 0;
let hasHydratedFromStorage = false;
/** 生命周期代次令牌：末次实例卸载归零时递增，拦截跨卸载-重建边界的过期轮询回包 */
let sessionEpoch = 0;

function isTaskPollable(status: string): boolean {
  return POLLABLE_STATUSES.has(status);
}

function isPriorityTask(task: ActiveTask): boolean {
  return isTaskPollable(task.status) || needsUserAction(task.status as TaskStatus);
}

/**
 * 截断至最多 3 条：
 * 1) 保证最新提交的头部任务在列；
 * 2) 优先保留活跃态/待操作任务，避免老运行任务被已完成任务挤掉；
 * 3) 剩余槽位按从新到旧补齐并保持原有先后顺序。
 */
function trimActiveTasks(tasks: ActiveTask[]): ActiveTask[] {
  if (tasks.length <= MAX_STORED_TASKS) return tasks;

  const selected = new Set<number>([0]);
  for (let i = 1; i < tasks.length && selected.size < MAX_STORED_TASKS; i += 1) {
    if (isPriorityTask(tasks[i])) {
      selected.add(i);
    }
  }
  for (let i = 1; i < tasks.length && selected.size < MAX_STORED_TASKS; i += 1) {
    if (!selected.has(i)) {
      selected.add(i);
    }
  }
  return tasks.filter((_, idx) => selected.has(idx));
}

function loadStoredTasks(): ActiveTask[] {
  if (typeof sessionStorage === "undefined") return [];
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const valid = parsed.filter(
      (item): item is ActiveTask =>
        Boolean(item) &&
        typeof item.task_id === "string" &&
        typeof item.title === "string" &&
        typeof item.status === "string",
    );
    return trimActiveTasks(valid);
  } catch {
    return [];
  }
}

function saveStoredTasks(tasks: ActiveTask[]) {
  if (typeof sessionStorage === "undefined") return;
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(trimActiveTasks(tasks)));
  } catch {
    // 隐私模式或配额满时静默降级
  }
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function pollOnce() {
  const epoch = sessionEpoch;
  const pending = sharedActiveTasks.value.filter((item) => isTaskPollable(item.status));
  if (!pending.length) {
    stopPolling();
    return;
  }
  await Promise.all(
    pending.map(async (item) => {
      try {
        const snapshot = await taskApi.get(item.task_id);
        if (epoch !== sessionEpoch || mountedCount === 0) return;
        const target = sharedActiveTasks.value.find((t) => t.task_id === item.task_id);
        if (target) {
          target.status = snapshot.status;
        }
      } catch {
        // 单条轮询失败静默：下一轮继续
      }
    }),
  );
  if (epoch !== sessionEpoch || mountedCount === 0) return;
  saveStoredTasks(sharedActiveTasks.value);
  if (!sharedActiveTasks.value.some((item) => isTaskPollable(item.status))) {
    stopPolling();
  }
}

function startPolling() {
  if (pollTimer !== null) return;
  pollTimer = window.setInterval(pollOnce, POLL_MS);
  void pollOnce();
}

function addTask(task: ActiveTask) {
  const deduped = sharedActiveTasks.value.filter((item) => item.task_id !== task.task_id);
  sharedActiveTasks.value = trimActiveTasks([task, ...deduped]);
  saveStoredTasks(sharedActiveTasks.value);
  if (isTaskPollable(task.status)) {
    startPolling();
  }
}

function removeTask(taskId: string) {
  sharedActiveTasks.value = sharedActiveTasks.value.filter((item) => item.task_id !== taskId);
  saveStoredTasks(sharedActiveTasks.value);
  if (!sharedActiveTasks.value.some((item) => isTaskPollable(item.status))) {
    stopPolling();
  }
}
</script>

<script setup lang="ts">
/**
 * 全局 AI 入口（Issue #8 P0/P1）：侧栏一句话创建综述任务。
 *
 * P0：展开态 BePromptInput 紧凑版（Enter 直提交）；折叠态 Sparkles 圆按钮。
 * P1：① 提交前意图分类（fail-open）——非综述请求就地提示；② 提交后不跳页，
 * 入口内显示进行中任务条（4s 轮询，按状态精确分流并持久化至 sessionStorage，最多 3 条）。
 */
import { computed, onBeforeUnmount, onMounted } from "vue";
import { useRouter } from "vue-router";
import { Sparkles, X } from "lucide-vue-next";

import BePromptInput from "@/components/motion/BePromptInput.vue";
import { classifyIntent } from "@/api/intent";
import {
  GLOBAL_PROMPT_PRESETS,
  getWorkflowPreset,
} from "@/config/workflow-presets";
import type { WorkflowPreset } from "@/api/types";
import { taskStatusLabel } from "@/utils/task-status";

const props = defineProps<{ collapsed: boolean }>();
const emit = defineEmits<{ expand: []; navigate: [] }>();

const router = useRouter();
const topic = ref("");
/** 用户手动点选锁定的预设；null 表示交由 classifyIntent 自动识别。 */
const lockedPreset = ref<WorkflowPreset | null>(null);
const submitting = ref(false);
const error = ref<string | null>(null);

if (mountedCount === 0 && !hasHydratedFromStorage) {
  sharedActiveTasks.value = loadStoredTasks();
  hasHydratedFromStorage = true;
}

const activeTasks = sharedActiveTasks;

const activePlaceholder = computed(() =>
  lockedPreset.value
    ? getWorkflowPreset(lockedPreset.value).promptPlaceholder
    : getWorkflowPreset("literature_review").promptPlaceholder,
);

function togglePreset(presetId: WorkflowPreset) {
  lockedPreset.value = lockedPreset.value === presetId ? null : presetId;
}

const pendingCount = computed(
  () => activeTasks.value.filter((item) => isTaskPollable(item.status)).length,
);

async function submit(value: string) {
  const text = value.trim();
  if (!text || submitting.value) return;
  submitting.value = true;
  error.value = null;
  try {
    // P1-1 意图分类（fail-open：后端不可用时回落 literature_review）
    const intent = await classifyIntent(text);
    if (intent.intent !== "literature_review") {
      error.value = "当前入口只支持文献综述任务；其他自动化能力在路上，敬请期待。";
      return;
    }
    const resolvedPreset = getWorkflowPreset(
      lockedPreset.value ?? intent.workflow_preset ?? "literature_review",
    );
    const created = await taskApi.create({
      topic: intent.topic,
      keywords: intent.keywords.length ? intent.keywords : [text.slice(0, 30)],
      workflow_preset: resolvedPreset.id,
      max_documents: resolvedPreset.defaults.maxDocuments,
      verify_references: resolvedPreset.defaults.verifyReferences,
    });
    topic.value = "";
    // P1-2 不跳页：任务条进入队列并持久化、开始轮询状态
    addTask({
      task_id: created.task_id,
      title: intent.topic,
      status: created.status,
    });
  } catch (err) {
    error.value = (err as Error).message || "任务创建失败";
  } finally {
    submitting.value = false;
  }
}

onMounted(() => {
  if (mountedCount === 0 && !hasHydratedFromStorage) {
    sharedActiveTasks.value = loadStoredTasks();
    hasHydratedFromStorage = true;
  }
  mountedCount += 1;
  if (sharedActiveTasks.value.some((item) => isTaskPollable(item.status))) {
    startPolling();
  }
});

onBeforeUnmount(() => {
  mountedCount = Math.max(0, mountedCount - 1);
  if (mountedCount === 0) {
    sessionEpoch += 1;
    stopPolling();
    sharedActiveTasks.value = [];
    hasHydratedFromStorage = false;
  }
});

function dismissTask(taskId: string) {
  removeTask(taskId);
}

function navigateToTask(taskId: string) {
  emit("navigate");
  void router.push(`/tasks/${taskId}`);
}

function taskRowClass(status: string): string {
  if (status === "failed") {
    return "border-destructive/30 bg-destructive/5 text-destructive";
  }
  if (status === "waiting_human") {
    return "cursor-pointer border-warning/35 bg-warning/10 text-warning hover:bg-warning/15";
  }
  if (status === "waiting_approval") {
    return "cursor-pointer border-emerald-500/30 bg-emerald-500/5 text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-400";
  }
  if (status === "completed") {
    return "cursor-pointer border-border bg-muted/30 text-foreground hover:bg-muted/50";
  }
  return "cursor-pointer border-border bg-muted/30 text-muted-foreground hover:bg-muted/50";
}
</script>

<template>
  <!-- 折叠态：Sparkles 圆按钮（有进行中任务时角标计数） -->
  <button
    v-if="props.collapsed"
    type="button"
    data-test="global-prompt-collapsed"
    class="relative mx-auto mb-1 mt-1 flex size-9 items-center justify-center rounded-xl border border-border text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    title="一句话开始一篇综述"
    aria-label="一句话开始一篇综述"
    @click="emit('expand')"
  >
    <Sparkles class="size-4" />
    <span
      v-if="pendingCount"
      data-test="global-prompt-badge"
      class="absolute -right-1 -top-1 grid size-4 place-items-center rounded-full bg-primary text-[9px] font-semibold tabular-nums text-primary-foreground"
    >
      {{ pendingCount }}
    </span>
  </button>

  <!-- 展开态：预设 Chip + 紧凑输入条 + 进行中任务条 -->
  <div v-else class="px-2 pb-1 pt-1">
    <div
      class="mb-1.5 flex flex-wrap items-center gap-1"
      role="group"
      aria-label="选择工作流预设"
    >
      <button
        v-for="preset in GLOBAL_PROMPT_PRESETS"
        :key="preset.id"
        type="button"
        :data-test="`global-preset-${preset.id}`"
        :aria-pressed="lockedPreset === preset.id"
        :class="[
          'rounded-full border px-2 py-0.5 text-[10px] font-medium transition-colors',
          lockedPreset === preset.id
            ? 'border-primary bg-primary/10 text-primary'
            : 'border-border bg-background text-muted-foreground hover:text-foreground',
        ]"
        @click="togglePreset(preset.id)"
      >
        {{ preset.shortLabel }}
      </button>
    </div>
    <BePromptInput
      v-model="topic"
      data-test="global-prompt"
      :placeholder="activePlaceholder"
      hint="Enter 创建任务"
      min-height="3.5rem"
      :submit-on-enter="true"
      compact
      :submitting="submitting"
      class="text-xs"
      @submit="submit"
    />
    <p
      v-if="error"
      data-test="global-prompt-error"
      class="mt-1 truncate px-1 text-[11px] leading-4 text-destructive"
      :title="error"
    >
      {{ error }}
    </p>

    <!-- 进行中任务条（P1-2 流式回显） -->
    <ul v-if="activeTasks.length" class="mt-1.5 flex flex-col gap-1">
      <li v-for="item in activeTasks" :key="item.task_id">
        <div
          data-test="global-prompt-task"
          class="group flex items-center gap-1.5 rounded-lg border px-2 py-1.5 text-[11px] leading-4 transition-colors"
          :class="taskRowClass(item.status)"
          @click="navigateToTask(item.task_id)"
        >
          <span class="min-w-0 flex-1 truncate" :title="item.title">
            <template v-if="item.status === 'failed'">✗ {{ item.title }}（执行失败）</template>
            <template v-else-if="item.status === 'waiting_human'">! {{ item.title }}（待人工介入）</template>
            <template v-else-if="item.status === 'waiting_approval'">
              ✓ 规划已生成 · 点击查看
            </template>
            <template v-else-if="item.status === 'completed'">✓ {{ item.title }}（已完成）</template>
            <template v-else-if="item.status === 'cancelled'">— {{ item.title }}（已终止）</template>
            <template v-else-if="item.status === 'planning' || item.status === 'created'">
              规划中… {{ item.title }}
            </template>
            <template v-else>{{ taskStatusLabel(item.status) }}… {{ item.title }}</template>
          </span>
          <button
            type="button"
            class="shrink-0 rounded p-0.5 text-muted-foreground opacity-75 transition-opacity hover:text-foreground focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100"
            aria-label="移除"
            @click.stop="dismissTask(item.task_id)"
          >
            <X class="size-3" />
          </button>
        </div>
      </li>
    </ul>
  </div>
</template>
