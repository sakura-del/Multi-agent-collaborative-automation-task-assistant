<script setup lang="ts">
import BeAgentPlan from "@/components/motion/BeAgentPlan.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import TaskDag from "@/components/tasks/TaskDag.vue";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { TaskPlan } from "@/api/types";

// 规划确认面板：只提供"重新生成"与"确认规划"两个命令（不可编辑规划内容）
// 样式走 shadcn-vue / Nova 规范（AGENTS.md §6.5）。
//
// 「重新生成」发的是 `replan`（退回规划阶段），不是节点重跑：规划失败时
// 任务还没有任何子任务，`retry_node` 只会 404。
//
// ⚠️ 测试契约（不要改）：data-test="plan-panel" / "plan-conflict" / "plan-error" /
// "plan-regenerate" / "plan-approve"。
const props = withDefaults(
  defineProps<{
    plan: TaskPlan | null;
    pending?: boolean;
    error?: string | null;
    conflict?: string | null;
    /** 规划被阻断（转人工且没有 plan）：空态要说「没生成」，不是「还没开始」。 */
    blocked?: boolean;
    /** 当前状态是否允许重新规划（与后端白名单一致）；false 时连按钮都不出现。 */
    canReplan?: boolean;
    /**
     * 当前状态是否允许确认规划（只有 waiting_approval）。
     *
     * false 时按钮不渲染并给出原因：`waiting_human` 下旧规划仍在快照里，
     * 若照常渲染，用户点了只会拿到 422（Issue #23）。
     */
    canApprove?: boolean;
    /** 节点步骤（beUI Agent Plan 形态，TaskDetailView 组装）；空 = 不渲染卡片 */
    steps?: import("@/components/motion/BeAgentPlan.vue").AgentPlanStep[];
    /** 规划面板级状态徽章 */
    planStatus?: "ready" | "running" | "completed" | "failed" | "draft";
  }>(),
  {
    pending: false,
    error: null,
    conflict: null,
    blocked: false,
    canReplan: true,
    canApprove: true,
  }
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
            v-if="props.canReplan"
            variant="outline"
            data-test="plan-regenerate"
            type="button"
            :disabled="props.pending"
            @click="emit('regenerate')"
          >
            重新生成
          </Button>
          <Button
            v-if="props.canApprove"
            data-test="plan-approve"
            type="button"
            :disabled="props.pending"
            @click="emit('approve', props.plan.version)"
          >
            确认规划
          </Button>
        </div>
      </header>

      <!-- 只有 waiting_approval 能确认；其它状态给出真实原因，而不是让用户点了报错 -->
      <p
        v-if="!props.canApprove"
        class="text-sm text-muted-foreground"
        data-test="plan-approve-hint"
      >
        该规划已确认，任务已暂停在执行阶段；请到「执行」页重试失败的节点，或点上方「重新生成」换一版规划。
      </p>

      <div class="grid gap-3 md:grid-cols-3">
        <Card class="gap-1.5 p-3">
          <h3 class="mb-1.5 text-sm font-medium">研究问题</h3>
          <ol class="list-decimal space-y-1 pl-4 text-sm text-muted-foreground">
            <li v-for="question in props.plan.research_questions" :key="question">
              {{ question }}
            </li>
          </ol>
        </Card>
        <Card class="gap-1.5 p-3">
          <h3 class="mb-1.5 text-sm font-medium">检索式</h3>
          <ul class="list-disc space-y-1 pl-4 text-sm text-muted-foreground">
            <li v-for="query in props.plan.search_queries" :key="query">{{ query }}</li>
          </ul>
        </Card>
        <Card class="gap-1.5 p-3">
          <h3 class="mb-1.5 text-sm font-medium">验收标准</h3>
          <ul class="list-disc space-y-1 pl-4 text-sm text-muted-foreground">
            <li v-for="item in props.plan.acceptance_criteria" :key="item">{{ item }}</li>
          </ul>
        </Card>
      </div>

      <section class="flex flex-col gap-2">
        <h3 class="text-sm font-medium">执行计划（beUI Agent Plan）</h3>
        <BeAgentPlan
          :title="props.plan.topic"
          :steps="props.steps ?? []"
          :plan-status="props.planStatus ?? 'ready'"
          :revision="props.plan.version"
          :pending="props.pending"
          :show-actions="false"
        />
        <details class="mt-1">
          <summary class="cursor-pointer text-xs text-muted-foreground">查看经典 DAG 视图</summary>
          <div class="mt-2">
            <TaskDag :nodes="props.plan.nodes" />
          </div>
        </details>
      </section>
    </template>

    <EmptyState
      v-else
      :title="props.blocked ? '规划没有生成' : '暂无规划'"
      :description="
        props.blocked
          ? '任务在规划阶段停下，具体原因见页面顶部提示。处理后再点「重新规划」。'
          : '任务进入规划阶段后，这里会展示研究问题、检索式与任务 DAG。'
      "
    >
      <!-- 没有规划时唯一能做的事：让后端重新走一遍规划（规划失败时没有任何
           子任务，节点重跑一定 404）。 -->
      <Button
        v-if="props.canReplan"
        variant="outline"
        data-test="plan-regenerate"
        type="button"
        :disabled="props.pending"
        @click="emit('regenerate')"
      >
        {{ props.blocked ? "重新规划" : "重新生成" }}
      </Button>
    </EmptyState>
  </section>
</template>
