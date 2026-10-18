<script setup lang="ts">
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import TaskDag from "@/components/tasks/TaskDag.vue";
import type { TaskPlan } from "@/api/types";

// 规划确认面板：只提供"重新生成"与"确认规划"两个命令（不可编辑规划内容）
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
  <section class="plan-panel" data-test="plan-panel">
    <ErrorBanner :message="props.conflict" testid="plan-conflict" />
    <ErrorBanner :message="props.error" testid="plan-error" />

    <template v-if="props.plan">
      <header class="plan-panel__header">
        <div>
          <h2>{{ props.plan.topic }}</h2>
          <p class="page__hint">规划版本 v{{ props.plan.version }} · {{ props.plan.nodes.length }} 个节点</p>
        </div>
        <div class="plan-panel__actions">
          <button
            class="btn"
            data-test="plan-regenerate"
            type="button"
            :disabled="props.pending"
            @click="emit('regenerate')"
          >
            重新生成
          </button>
          <button
            class="btn btn--primary"
            data-test="plan-approve"
            type="button"
            :disabled="props.pending"
            @click="emit('approve', props.plan.version)"
          >
            确认规划
          </button>
        </div>
      </header>

      <div class="plan-panel__grid">
        <section>
          <h3>研究问题</h3>
          <ol>
            <li v-for="question in props.plan.research_questions" :key="question">{{ question }}</li>
          </ol>
        </section>
        <section>
          <h3>检索式</h3>
          <ul>
            <li v-for="query in props.plan.search_queries" :key="query">{{ query }}</li>
          </ul>
        </section>
        <section>
          <h3>验收标准</h3>
          <ul>
            <li v-for="item in props.plan.acceptance_criteria" :key="item">{{ item }}</li>
          </ul>
        </section>
      </div>

      <section class="plan-panel__dag">
        <h3>任务 DAG（只读）</h3>
        <TaskDag :nodes="props.plan.nodes" />
      </section>
    </template>

    <p v-else class="page__hint">暂无规划。</p>
  </section>
</template>

<style scoped>
.plan-panel {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.plan-panel__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

.plan-panel__header > div:first-child {
  min-width: 0;
}

.plan-panel__header h2 {
  font-size: 1.05rem;
  overflow-wrap: anywhere;
}

.plan-panel__actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.plan-panel__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 1rem;
}

.plan-panel__grid h3,
.plan-panel__dag h3 {
  font-size: 0.9rem;
  margin-bottom: 0.25rem;
}

.plan-panel__grid ol,
.plan-panel__grid ul {
  padding-left: 1.1rem;
  font-size: 0.88rem;
  color: #475569;
}

.plan-panel__dag {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
</style>
