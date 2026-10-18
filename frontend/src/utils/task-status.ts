import type { TaskStatus } from "@/api/types";

// 任务状态 → 粗略进度（列表页展示用；精确进度来自子任务快照）
const PROGRESS: Record<TaskStatus, number> = {
  created: 5,
  planning: 15,
  waiting_approval: 25,
  running: 55,
  retrying: 55,
  paused: 55,
  reviewing: 85,
  waiting_human: 60,
  completed: 100,
  failed: 100,
  cancelled: 100,
};

export function taskProgress(status: TaskStatus): number {
  return PROGRESS[status] ?? 0;
}

/** 是否处于终态（用于列表筛选/操作禁用） */
export function isTerminal(status: TaskStatus): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}

/** ISO 时间 → 本地可读文本；空值返回占位符 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "—";
  }
  return parsed.toLocaleString("zh-CN", { hour12: false });
}
