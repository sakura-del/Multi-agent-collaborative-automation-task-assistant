<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import type { PlanNode, SubtaskStatus } from "@/api/types";
import {
  DAG_NODE_HEIGHT,
  DAG_NODE_WIDTH,
  DAG_VERTICAL_NODE_WIDTH,
  layoutDagAdaptive,
  nodeAriaLabel,
  nodeStatusLabel,
  nodeStatusTone,
} from "@/utils/dag-layout";

// 固定几何 DAG：位置只由拓扑决定（状态更新不会让节点跳动）。
// 交互只保留平移/适应窗口/选择；拖拽与连线编辑按计划禁用。
//
// ## 自适应换向（2026-10-23 修裁切缺陷）
//
// 旧实现固定横向铺列。链式任务（n1→n2→…→n5）5 个节点 = 5 列 = 1384px，
// 容器只有约 1100px → 右侧节点被裁掉。截图实测：**5 个节点只显示 3 个**，
// 而日志页显示 n1~n5 全部完成 —— 用户会以为任务只跑了 3 步。
//
// 现在按容器实测宽度自适应：放不下就换向纵向（深度铺成行），
// 链式任务自然变成一列从上到下，不需要任何横向滚动。
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

/** 容器实测宽度（`ResizeObserver` 更新；未挂载时给保守默认值）。 */
const availableWidth = ref(720);
const containerRef = ref<HTMLElement | null>(null);

const layout = computed(() => layoutDagAdaptive(props.nodes, availableWidth.value));
const positions = computed(() => layout.value.positions);
const bounds = computed(() => ({ width: layout.value.width, height: layout.value.height }));
const isVertical = computed(() => layout.value.orientation === "vertical");
const nodeWidth = computed(() =>
  isVertical.value ? DAG_VERTICAL_NODE_WIDTH : DAG_NODE_WIDTH
);

let observer: ResizeObserver | null = null;

onMounted(() => {
  if (typeof ResizeObserver === "undefined" || !containerRef.value) {
    return;
  }
  observer = new ResizeObserver((entries) => {
    const width = entries[0]?.contentRect.width;
    if (typeof width === "number" && width > 0) {
      availableWidth.value = width;
    }
  });
  observer.observe(containerRef.value);
});

onBeforeUnmount(() => {
  observer?.disconnect();
  observer = null;
});

/**
 * 边路径：横向用三次贝塞尔（左→右），纵向用上下曲线（上→下）。
 *
 * 两种方向必须分开算 —— 沿用在横向假设下的控制点，纵向时会画出
 * 穿出容器的怪线。
 */
function edgePath(from: PlanNode, to: PlanNode): string {
  const start = positions.value[from.id];
  const end = positions.value[to.id];
  if (!start || !end) {
    return "";
  }

  if (isVertical.value) {
    const x1 = start.x + nodeWidth.value / 2;
    const y1 = start.y + DAG_NODE_HEIGHT;
    const x2 = end.x + nodeWidth.value / 2;
    const y2 = end.y;
    const mid = (y1 + y2) / 2;
    return `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`;
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
  <div class="flex min-w-0 flex-col gap-2" data-test="dag" data-draggable="false" data-connectable="false">
    <div class="flex items-center gap-3">
      <span class="text-xs text-muted-foreground">
        共 {{ nodes.length }} 个节点
        <template v-if="isVertical"> · 纵向排列（横向放不下会自动换向）</template>
      </span>
    </div>

    <!-- 容器：宽度实测 → 驱动换向。纵向时允许纵向滚动（节点多也不会丢）。 -->
    <div
      ref="containerRef"
      data-test="dag-viewport"
      :data-orientation="layout.orientation"
      class="max-h-[520px] max-w-full min-w-0 overflow-auto rounded-lg border border-border bg-background p-3"
    >
      <div
        class="relative"
        :style="{
          width: `${bounds.width}px`,
          height: `${bounds.height}px`,
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
            selected === node.id ? 'ring-2 ring-primary/35' : '',
          ]"
          :data-test="`dag-node-${node.id}`"
          :style="{
            left: `${positions[node.id]?.x ?? 0}px`,
            top: `${positions[node.id]?.y ?? 0}px`,
            width: `${nodeWidth}px`,
            height: `${DAG_NODE_HEIGHT}px`,
          }"
          role="button"
          tabindex="0"
          :aria-label="nodeAriaLabel(node.id, node.title, statusOf(node.id))"
          @click="emit('select', node.id)"
          @keydown.enter.prevent="emit('select', node.id)"
        >
          <span class="line-clamp-2 text-sm font-semibold">{{ node.title }}</span>
          <span class="text-xs text-muted-foreground">
            {{ node.type }} · {{ nodeStatusLabel(statusOf(node.id)) }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
