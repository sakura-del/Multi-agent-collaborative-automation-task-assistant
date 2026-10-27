<script setup lang="ts">
// 工作台统一页头：只负责信息层级，不承载业务状态；右侧操作由原页面通过插槽提供。
defineProps<{
  title: string;
  description?: string;
  /** 保留少数页面已有的描述测试契约；不是业务参数。 */
  descriptionTestid?: string;
  eyebrow?: string;
}>();
</script>

<template>
  <header class="mb-5 flex flex-wrap items-start justify-between gap-3">
    <div class="min-w-0">
      <p
        v-if="eyebrow"
        class="mb-2 text-[10px] font-semibold tracking-[0.16em] text-primary uppercase"
      >
        {{ eyebrow }}
      </p>
      <h1 class="font-display text-3xl font-medium tracking-[-0.035em]">{{ title }}</h1>
      <p
        v-if="description"
        :data-test="descriptionTestid"
        class="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground"
      >
        {{ description }}
      </p>
    </div>
    <div v-if="$slots.default" class="flex shrink-0 flex-wrap items-center gap-2">
      <slot />
    </div>
  </header>
</template>
