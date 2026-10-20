<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { Button } from "@/components/ui/button";
import type { PlanNode, SubtaskStatus } from "@/api/types";
import {
  DAG_NODE_HEIGHT,
  DAG_NODE_WIDTH,
  dagBounds,
  layoutDag,
  nodeAriaLabel,
  nodeStatusLabel,
  nodeStatusTone,
} from "@/utils/dag-layout";

// 固定几何 DAG：位置只由拓扑决定（状态更新不会让节点跳动）。
// 交互只保留平移/适应窗口/选择；拖拽与连线编辑按计划禁用。
const props = withDefaults(
  defineProps<{
    nodes: PlanNode[];
    statuses?: Record<string, SubtaskStatus>;
    selected?: string | null;
  }>(),
  { statuses: () => ({}), selected: null }
);

const emit = defineEmits<{ select: [nodeId: string] }>();

/**
 * 语义色调 → Tailwind 类（DAG 边与节点）。
 *
 * ⚠️ 必须用**字面量**类名，不能拼字符串：Tailwind 靠静态扫描生成样式，
 * `stroke-${color}` 这类动态拼接不会被识别，样式会静默丢失。
 */
const EDGE_TONE_CLASS: Record<string, string> = {
  neutral: "stroke-border",
  active: "stroke-primary/50",
  success: "stroke-success/60",
  attention: "stroke-warning/60",
  danger: "stroke-destructive/50",
};

const NODE_TONE_CLASS: Record<string, string> = {
  neutral: "border-l-border",
  active: "border-l-primary",
  success: "border-l-success",
  attention: "border-l-warning",
  danger: "border-l-destructive",
};

const positions = computed(() => layoutDag(props.nodes));
const bounds = computed(() => dagBounds(positions.value));
const scale = ref(1);

/** 窄屏默认适配窗口，避免 DAG 一进来就被裁切（桌面保持 1:1 便于读数） */
onMounted(() => {
  if (typeof window !== "undefined" && window.innerWidth <= 720) {
    const available = Math.max(window.innerWidth - 72, 240);
    scale.value = Number(Math.min(1, available / Math.max(bounds.value.width, 1)).toFixed(3));
  }
});

function toggleFit() {
  // happy-dom 下没有真实尺寸，取容器宽度做保守估算
  const available = 720;
  const next = Math.min(1, available / Math.max(bounds.value.width, 1));
  scale.value = scale.value === 1 ? Number(next.toFixed(3)) : 1;
}

function edgePath(from: PlanNode, to: PlanNode): string {
  const start = positions.value[from.id];
  const end = positions.value[to.id];
  if (!start || !end) {
    return "";
  }
  const x1 = start.x + DAG_NODE_WIDTH;
  const y1 = start.y + DAG_NODE_HEIGHT / 2;
  const x2 = end.x;
  const y2 = end.y + DAG_NODE_HEIGHT / 2;
  const mid = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
}

const edges = computed(() => {
  const byId = new Map(props.nodes.map((node) => [node.id, node]));
  const list: Array<{ id: string; d: string; tone: string }> = [];
  for (const node of props.nodes) {
    for (const dependency of node.dependencies) {
      const parent = byId.get(dependency);
      if (!parent) {
        continue;
      }
      list.push({
        id: `${dependency}-${node.id}`,
        d: edgePath(parent, node),
        tone: nodeStatusTone(props.statuses[node.id]),
      });
    }
  }
  return list;
});

function statusOf(nodeId: string): SubtaskStatus | undefined {
  return props.statuses[nodeId];
}
</script>

<template>
  <div
    class="flex min-w-0 flex-col gap-2"
    data-test="dag"
    data-draggable="false"
    data-connectable="false"
    :style="{ height: `${bounds.height + 24}px` }"
  >
    <div class="flex items-center gap-3">
      <Button variant="outline" size="sm" data-test="dag-fit" type="button" @click="toggleFit">
        {{ scale === 1 ? "适应窗口" : "原始大小" }}
      </Button>
      <span class="text-xs text-muted-foreground">共 {{ nodes.length }} 个节点</span>
    </div>

    <div
      class="max-h-[420px] max-w-full min-w-0 overflow-auto rounded-lg border border-border bg-background"
    >
      <div
        class="relative"
        :style="{
          width: `${bounds.width}px`,
          height: `${bounds.height}px`,
          transform: `scale(${scale})`,
          transformOrigin: 'top left',
        }"
      >
        <svg
          class="pointer-events-none absolute inset-0"
          :width="bounds.width"
          :height="bounds.height"
          aria-hidden="true"
        >
          <path
            v-for="edge in edges"
            :key="edge.id"
            :data-test="`dag-edge-${edge.id}`"
            :d="edge.d"
            class="fill-none stroke-[1.6]"
            :class="EDGE_TONE_CLASS[edge.tone]"
          />
        </svg>

        <div
          v-for="node in nodes"
          :key="node.id"
          class="absolute flex cursor-pointer flex-col justify-center gap-0.5 rounded-lg border border-l-4 border-border bg-card px-2.5 py-2 text-left"
          :class="[
            NODE_TONE_CLASS[nodeStatusTone(statusOf(node.id))],
            selected === node.id
              ? 'ring-2 ring-primary/35'
              : '',
          ]"
          :data-test="`dag-node-${node.id}`"
          :style="{
            left: `${positions[node.id]?.x ?? 0}px`,
            top: `${positions[node.id]?.y ?? 0}px`,
            width: `${DAG_NODE_WIDTH}px`,
            height: `${DAG_NODE_HEIGHT}px`,
          }"
          role="button"
          tabindex="0"
          :aria-label="nodeAriaLabel(node.id, node.title, statusOf(node.id))"
          @click="emit('select', node.id)"
          @keydown.enter.prevent="emit('select', node.id)"
        >
          <span class="text-sm font-semibold">{{ node.title }}</span>
          <span class="text-xs text-muted-foreground">
            {{ node.type }} · {{ nodeStatusLabel(statusOf(node.id)) }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
