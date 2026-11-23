/**
 * 「转人工」原因与下一步建议。
 *
 * 抽成纯函数的原因：详情页要用它，但它本身是「错误文案 → 用户该做什么」的
 * 映射表，挂载整个详情页去测成本太高，而且容易测成实现细节。
 */
import type { TaskEvent } from "@/api/types";

/**
 * 取最近一次转人工的原因（事件按时间正序，从后往前找）。
 *
 * ## 为什么需要 currentStatus（Issue #21 横幅残留）

 * 事件流是累计的：replan 成功后旧的 `task_waiting_human` 事件仍在流里。
 * 状态已离开 waiting_human（如 waiting_approval「规划已生成等你确认」）时，
 * 旧失败原因只剩误导——横幅残留会让用户反复点「重新规划」。
 */
export function latestWaitingReason(
  events: readonly TaskEvent[],
  currentStatus?: string,
): string {
  // 状态门控：只有仍在 waiting_human 时，历史转人工原因才与「当前」相关
  if (currentStatus && currentStatus !== "waiting_human") return "";
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.event_type !== "task_waiting_human") continue;
    const reason = event.payload?.["reason"];
    return typeof reason === "string" ? reason.trim() : "";
  }
  return "";
}

/**
 * 把后端错误翻译成「下一步做什么」。
 *
 * 原文照常展示（方便贴给管理员），这里只补一条可执行建议；
 * 认不出的错误返回空串，不硬编建议。
 */
export function adviceForWaitingReason(reason: string): string {
  if (!reason) return "";
  if (/Missing credentials|api_key|OPENAI_API_KEY|OPENAI_ADMIN_KEY/i.test(reason)) {
    return "平台模型没有可用的密钥。新建任务时在「模型」里选择你自己的模型；管理员配好平台密钥后，点「重新规划」重试。";
  }
  if (/timed out|timeout/i.test(reason)) {
    return "模型响应超时。到「我的模型」把该凭据换成响应更快的模型，再回本页点「重新规划」重试。";
  }
  if (/凭据已被删除|凭据不可用/.test(reason)) {
    return "任务使用的模型凭据已失效。到「我的模型」重新配置后新建任务；重新规划仍会失败。";
  }
  if (/凭据/.test(reason)) {
    return "检查「我的模型」里的凭据是否可用，然后新建任务重试。";
  }
  return "";
}
