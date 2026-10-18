import type { PlanNode, SubtaskStatus } from "@/api/types";

// DAG 几何：固定节点尺寸，按拓扑深度分列、同层按出现次序分行（确定性、不受状态影响）
export const DAG_NODE_WIDTH = 200;
export const DAG_NODE_HEIGHT = 68;
export const DAG_COLUMN_GAP = 96;
export const DAG_ROW_GAP = 24;

export interface DagPoint {
  x: number;
  y: number;
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
