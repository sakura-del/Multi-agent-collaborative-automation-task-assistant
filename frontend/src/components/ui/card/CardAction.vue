<script setup lang="ts">
import type { HTMLAttributes } from "vue";

import { cn } from "@/lib/utils";

/**
 * 右上角操作槽，跨标题与描述两行。
 *
 * 用 flex 而不是 block：内部放 checkbox / radio 这类 inline-flex 控件时，
 * 对勾出现不会改变基线，卡片标题不会跟着抖。
 */
const props = defineProps<{ class?: HTMLAttributes["class"] }>();
</script>

<template>
  <div
    data-slot="card-action"
    :class="
      cn(
        'col-start-2 row-span-2 row-start-1 flex items-center self-start justify-self-end',
        props.class,
      )
    "
  >
    <slot />
  </div>
</template>
