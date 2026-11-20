<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { Clock, Loader2, Mail, RefreshCw } from "lucide-vue-next";

import { taskApi } from "@/api/tasks";
import type { TaskRefreshRun, TaskRoutine } from "@/api/types";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/utils/task-status";
import { renderMarkdown } from "@/utils/markdown";

// 持续更新（Issue #27）：手动「再次检索新文献」+ 定时重跑排期 + 历史摘要。
//
// ## 为什么放在页头下方而不是某个 tab 里
//
// 它是**任务级**能力（对同一篇综述持续补证据），不属于「结果」或「日志」任何
// 一个 tab。放到 tab 里意味着用户切走就看不见「上一次刷新是什么时候」，
// 而这恰恰是判断「这篇综述还新不新鲜」的唯一线索。
//
// ## 为什么刷新结果要轮询而不是等 WS 事件
//
// 刷新是任务的旁路动作（不改任务状态），现有 WS 只推任务状态/节点事件。
// 为它伪造一条 task_status 事件会污染状态机语义；一次刷新通常 10–60 秒，
// 3 秒轮询足够，且离开页面就自动停。
const props = defineProps<{ taskId: string; status: string }>();

const POLL_INTERVAL_MS = 3000;
const MAX_POLLS = 40; // 2 分钟，超过就停（避免页面挂着无限打接口）

const routine = ref<TaskRoutine | null>(null);
const runs = ref<TaskRefreshRun[]>([]);
const errorMessage = ref<string | null>(null);
const loading = ref(true);
const starting = ref(false);
const savingRoutine = ref(false);
const expandedRunId = ref<string | null>(null);
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let polls = 0;

const isCompleted = computed(() => props.status === "completed");
const refreshing = computed(() => runs.value.some((run) => run.status === "running"));
const visibleRuns = computed(() => runs.value.slice(0, 5));
const hasNew = computed(() => (runs.value[0]?.new_count ?? 0) > 0);

function stopPolling() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

async function loadRuns() {
  runs.value = await taskApi.refreshes(props.taskId, 10);
}

async function loadAll() {
  loading.value = true;
  errorMessage.value = null;
  try {
    const [list, saved] = await Promise.all([
      taskApi.refreshes(props.taskId, 10),
      taskApi.routine(props.taskId),
    ]);
    runs.value = list;
    routine.value = saved;
    if (list.some((run) => run.status === "running")) {
      polls = 0;
      schedulePoll();
    }
  } catch (error) {
    errorMessage.value = (error as Error).message;
  } finally {
    loading.value = false;
  }
}

function schedulePoll() {
  stopPolling();
  if (polls >= MAX_POLLS) return;
  pollTimer = setTimeout(async () => {
    polls += 1;
    try {
      await loadRuns();
    } catch {
      // 轮询失败不打扰用户：下一轮再试
    }
    if (refreshing.value) {
      schedulePoll();
    } else {
      // 刷新结束后把排期的「下次运行」也刷新一下（last_run_at 变了）
      taskApi
        .routine(props.taskId)
        .then((value) => {
          routine.value = value;
        })
        .catch(() => undefined);
    }
  }, POLL_INTERVAL_MS);
}

async function startRefresh() {
  starting.value = true;
  errorMessage.value = null;
  try {
    const created = await taskApi.startRefresh(props.taskId);
    runs.value = [created, ...runs.value];
    polls = 0;
    schedulePoll();
  } catch (error) {
    errorMessage.value = (error as Error).message;
  } finally {
    starting.value = false;
  }
}

async function saveRoutine(patch: Partial<Pick<TaskRoutine, "enabled" | "cadence" | "notify">>) {
  savingRoutine.value = true;
  errorMessage.value = null;
  const current = routine.value;
  try {
    routine.value = await taskApi.saveRoutine(props.taskId, {
      enabled: patch.enabled ?? current?.enabled ?? true,
      cadence: patch.cadence ?? current?.cadence ?? "weekly",
      notify: patch.notify ?? current?.notify ?? "email",
    });
  } catch (error) {
    errorMessage.value = (error as Error).message;
  } finally {
    savingRoutine.value = false;
  }
}

function toggleEnabled(event: Event) {
  const checked = (event.target as HTMLInputElement).checked;
  void saveRoutine({ enabled: checked });
}

function changeCadence(event: Event) {
  const value = (event.target as HTMLSelectElement).value as TaskRoutine["cadence"];
  void saveRoutine({ cadence: value });
}

function toggleNotify(event: Event) {
  const checked = (event.target as HTMLInputElement).checked;
  void saveRoutine({ notify: checked ? "email" : "none" });
}

function toggleRun(run: TaskRefreshRun) {
  expandedRunId.value = expandedRunId.value === run.id ? null : run.id;
}

const CADENCE_LABEL: Record<string, string> = { weekly: "每周", monthly: "每月" };

/** 一行状态文案：新增几篇 / 无新增 / 失败 / 进行中。 */
function runSummary(run: TaskRefreshRun): string {
  if (run.status === "running") return "正在检索…";
  if (run.status === "failed") return run.error ? `失败：${run.error}` : "失败";
  if (run.new_count === 0) return `本期无新增（命中 ${run.hits_total}，重复 ${run.duplicate_count}）`;
  return `新增 ${run.new_count} 篇（命中 ${run.hits_total}，重复 ${run.duplicate_count}）`;
}

onMounted(loadAll);
onBeforeUnmount(stopPolling);
</script>

<template>
  <section
    v-if="isCompleted"
    data-test="task-refresh"
    class="mb-4 rounded-xl border border-border bg-card/70 p-4"
  >
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div class="min-w-0">
        <p class="text-[9px] font-semibold tracking-[0.16em] text-primary uppercase">
          Keep it fresh
        </p>
        <h2 class="mt-1 font-display text-base font-medium">持续更新</h2>
        <p class="mt-1 text-xs text-muted-foreground">
          只检索上次之后的新文献，生成增量摘要，不改动已完成的正文。
        </p>
      </div>
      <Button
        data-test="refresh-now"
        size="sm"
        :disabled="starting || refreshing || loading"
        @click="startRefresh"
      >
        <Loader2 v-if="starting || refreshing" class="animate-spin" aria-hidden="true" />
        <RefreshCw v-else :size="14" aria-hidden="true" />
        <span>{{ refreshing ? "检索中…" : "再次检索新文献" }}</span>
      </Button>
    </div>

    <ErrorBanner :message="errorMessage" class="mt-3" />

    <!-- 排期：开关 + 频率 + 是否邮件汇报 -->
    <div
      class="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm"
      data-test="routine-controls"
    >
      <label class="inline-flex items-center gap-2">
        <input
          type="checkbox"
          data-test="routine-enabled"
          class="size-3.5 accent-primary"
          :checked="routine?.enabled ?? false"
          :disabled="savingRoutine"
          @change="toggleEnabled"
        />
        <span>定时重跑</span>
      </label>

      <label class="inline-flex items-center gap-2 text-xs text-muted-foreground">
        频率
        <select
          data-test="routine-cadence"
          class="h-7 rounded-md border border-border bg-card px-1.5 text-xs"
          :value="routine?.cadence ?? 'weekly'"
          :disabled="savingRoutine || !routine?.enabled"
          @change="changeCadence"
        >
          <option v-for="(label, value) in CADENCE_LABEL" :key="value" :value="value">
            {{ label }}
          </option>
        </select>
      </label>

      <label class="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <input
          type="checkbox"
          data-test="routine-notify"
          class="size-3.5 accent-primary"
          :checked="routine?.notify === 'email'"
          :disabled="savingRoutine || !routine?.enabled"
          @change="toggleNotify"
        />
        <Mail :size="12" aria-hidden="true" />
        有新证据时邮件通知
      </label>

      <span
        v-if="routine?.enabled"
        data-test="routine-next"
        class="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground"
      >
        <Clock :size="12" aria-hidden="true" />
        下次：{{ formatDateTime(routine.next_run_at) }}
      </span>
    </div>

    <!-- 历史：默认只展开最近一次，其余点开才看全文 -->
    <ul v-if="visibleRuns.length" class="mt-3 flex flex-col gap-2" data-test="refresh-runs">
      <li
        v-for="run in visibleRuns"
        :key="run.id"
        class="rounded-lg border border-border/70 bg-background/60 px-3 py-2"
        :data-test="`refresh-run-${run.status}`"
      >
        <button
          type="button"
          class="flex w-full flex-wrap items-center gap-2 text-left text-xs"
          @click="toggleRun(run)"
        >
          <span class="font-medium">{{ runSummary(run) }}</span>
          <span class="text-muted-foreground">
            {{ formatDateTime(run.finished_at ?? run.started_at) }}
          </span>
          <span class="rounded-full border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">
            {{ run.trigger === "schedule" ? "定时" : "手动" }}
          </span>
          <span v-if="run.status === 'completed' && !run.citation_ok" class="text-[10px] text-warning">
            摘要已回落为清单
          </span>
          <span class="ml-auto text-muted-foreground">{{ expandedRunId === run.id ? "收起" : "展开" }}</span>
        </button>

        <!-- 本轮摘要（Markdown 渲染，renderMarkdown 内部已消毒） -->
        <div
          v-if="expandedRunId === run.id || (run === runs[0] && hasNew)"
          class="md-body mt-2 border-t border-border/60 pt-2 text-sm"
          data-test="refresh-summary"
          v-html="renderMarkdown(run.summary_markdown ?? '')"
        />
      </li>
    </ul>
  </section>
</template>
