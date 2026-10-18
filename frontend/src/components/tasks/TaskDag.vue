<script setup lang="ts">
import { computed, ref } from "vue";

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

const positions = computed(() => layoutDag(props.nodes));
const bounds = computed(() => dagBounds(positions.value));
const scale = ref(1);

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
    class="dag"
    data-test="dag"
    data-draggable="false"
    data-connectable="false"
    :style="{ height: `${bounds.height + 24}px` }"
  >
    <div class="dag__toolbar">
      <button class="btn" data-test="dag-fit" type="button" @click="toggleFit">
        {{ scale === 1 ? "适应窗口" : "原始大小" }}
      </button>
      <span class="dag__hint">共 {{ nodes.length }} 个节点</span>
    </div>

    <div class="dag__viewport">
      <div
        class="dag__canvas"
        :style="{
          width: `${bounds.width}px`,
          height: `${bounds.height}px`,
          transform: `scale(${scale})`,
          transformOrigin: 'top left',
        }"
      >
        <svg class="dag__edges" :width="bounds.width" :height="bounds.height" aria-hidden="true">
          <path
            v-for="edge in edges"
            :key="edge.id"
            :data-test="`dag-edge-${edge.id}`"
            :d="edge.d"
            class="dag__edge"
            :class="`dag__edge--${edge.tone}`"
          />
        </svg>

        <div
          v-for="node in nodes"
          :key="node.id"
          class="dag-node"
          :class="[`dag-node--${nodeStatusTone(statusOf(node.id))}`, { 'dag-node--selected': selected === node.id }]"
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
          <span class="dag-node__title">{{ node.title }}</span>
          <span class="dag-node__meta">
            {{ node.type }} · {{ nodeStatusLabel(statusOf(node.id)) }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dag {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  min-width: 0;
}

.dag__toolbar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.dag__hint {
  font-size: 0.82rem;
  color: #64748b;
}

.dag__viewport {
  overflow: auto;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: #fcfdff;
  max-height: 420px;
  max-width: 100%;
  min-width: 0;
}

.dag__canvas {
  position: relative;
}

.dag__edges {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.dag__edge {
  fill: none;
  stroke: #cbd5e1;
  stroke-width: 1.6;
}

.dag__edge--active {
  stroke: #93c5fd;
}

.dag__edge--success {
  stroke: #6ee7b7;
}

.dag__edge--attention {
  stroke: #fcd34d;
}

.dag__edge--danger {
  stroke: #fca5a5;
}

.dag-node {
  position: absolute;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 0.15rem;
  padding: 0.5rem 0.65rem;
  border: 1px solid var(--color-border);
  border-left-width: 4px;
  border-radius: 10px;
  background: var(--color-card);
  cursor: pointer;
  text-align: left;
}

.dag-node--neutral {
  border-left-color: #cbd5e1;
}

.dag-node--active {
  border-left-color: var(--color-primary);
}

.dag-node--success {
  border-left-color: var(--color-success);
}

.dag-node--attention {
  border-left-color: var(--color-warning);
}

.dag-node--danger {
  border-left-color: var(--color-error);
}

.dag-node--selected {
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.35);
}

.dag-node__title {
  font-size: 0.88rem;
  font-weight: 600;
}

.dag-node__meta {
  font-size: 0.76rem;
  color: #64748b;
}
</style>
