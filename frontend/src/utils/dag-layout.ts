import type { PlanNode, SubtaskStatus } from "@/api/types";

// DAG 几何：固定节点尺寸，按拓扑深度分列、同层按出现次序分行（确定性、不受状态影响）
export const DAG_NODE_WIDTH = 200;
/**
 * 节点高度 84px（原 68）。
 *
 * 实测：标题两行 + 副标题一行需要约 76px 内容高度，68px 会溢出 ——
 * 截图里第二行文字压在副标题上（「…书目信息与证据证据」叠字）。
 * 加高到 84 保证两行标题不挤压元信息行。
 */
export const DAG_NODE_HEIGHT = 84;
export const DAG_COLUMN_GAP = 96;
export const DAG_ROW_GAP = 24;

/** 纵向排列时的几何（换向后节点变宽，因为纵向布局横向空间充裕）。 */
export const DAG_VERTICAL_NODE_WIDTH = 300;
export const DAG_VERTICAL_ROW_GAP = 40;

/**
 * 布局方向。
 *
 * - `horizontal`：拓扑深度铺成列（默认，适合宽屏多分支）；
 * - `vertical`：拓扑深度铺成行（**链式任务**用，避免横向被裁切）。
 */
export type DagOrientation = "horizontal" | "vertical";

export interface DagPoint {
  x: number;
  y: number;
}

export interface DagLayout {
  positions: Record<string, DagPoint>;
  width: number;
  height: number;
  orientation: DagOrientation;
}

/** 计算每个节点的绝对坐标；只依赖拓扑结构，因此状态更新不会让节点跳动 */
export function layoutDag(nodes: PlanNode[]): Record<string, DagPoint> {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const depthCache = new Map<string, number>();

  function depth(nodeId: string, seen: Set<string> = new Set()): number {
    const cached = depthCache.get(nodeId);
    if (cached !== undefined) {
      return cached;
    }
    if (seen.has(nodeId)) {
      return 0; // 依赖成环时保护：按首层处理
    }
    seen.add(nodeId);
    const node = byId.get(nodeId);
    const parents = (node?.dependencies ?? []).filter((parent) => byId.has(parent));
    const value = parents.length === 0 ? 0 : Math.max(...parents.map((p) => depth(p, seen))) + 1;
    depthCache.set(nodeId, value);
    return value;
  }

  const rowCursor = new Map<number, number>();
  const positions: Record<string, DagPoint> = {};
  for (const node of nodes) {
    const column = depth(node.id);
    const row = rowCursor.get(column) ?? 0;
    rowCursor.set(column, row + 1);
    positions[node.id] = {
      x: column * (DAG_NODE_WIDTH + DAG_COLUMN_GAP),
      y: row * (DAG_NODE_HEIGHT + DAG_ROW_GAP),
    };
  }
  return positions;
}

/**
 * 按可用宽度选择布局方向，计算坐标与包围盒。
 *
 * ## 为什么需要换向（实测缺陷）
 *
 * 旧实现固定横向。链式任务（n1→n2→n3→n4→n5）5 个节点 = 5 列，
 * 宽度 5×200 + 4×96 = **1384px**，而容器只有约 1100px →
 * 右侧 n4/n5 被裁掉，用户第一眼看到的是**被切断的图**（截图实测：
 * 5 个节点只显示 3 个）。
 *
 * 横向滚动条在视觉上几乎不可见，等于「节点丢了」。
 *
 * 所以：横向放不下时**换向为纵向**（深度铺成行），链式任务自然变成
 * 一列从上到下 —— 不需要任何滚动，窄屏也友好。
 */
export function layoutDagAdaptive(
  nodes: PlanNode[],
  availableWidth: number,
): DagLayout {
  const horizontal = layoutDag(nodes);
  const horizontalBounds = dagBounds(horizontal);
  const byId = new Map(nodes.map((node) => [node.id, node]));

  // 横向能放下（含一点余量）就保持横向
  if (horizontalBounds.width <= Math.max(availableWidth, 320)) {
    return {
      positions: horizontal,
      width: horizontalBounds.width,
      height: horizontalBounds.height,
      orientation: "horizontal",
    };
  }

  // 换向为纵向：深度 → y，同层序号 → x
  const depthCache = new Map<string, number>();
  function depth(nodeId: string, seen: Set<string> = new Set()): number {
    const cached = depthCache.get(nodeId);
    if (cached !== undefined) {
      return cached;
    }
    if (seen.has(nodeId)) {
      return 0;
    }
    seen.add(nodeId);
    const node = byId.get(nodeId);
    const parents = (node?.dependencies ?? []).filter((parent) => byId.has(parent));
    const value = parents.length === 0 ? 0 : Math.max(...parents.map((p) => depth(p, seen))) + 1;
    depthCache.set(nodeId, value);
    return value;
  }

  const columnCursor = new Map<number, number>();
  const vertical: Record<string, DagPoint> = {};
  for (const node of nodes) {
    const row = depth(node.id);
    const column = columnCursor.get(row) ?? 0;
    columnCursor.set(row, column + 1);
    vertical[node.id] = {
      x: column * (DAG_VERTICAL_NODE_WIDTH + 32),
      y: row * (DAG_NODE_HEIGHT + DAG_VERTICAL_ROW_GAP),
    };
  }

  const maxX = Math.max(...Object.values(vertical).map((p) => p.x), 0);
  const maxY = Math.max(...Object.values(vertical).map((p) => p.y), 0);
  return {
    positions: vertical,
    width: maxX + DAG_VERTICAL_NODE_WIDTH,
    height: maxY + DAG_NODE_HEIGHT,
    orientation: "vertical",
  };
}

const STATUS_LABELS: Record<SubtaskStatus, string> = {
  pending: "待调度",
  ready: "就绪",
  running: "执行中",
  validating: "校验中",
  completed: "已完成",
  failed: "失败",
  retrying: "重试中",
  waiting_human: "待人工介入",
  paused: "已暂停",
  terminated: "已终止",
  cancelled: "已取消",
};

const STATUS_TONES: Record<SubtaskStatus, string> = {
  pending: "neutral",
  ready: "neutral",
  running: "active",
  validating: "active",
  completed: "success",
  failed: "danger",
  retrying: "attention",
  waiting_human: "attention",
  paused: "attention",
  terminated: "neutral",
  cancelled: "neutral",
};

export function nodeStatusLabel(status: SubtaskStatus | undefined): string {
  return status ? STATUS_LABELS[status] ?? status : "待调度";
}

export function nodeStatusTone(status: SubtaskStatus | undefined): string {
  return status ? STATUS_TONES[status] ?? "neutral" : "neutral";
}

/** 无障碍标签：状态必须在标签里可区分（测试与读屏都依赖它） */
export function nodeAriaLabel(nodeId: string, title: string, status: SubtaskStatus | undefined): string {
  return `节点 ${nodeId} ${title}（${nodeStatusLabel(status)}）`;
}

/** 内容包围盒，用于"适应窗口" */
export function dagBounds(positions: Record<string, DagPoint>): { width: number; height: number } {
  const points = Object.values(positions);
  if (points.length === 0) {
    return { width: DAG_NODE_WIDTH, height: DAG_NODE_HEIGHT };
  }
  const maxX = Math.max(...points.map((point) => point.x));
  const maxY = Math.max(...points.map((point) => point.y));
  return { width: maxX + DAG_NODE_WIDTH, height: maxY + DAG_NODE_HEIGHT };
}
