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

/**
 * 是否「需要我行动」。
 *
 * 只有两个状态真正卡在用户这边：
 * - `waiting_approval`：规划生成完毕，等用户确认才能执行；
 * - `waiting_human`：校验失败转人工，等用户介入。
 *
 * 其余状态都是系统自己推进或已终结。任务列表的「待我确认」汇总就基于它——
 * 这是列表里**唯一**需要用户行动的信息，必须比其它计数更醒目。
 */
export function needsUserAction(status: TaskStatus): boolean {
  return status === "waiting_approval" || status === "waiting_human";
}

/** 是否「进行中」（系统正在推进，用户无需操作） */
export function isActive(status: TaskStatus): boolean {
  return !isTerminal(status) && !needsUserAction(status);
}

/**
 * 把任务标题拆成「主标题 + 次级标记」。
 *
 * ## 为什么需要
 *
 * 实测任务列表 20 行里 19 行标题几乎一样：
 * `检索增强生成评估方法综述（Issue #6 复验 #3）`。括号里的标记是**唯一**的区分依据，
 * 却和主题同样权重，视觉上是一堵同质化的墙。
 *
 * 拆开后主行只留主题，标记降到次级灰字 —— 信息一条不少，但层级清楚了。
 *
 * ## 只拆最外层括号
 *
 * `综述（第一轮（含附录））` → main=`综述`, meta=`第一轮（含附录）`。
 * 用括号深度计数而不是正则，嵌套时不会把主题切碎。
 * 全角 `（）` 与半角 `()` 都识别（中文标题里两种都常见）。
 */
export function splitTaskTitle(title: string): { main: string; meta: string | null } {
  const text = (title ?? "").trim();
  if (!text) {
    return { main: "", meta: null };
  }

  // 从末尾找闭合括号，再按深度找配对的开启括号
  const closing = text[text.length - 1];
  const pairs: Record<string, string> = { "）": "（", ")": "(" };
  const opening = pairs[closing];
  if (!opening) {
    return { main: text, meta: null };
  }

  let depth = 0;
  for (let index = text.length - 1; index >= 0; index -= 1) {
    const char = text[index];
    if (char === closing) {
      depth += 1;
    } else if (char === opening) {
      depth -= 1;
      if (depth === 0) {
        // 只有「括号在标题末尾」才算标记；`摘要（草稿）补充` 这种不动
        if (index === 0) {
          return { main: text, meta: null };
        }
        const main = text.slice(0, index).trim();
        const meta = text.slice(index + 1, -1).trim();
        if (!main || !meta) {
          return { main: text, meta: null };
        }
        return { main, meta };
      }
    }
  }
  return { main: text, meta: null };
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
