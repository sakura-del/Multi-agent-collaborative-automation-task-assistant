<script setup lang="ts">
import { computed } from "vue";
import type { HTMLAttributes } from "vue";

import { cn } from "@/lib/utils";

/**
 * 卡片标题。
 *
 * `as` 控制语义层级：卡片开启一个区块时用 h2/h3，纯展示用 div。
 * 旧实现写死 h3，列表页一屏几十个 h3，屏幕阅读器按标题跳转时全是同级。
 */
const props = withDefaults(
  defineProps<{
    class?: HTMLAttributes["class"];
    as?: "div" | "h2" | "h3" | "h4";
  }>(),
  { as: "div" },
);

const tag = computed(() => props.as);
</script>

<template>
  <component
    :is="tag"
    data-slot="card-title"
    :class="cn('text-[15px] leading-snug font-semibold text-foreground', props.class)"
  >
    <slot />
  </component>
</template>
