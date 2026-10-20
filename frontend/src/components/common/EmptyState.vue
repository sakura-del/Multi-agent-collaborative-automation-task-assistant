<script setup lang="ts">
import type { HTMLAttributes } from "vue";
import { Inbox } from "lucide-vue-next";

import { cn } from "@/lib/utils";

/**
 * 空态（项目通用件）。
 *
 * 空列表必须给**明确引导**（说明下一步做什么），不能只显示「暂无数据」。
 * 图标默认用 Lucide 的 Inbox，可通过 `#icon` 插槽替换。
 */
const props = withDefaults(
  defineProps<{
    title: string;
    description?: string;
    testid?: string;
    class?: HTMLAttributes["class"];
  }>(),
  { description: undefined, testid: "empty-state", class: undefined }
);
</script>

<template>
  <div
    :data-test="props.testid"
    :class="
      cn(
        'flex flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-border px-6 py-10 text-center',
        props.class
      )
    "
  >
    <slot name="icon">
      <Inbox class="mb-1 size-6 text-muted-foreground" aria-hidden="true" />
    </slot>
    <p class="text-sm font-medium text-foreground">{{ props.title }}</p>
    <p v-if="props.description" class="max-w-md text-sm text-muted-foreground">
      {{ props.description }}
    </p>
  </div>
</template>
