<script setup lang="ts">
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import TaskDag from "@/components/tasks/TaskDag.vue";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { TaskPlan } from "@/api/types";

// 规划确认面板：只提供"重新生成"与"确认规划"两个命令（不可编辑规划内容）
// 样式走 shadcn-vue / Nova 规范（AGENTS.md §6.5）。
//
// ⚠️ 测试契约（不要改）：data-test="plan-panel" / "plan-conflict" / "plan-error" /
// "plan-regenerate" / "plan-approve"。
const props = withDefaults(
  defineProps<{
    plan: TaskPlan | null;
    pending?: boolean;
    error?: string | null;
    conflict?: string | null;
  }>(),
  { pending: false, error: null, conflict: null }
);

const emit = defineEmits<{ approve: [version: number]; regenerate: [] }>();
</script>

<template>
  <section class="flex flex-col gap-4" data-test="plan-panel">
    <ErrorBanner :message="props.conflict" testid="plan-conflict" />
    <ErrorBanner :message="props.error" testid="plan-error" />

    <template v-if="props.plan">
      <header class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h2 class="text-base font-semibold tracking-tight break-words">
            {{ props.plan.topic }}
          </h2>
          <p class="mt-0.5 text-sm text-muted-foreground">
            规划版本 v{{ props.plan.version }} · {{ props.plan.nodes.length }} 个节点
          </p>
        </div>
        <div class="flex flex-wrap gap-2">
          <Button
            variant="outline"
            data-test="plan-regenerate"
            type="button"
            :disabled="props.pending"
            @click="emit('regenerate')"
          >
            重新生成
          </Button>
          <Button
            data-test="plan-approve"
            type="button"
            :disabled="props.pending"
            @click="emit('approve', props.plan.version)"
          >
            确认规划
          </Button>
        </div>
      </header>

      <div class="grid gap-3 md:grid-cols-3">
        <Card class="p-3">
          <h3 class="mb-1.5 text-sm font-medium">研究问题</h3>
          <ol class="list-decimal space-y-1 pl-4 text-sm text-muted-foreground">
            <li v-for="question in props.plan.research_questions" :key="question">
              {{ question }}
            </li>
          </ol>
        </Card>
        <Card class="p-3">
          <h3 class="mb-1.5 text-sm font-medium">检索式</h3>
          <ul class="list-disc space-y-1 pl-4 text-sm text-muted-foreground">
            <li v-for="query in props.plan.search_queries" :key="query">{{ query }}</li>
          </ul>
        </Card>
        <Card class="p-3">
          <h3 class="mb-1.5 text-sm font-medium">验收标准</h3>
          <ul class="list-disc space-y-1 pl-4 text-sm text-muted-foreground">
            <li v-for="item in props.plan.acceptance_criteria" :key="item">{{ item }}</li>
          </ul>
        </Card>
      </div>

      <section class="flex flex-col gap-2">
        <h3 class="text-sm font-medium">任务 DAG（只读）</h3>
        <TaskDag :nodes="props.plan.nodes" />
      </section>
    </template>

    <EmptyState
      v-else
      title="暂无规划"
      description="任务进入规划阶段后，这里会展示研究问题、检索式与任务 DAG。"
    />
  </section>
</template>
