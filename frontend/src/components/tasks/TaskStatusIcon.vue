<script setup lang="ts">
import { computed } from "vue";
import { Check, CircleDot, Clock, Loader2, X } from "lucide-vue-next";

import type { TaskStatus } from "@/api/types";
import { isTerminal, needsUserAction } from "@/utils/task-status";

/**
 * 任务状态图标（任务列表重设计 2026-10-23）。
 *
 * ## 为什么要有它
 *
 * 旧列表用**百分比进度条**表示状态，但那个百分比是从状态映射的常量：
 *
 * ```ts
 * failed: 100,      // 失败显示 100% 满格蓝
 * completed: 100,   // 与完成视觉完全相同
 * ```
 *
 * 结果「失败」和「完成」在列表里长得一样 —— 这一列几乎不传递信息。
 *
 * 图标用**形状**区分终态（✓ / ✕ / ⊘），形状比颜色更可靠：
 * 色盲用户、灰度打印、深色模式下都分得清。
 *
 * ## `data-state` 供测试与 E2E 断言
 *
 * 不靠图标 SVG 内部结构定位（那是 lucide 的实现细节）。
 */
const props = defineProps<{ status: TaskStatus }>();

type IconKind = "done" | "failed" | "cancelled" | "action" | "busy";

const KIND: Record<TaskStatus, IconKind> = {
  created: "busy",
  planning: "busy",
  waiting_approval: "action",
  running: "busy",
  retrying: "busy",
  paused: "action",
  reviewing: "busy",
  waiting_human: "action",
  completed: "done",
  failed: "failed",
  cancelled: "cancelled",
};

// 颜色用查表式字面量（AGENTS §6.5：绝不动态拼接 Tailwind 类名，否则静默丢失）
const TONE_CLASS: Record<IconKind, string> = {
  done: "text-success",
  failed: "text-destructive",
  cancelled: "text-muted-foreground",
  action: "text-warning",
  busy: "text-primary",
};

const LABEL: Record<IconKind, string> = {
  done: "已完成",
  failed: "已失败",
  cancelled: "已取消",
  action: "需要你处理",
  busy: "进行中",
};

const kind = computed<IconKind>(() => KIND[props.status] ?? "busy");
const toneClass = computed(() => TONE_CLASS[kind.value]);
const label = computed(() => LABEL[kind.value]);
const spinning = computed(() => props.status === "running" || props.status === "retrying");
</script>

<template>
  <span
    data-test="status-icon"
    :data-state="status"
    :class="['inline-flex shrink-0 items-center', toneClass]"
    :title="label"
    :aria-label="label"
    role="img"
  >
    <Check v-if="kind === 'done'" :size="15" :stroke-width="2.5" aria-hidden="true" />
    <X v-else-if="kind === 'failed'" :size="15" :stroke-width="2.5" aria-hidden="true" />
    <span
      v-else-if="kind === 'cancelled'"
      class="flex size-[15px] items-center justify-center"
      aria-hidden="true"
    >
      <span class="h-px w-3 bg-current" />
    </span>
    <Clock v-else-if="kind === 'action'" :size="15" :stroke-width="2.2" aria-hidden="true" />
    <Loader2
      v-else-if="spinning"
      :size="15"
      class="animate-spin motion-reduce:animate-none"
      aria-hidden="true"
    />
    <CircleDot v-else :size="15" aria-hidden="true" />
  </span>
</template>
