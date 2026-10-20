<script setup lang="ts">
import { computed } from "vue";
import { X } from "lucide-vue-next";

import StatusBadge from "@/components/common/StatusBadge.vue";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { PlanNode, SubtaskSnapshot } from "@/api/types";
import { formatDateTime } from "@/utils/task-status";

// 节点详情侧抽屉：位于 DAG 容器之外，避免影响画布几何
// 样式走 shadcn-vue / Nova 规范（AGENTS.md §6.5）。
//
// ⚠️ 测试契约（不要改）：data-test="node-detail"。
const props = defineProps<{
  node: PlanNode | null;
  subtask?: SubtaskSnapshot | null;
}>();

defineEmits<{ close: [] }>();

const outputKeys = computed(() => {
  const output = props.subtask?.output;
  return output && typeof output === "object" ? Object.keys(output) : [];
});
</script>

<template>
  <aside
    v-if="props.node"
    class="flex min-w-72 flex-col gap-3 rounded-lg border border-border bg-card p-4"
    data-test="node-detail"
  >
    <header class="flex items-start justify-between gap-2">
      <div class="min-w-0">
        <h2 class="text-sm font-semibold">{{ props.node.title }}</h2>
        <p class="mt-0.5 text-xs text-muted-foreground">
          {{ props.node.id }} · {{ props.node.type }} · 依赖
          {{ props.node.dependencies.length ? props.node.dependencies.join("、") : "无" }}
        </p>
      </div>
      <Button
        variant="ghost"
        size="icon-sm"
        type="button"
        title="关闭"
        aria-label="关闭"
        @click="$emit('close')"
      >
        <X :size="16" />
      </Button>
    </header>

    <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
      <dt class="text-muted-foreground">状态</dt>
      <dd><StatusBadge :status="props.subtask?.status ?? 'pending'" /></dd>
      <dt class="text-muted-foreground">重试次数</dt>
      <dd>{{ props.subtask?.retry_count ?? 0 }}</dd>
      <dt class="text-muted-foreground">开始时间</dt>
      <dd>{{ formatDateTime(props.subtask?.started_at) }}</dd>
      <dt class="text-muted-foreground">完成时间</dt>
      <dd>{{ formatDateTime(props.subtask?.completed_at) }}</dd>
    </dl>

    <Alert v-if="props.subtask?.error" variant="destructive">
      <span>{{ props.subtask.error }}</span>
    </Alert>

    <section v-if="outputKeys.length" class="flex flex-col gap-1">
      <h3 class="text-sm font-medium">产出字段</h3>
      <ul class="list-disc space-y-0.5 pl-4 text-sm text-muted-foreground">
        <li v-for="key in outputKeys" :key="key">{{ key }}</li>
      </ul>
    </section>
    <p v-else class="text-sm text-muted-foreground">该节点暂无产出。</p>
  </aside>
</template>
