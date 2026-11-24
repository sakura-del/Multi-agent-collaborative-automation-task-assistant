<script setup lang="ts">
/**
 * 全局 AI 入口（Issue #8 P0/P1）：侧栏一句话创建综述任务。
 *
 * P0：展开态 BePromptInput 紧凑版（Enter 直提交）；折叠态 Sparkles 圆按钮。
 * P1：① 提交前**意图分类**（后端 /tasks/classify-intent，平台模型轻量调用，
 *     fail-open）——非综述请求就地提示；② 提交后**不跳页**，入口内显示
 *     进行中任务条（4s 轮询状态：规划中 → ✓ 已生成点击查看 / 失败），最多 3 条。
 *
 * 付费墙复用既有套餐门禁（创建端点顺序：凭据 → 篇数 → token）。
 */
import { computed, onBeforeUnmount, ref } from "vue";
import { useRouter } from "vue-router";
import { Sparkles, X } from "lucide-vue-next";

import BePromptInput from "@/components/motion/BePromptInput.vue";
import { taskApi } from "@/api/tasks";
import { classifyIntent } from "@/api/intent";
import {
  GLOBAL_PROMPT_PRESETS,
  getWorkflowPreset,
} from "@/config/workflow-presets";
import type { WorkflowPreset } from "@/api/types";

const props = defineProps<{ collapsed: boolean }>();
const emit = defineEmits<{ expand: [] }>();

const router = useRouter();
const topic = ref("");
/** 用户手动点选锁定的预设；null 表示交由 classifyIntent 自动识别。 */
const lockedPreset = ref<WorkflowPreset | null>(null);
const submitting = ref(false);
const error = ref<string | null>(null);

const activePlaceholder = computed(() =>
  lockedPreset.value
    ? getWorkflowPreset(lockedPreset.value).promptPlaceholder
    : getWorkflowPreset("literature_review").promptPlaceholder,
);

function togglePreset(presetId: WorkflowPreset) {
  lockedPreset.value = lockedPreset.value === presetId ? null : presetId;
}

/** 进行中任务条（最多保留 3 条，新的在前） */
interface ActiveTask {
  task_id: string;
  title: string;
  status: string;
  failed: boolean;
}
const activeTasks = ref<ActiveTask[]>([]);
let pollTimer: number | null = null;

const pendingCount = computed(
  () =>
    activeTasks.value.filter(
      (item) => !item.failed && item.status !== "waiting_approval"
    ).length
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
    // P1-2 不跳页：任务条进入队列并开始轮询状态
    activeTasks.value.unshift({
      task_id: created.task_id,
      title: intent.topic,
      status: created.status,
      failed: false,
    });
    if (activeTasks.value.length > 3) activeTasks.value.pop();
    startPolling();
  } catch (err) {
    error.value = (err as Error).message || "任务创建失败";
  } finally {
    submitting.value = false;
  }
}

const POLL_MS = 4000;

function startPolling() {
  if (pollTimer !== null) return;
  pollTimer = window.setInterval(pollOnce, POLL_MS);
  void pollOnce();
}

async function pollOnce() {
  const pending = activeTasks.value.filter(
    (item) => !item.failed && item.status !== "waiting_approval"
  );
  if (!pending.length) {
    stopPolling();
    return;
  }
  await Promise.all(
    pending.map(async (item) => {
      try {
        const snapshot = await taskApi.get(item.task_id);
        item.status = snapshot.status;
        if (snapshot.status === "failed" || snapshot.status === "waiting_human") {
          item.failed = true;
        }
      } catch {
        // 单条轮询失败静默：下一轮继续
      }
    })
  );
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

onBeforeUnmount(stopPolling);

function dismissTask(taskId: string) {
  activeTasks.value = activeTasks.value.filter((item) => item.task_id !== taskId);
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
      class="absolute -right-1 -top-1 grid size-4 place-items-center rounded-full bg-primary text-[9px] font-semibold text-primary-foreground"
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
    <ul class="mt-1.5 flex flex-col gap-1">
      <li v-for="item in activeTasks" :key="item.task_id">
        <div
          data-test="global-prompt-task"
          class="group flex items-center gap-1.5 rounded-lg border px-2 py-1.5 text-[11px] leading-4"
          :class="
            item.failed
              ? 'border-destructive/30 bg-destructive/5 text-destructive'
              : item.status === 'waiting_approval'
                ? 'cursor-pointer border-emerald-500/30 bg-emerald-500/5 text-emerald-700 hover:bg-emerald-500/10'
                : 'border-border bg-muted/30 text-muted-foreground'
          "
          @click="router.push(`/tasks/${item.task_id}`)"
        >
          <span class="min-w-0 flex-1 truncate" :title="item.title">
            <template v-if="item.failed">✗ {{ item.title }}（转人工）</template>
            <template v-else-if="item.status === 'waiting_approval'">
              ✓ 规划已生成 · 点击查看
            </template>
            <template v-else-if="item.status === 'planning' || item.status === 'created'">
              规划中… {{ item.title }}
            </template>
            <template v-else>{{ item.status }} · {{ item.title }}</template>
          </span>
          <button
            type="button"
            class="shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100"
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
