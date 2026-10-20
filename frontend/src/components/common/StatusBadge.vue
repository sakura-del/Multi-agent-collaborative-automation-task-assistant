<script setup lang="ts">
// 通用状态徽标：任务/子任务状态 → 中文标签 + 语义色调
// 样式走 shadcn Badge 变体（AGENTS.md §6.5.3），不再自研配色。
import { computed } from "vue";

import { Badge } from "@/components/ui/badge";
import type { BadgeVariants } from "@/components/ui/badge";

const props = defineProps<{ status: string; label?: string }>();

/** 语义色调：把业务状态映射到 Badge 的 variant（而不是直接给颜色）。 */
type Tone = NonNullable<BadgeVariants["variant"]>;

const TONES: Record<string, Tone> = {
  created: "muted",
  planning: "default",
  waiting_approval: "warning",
  running: "default",
  reviewing: "default",
  retrying: "warning",
  paused: "warning",
  waiting_human: "warning",
  completed: "success",
  failed: "destructive",
  cancelled: "muted",
  terminated: "muted",
  validating: "default",
  ready: "muted",
  pending: "muted",
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

const tone = computed<Tone>(() => TONES[props.status] ?? "muted");
const text = computed(() => props.label ?? LABELS[props.status] ?? props.status);
</script>

<template>
  <Badge :variant="tone">{{ text }}</Badge>
</template>
