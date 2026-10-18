<script setup lang="ts">
// 通用状态徽标：任务/子任务状态 → 中文标签 + 色调
import { computed } from "vue";

const props = defineProps<{ status: string; label?: string }>();

const TONES: Record<string, string> = {
  created: "neutral",
  planning: "active",
  waiting_approval: "attention",
  running: "active",
  reviewing: "active",
  retrying: "attention",
  paused: "attention",
  waiting_human: "attention",
  completed: "success",
  failed: "danger",
  cancelled: "neutral",
  terminated: "neutral",
  validating: "active",
  ready: "neutral",
  pending: "neutral",
};

const LABELS: Record<string, string> = {
  created: "已创建",
  planning: "规划中",
  waiting_approval: "待确认规划",
  running: "执行中",
  reviewing: "复盘中",
  retrying: "重试中",
  paused: "已暂停",
  waiting_human: "待人工介入",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  terminated: "已终止",
  validating: "校验中",
  ready: "就绪",
  pending: "待调度",
};

const tone = computed(() => TONES[props.status] ?? "neutral");
const text = computed(() => props.label ?? LABELS[props.status] ?? props.status);
</script>

<template>
  <span class="status-badge" :class="`status-badge--${tone}`">{{ text }}</span>
</template>

<style scoped>
.status-badge {
  display: inline-block;
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  font-size: 0.78rem;
  line-height: 1.5;
  white-space: nowrap;
}

.status-badge--neutral {
  background: #f1f5f9;
  color: #475569;
}

.status-badge--active {
  background: #eff6ff;
  color: #1d4ed8;
}

.status-badge--attention {
  background: #fffbeb;
  color: #b45309;
}

.status-badge--success {
  background: #ecfdf5;
  color: #047857;
}

.status-badge--danger {
  background: #fef2f2;
  color: #b91c1c;
}
</style>
