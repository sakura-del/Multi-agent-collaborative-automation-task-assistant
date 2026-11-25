<script setup lang="ts">
import { computed } from "vue";
import { Brain, CircleAlert, Clock3, Repeat2, X } from "lucide-vue-next";

import StatusBadge from "@/components/common/StatusBadge.vue";
import { Button } from "@/components/ui/button";
import type { PlanNode, SubtaskSnapshot } from "@/api/types";
import { formatDateTime } from "@/utils/task-status";

// 节点详情侧栏：beUI Trace Waterfall 的 **span 详情面板**复刻
// （https://asharca.github.io/ui/components/agents/agent-traces/ —— 详情侧栏：
//   Timing 条 + 状态徽章 + 错误 destructive + Attributes 列表 + tabular-nums）。
// 数据保持宿主持有：node + subtask 由 TaskDetailView 传入。
//
// ⚠️ 测试契约（不要改）：data-test="node-detail"。

const props = defineProps<{
  node: PlanNode | null;
  subtask?: SubtaskSnapshot | null;
  /** 全部子任务（用于 Timing 条的窗口占比） */
  siblings?: SubtaskSnapshot[];
}>();

defineEmits<{ close: [] }>();

const KIND_LABEL: Record<string, string> = {
  planner: "智能体",
  search: "文献检索",
  extract: "模型生成",
  write: "模型生成",
  validate: "质量校验",
  review: "智能体",
};

const attributes = computed(() => {
  const list: { label: string; value: string }[] = [
    { label: "节点标识", value: props.node?.id ?? "" },
    { label: "节点类型", value: KIND_LABEL[props.node?.type ?? ""] ?? props.node?.type ?? "" },
    {
      label: "依赖",
      value: props.node?.dependencies.length ? props.node.dependencies.join("、") : "无",
    },
    { label: "重试", value: String(props.subtask?.retry_count ?? 0) },
  ];
  const output = props.subtask?.output;
  if (output && typeof output === "object") {
    list.push({ label: "输出字段", value: Object.keys(output).join(", ") });
  }
  return list.filter((item) => item.value !== "");
});

/** Timing 条：本 span 在全部兄弟 span 的时间窗口中的位置与宽度 */
const timing = computed(() => {
  const siblings = props.siblings ?? [];
  const self = props.subtask;
  if (!self?.started_at || !siblings.length) return null;
  const starts = siblings
    .filter((item) => item.started_at)
    .map((item) => new Date(item.started_at as string).getTime());
  const base = Math.min(...starts);
  const endCandidates = siblings.map((item) =>
    item.completed_at ? new Date(item.completed_at).getTime() : Date.now(),
  );
  const windowMs = Math.max(1, Math.max(...endCandidates) - base);
  const startMs = new Date(self.started_at as string).getTime() - base;
  const endMs = self.completed_at
    ? new Date(self.completed_at).getTime() - base
    : Date.now() - base;
  return {
    left: `${Math.min(100, (startMs / windowMs) * 100)}%`,
    width: `${Math.max(1.5, ((endMs - startMs) / windowMs) * 100)}%`,
    durationMs: Math.max(0, endMs - startMs),
  };
});

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
</script>

<template>
  <aside
    v-if="props.node"
    data-test="node-detail"
    class="w-full min-w-0 sm:min-w-72 overflow-clip rounded-2xl border border-border bg-background text-foreground"
  >
    <!-- Header：节点名称 + 类型 + 关闭 -->
    <header class="flex items-start justify-between gap-2 border-b border-border px-4 py-3">
      <div class="min-w-0">
        <p class="font-mono text-[10px] text-muted-foreground">{{ props.node.id }}</p>
        <h2 class="mt-0.5 text-sm font-semibold leading-5">{{ props.node.title }}</h2>
        <div class="mt-1.5 flex items-center gap-1.5">
          <StatusBadge :status="props.subtask?.status ?? 'pending'" />
          <span
            class="inline-flex h-5 items-center gap-1 rounded-full bg-muted px-2 text-[10px] font-medium text-muted-foreground"
          >
            <Brain class="size-3" />
            {{ KIND_LABEL[props.node.type] ?? props.node.type }}
          </span>
        </div>
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        title="关闭"
        aria-label="关闭"
        @click="$emit('close')"
      >
        <X class="size-3.5" />
      </Button>
    </header>

    <div class="flex flex-col gap-3 px-4 py-3">
      <!-- Timing 区：Timing 条 + tabular-nums -->
      <section>
        <h3
          class="mb-1.5 flex items-center gap-1 text-[10px] font-medium tracking-wide text-muted-foreground"
        >
          <Clock3 class="size-3" />
          执行耗时
        </h3>
        <div class="relative h-2 rounded-full bg-muted/40">
          <span
            v-if="timing"
            class="absolute top-0 h-full rounded-full bg-primary"
            :style="{ left: timing.left, width: timing.width }"
          />
        </div>
        <dl class="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          <dt class="text-muted-foreground">开始时间</dt>
          <dd class="font-mono tabular-nums">{{ formatDateTime(props.subtask?.started_at) }}</dd>
          <dt class="text-muted-foreground">结束时间</dt>
          <dd class="font-mono tabular-nums">{{ formatDateTime(props.subtask?.completed_at) }}</dd>
          <dt class="text-muted-foreground">持续时长</dt>
          <dd class="font-mono tabular-nums">
            {{ timing ? formatDuration(timing.durationMs) : "—" }}
          </dd>
        </dl>
      </section>

      <!-- 错误 -->
      <div
        v-if="props.subtask?.error"
        class="flex items-start gap-1.5 rounded-lg border border-destructive/30 bg-destructive/5 px-2.5 py-2"
      >
        <CircleAlert class="mt-0.5 size-3.5 shrink-0 text-destructive" />
        <p class="min-w-0 break-words text-[11px] leading-4 text-destructive">
          {{ props.subtask.error }}
        </p>
      </div>

      <!-- 属性列表 -->
      <section>
        <h3 class="mb-1.5 text-[10px] font-medium tracking-wide text-muted-foreground">
          节点属性
        </h3>
        <dl class="flex flex-col gap-1">
          <div
            v-for="item in attributes"
            :key="item.label"
            class="flex items-start justify-between gap-2 rounded-md bg-muted/40 px-2 py-1"
          >
            <dt class="shrink-0 font-mono text-[10px] text-muted-foreground">{{ item.label }}</dt>
            <dd class="min-w-0 truncate text-right text-[11px]" :title="item.value">
              <Repeat2 v-if="item.label === '重试'" class="mr-0.5 inline size-3" />
              {{ item.value }}
            </dd>
          </div>
        </dl>
      </section>
    </div>
  </aside>
</template>
