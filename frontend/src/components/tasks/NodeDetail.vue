<script setup lang="ts">
import { computed } from "vue";
import { X } from "lucide-vue-next";

import StatusBadge from "@/components/common/StatusBadge.vue";
import type { PlanNode, SubtaskSnapshot } from "@/api/types";
import { formatDateTime } from "@/utils/task-status";

// 节点详情侧抽屉：位于 DAG 容器之外，避免影响画布几何
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
  <aside v-if="props.node" class="drawer" data-test="node-detail">
    <header class="drawer__header">
      <div>
        <h2>{{ props.node.title }}</h2>
        <p class="drawer__meta">
          {{ props.node.id }} · {{ props.node.type }} · 依赖
          {{ props.node.dependencies.length ? props.node.dependencies.join("、") : "无" }}
        </p>
      </div>
      <button class="icon-button" type="button" title="关闭" aria-label="关闭" @click="$emit('close')">
        <X :size="16" />
      </button>
    </header>

    <dl class="drawer__facts">
      <dt>状态</dt>
      <dd><StatusBadge :status="props.subtask?.status ?? 'pending'" /></dd>
      <dt>重试次数</dt>
      <dd>{{ props.subtask?.retry_count ?? 0 }}</dd>
      <dt>开始时间</dt>
      <dd>{{ formatDateTime(props.subtask?.started_at) }}</dd>
      <dt>完成时间</dt>
      <dd>{{ formatDateTime(props.subtask?.completed_at) }}</dd>
    </dl>

    <p v-if="props.subtask?.error" class="banner banner--error">{{ props.subtask.error }}</p>

    <section v-if="outputKeys.length" class="drawer__section">
      <h3>产出字段</h3>
      <ul>
        <li v-for="key in outputKeys" :key="key">{{ key }}</li>
      </ul>
    </section>
    <p v-else class="page__hint">该节点暂无产出。</p>
  </aside>
</template>

<style scoped>
.drawer {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding: 1rem;
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  min-width: 280px;
}

.drawer__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.5rem;
}

.drawer__header h2 {
  font-size: 1rem;
}

.drawer__meta {
  font-size: 0.8rem;
  color: #64748b;
  margin-top: 0.15rem;
}

.drawer__facts {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.25rem 0.75rem;
  font-size: 0.85rem;
}

.drawer__facts dt {
  color: #64748b;
}

.drawer__section h3 {
  font-size: 0.9rem;
  margin-bottom: 0.25rem;
}

.drawer__section ul {
  list-style: none;
  font-size: 0.85rem;
  color: #475569;
}
</style>
