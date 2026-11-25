<script setup lang="ts">
import { computed } from "vue";

export interface SegmentedItem {
  id: string;
  label: string;
  value: number;
  color: string;
}

const props = defineProps<{
  segments: SegmentedItem[];
}>();

const total = computed(() =>
  props.segments.reduce((sum, item) => sum + Math.max(0, item.value), 0),
);

const normalized = computed(() => {
  if (total.value <= 0) return [];
  return props.segments
    .filter((item) => item.value > 0)
    .map((item) => ({
      ...item,
      percent: ((item.value / total.value) * 100).toFixed(1),
      width: `${(item.value / total.value) * 100}%`,
    }));
});
</script>

<template>
  <div v-if="normalized.length" class="space-y-2">
    <div
      class="flex h-2.5 w-full overflow-hidden rounded-full bg-muted"
      role="img"
      aria-label="分布比例条"
    >
      <div
        v-for="seg in normalized"
        :key="seg.id"
        class="h-full border-r border-background/80 last:border-r-0 transition-opacity duration-300 motion-reduce:transition-none"
        :style="{ width: seg.width, backgroundColor: seg.color }"
        :title="`${seg.label}：${seg.value}（${seg.percent}%）`"
      />
    </div>
  </div>
</template>
