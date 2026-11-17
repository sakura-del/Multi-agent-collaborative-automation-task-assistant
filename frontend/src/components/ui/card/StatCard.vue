<script setup lang="ts">
/**
 * 指标小块（KPI）：标签 + 数值 + 口径说明。
 *
 * 抽出来的原因：AdminByok 与 Observability 里有 10 处完全相同的
 * `rounded-2xl border border-border bg-card px-3 py-2.5`，外加同一套
 * 11px 大写标签与 tabular-nums 数值。改一次间距要改十处，漏一处就错位。
 *
 * `hint` 走插槽：有的说明带警告图标、有的带 `data-test`、有的按条件切换，
 * 纯字符串 prop 覆盖不了这些情况。
 */
import type { HTMLAttributes } from "vue";

import { cn } from "@/lib/utils";

const props = withDefaults(
  defineProps<{
    /** 标签文本；需要图标等富内容时用 #label 插槽，此时可省略。 */
    label?: string;
    value: string | number;
    /** 口径说明（分母、统计范围）。复杂内容用 #hint 插槽。 */
    hint?: string;
    /** 数值的 data-test，供 E2E / 单测定位。 */
    valueTestid?: string;
    class?: HTMLAttributes["class"];
    /** md：成本类次要指标；lg：主指标。 */
    size?: "md" | "lg";
  }>(),
  { size: "lg" },
);
</script>

<template>
  <div :class="cn('rounded-2xl border border-border bg-card px-3 py-2.5', props.class)">
    <p class="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
      <slot name="label">{{ props.label }}</slot>
    </p>
    <p
      class="mt-1 font-semibold tabular-nums"
      :class="props.size === 'lg' ? 'text-2xl' : 'text-xl'"
      :data-test="props.valueTestid"
    >
      {{ props.value }}
    </p>
    <slot name="hint">
      <p v-if="props.hint" class="mt-0.5 text-[11px] text-muted-foreground">{{ props.hint }}</p>
    </slot>
  </div>
</template>
