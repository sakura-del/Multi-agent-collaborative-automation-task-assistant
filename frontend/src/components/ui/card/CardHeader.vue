<script setup lang="ts">
import { computed, inject } from "vue";
import type { HTMLAttributes } from "vue";

import { cn } from "@/lib/utils";

import { CARD_SIZE_KEY, INSET_CLASS } from "./card-context";

/**
 * 标题区：双行网格（标题 + 描述）。
 *
 * 存在 `CardAction` 时自动切成 `[1fr_auto]` 两列，操作贴右上角、跨两行。
 * 用 CSS `:has` 判断而不是让调用方传 prop——`CardAction` 是否出现由
 * 子元素决定，调用方不该为了对齐再声明一次。
 */
const props = defineProps<{ class?: HTMLAttributes["class"] }>();

const size = inject(CARD_SIZE_KEY, computed(() => "default" as const));
</script>

<template>
  <div
    data-slot="card-header"
    :class="
      cn(
        'grid auto-rows-min grid-rows-[auto_auto] items-start gap-1.5',
        '[&:has([data-slot=card-action])]:grid-cols-[1fr_auto]',
        INSET_CLASS[size],
        props.class,
      )
    "
  >
    <slot />
  </div>
</template>
