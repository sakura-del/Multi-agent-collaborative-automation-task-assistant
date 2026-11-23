import { describe, expect, it } from "vitest";

import type { TaskEvent } from "@/api/types";

import { adviceForWaitingReason, latestWaitingReason } from "./waiting-reason";

function event(id: number, type: string, payload: Record<string, unknown> = {}): TaskEvent {
  return {
    event_id: id,
    event_type: type,
    occurred_at: "2026-11-17T06:40:00Z",
    payload,
  } as TaskEvent;
}

describe("latestWaitingReason", () => {
  it("取最近一次转人工的原因", () => {
    const events = [
      event(1, "task_waiting_human", { reason: "旧的失败" }),
      event(2, "task_status"),
      event(3, "task_waiting_human", { reason: "规划失败: Request timed out." }),
    ];
    expect(latestWaitingReason(events)).toBe("规划失败: Request timed out.");
  });

  it("没有转人工事件时返回空串", () => {
    expect(latestWaitingReason([event(1, "task_status")])).toBe("");
    expect(latestWaitingReason([])).toBe("");
  });

  it("原因缺失或非字符串时不抛错", () => {
    expect(latestWaitingReason([event(1, "task_waiting_human")])).toBe("");
    expect(latestWaitingReason([event(1, "task_waiting_human", { reason: 42 })])).toBe("");
  });
});

describe("adviceForWaitingReason", () => {
  it("缺密钥：引导选自己的模型或找管理员", () => {
    const advice = adviceForWaitingReason(
      "规划失败: Missing credentials. Please pass an `api_key` or set the `OPENAI_API_KEY`",
    );
    expect(advice).toContain("选择你自己的模型");
    expect(advice).toContain("管理员");
  });

  it("超时：引导换更快的模型", () => {
    expect(adviceForWaitingReason("规划失败: Request timed out.")).toContain("响应更快");
  });

  it("凭据失效：引导去我的模型检查", () => {
    expect(adviceForWaitingReason("任务指定的模型凭据已被删除，无法继续")).toContain("我的模型");
  });

  it("认不出的错误不硬编建议", () => {
    expect(adviceForWaitingReason("something odd happened")).toBe("");
    expect(adviceForWaitingReason("")).toBe("");
  });
});

describe("latestWaitingReason 的状态门控（Issue #21）", () => {
  const failure = { event_id: 1, event_type: "task_waiting_human", payload: { reason: "规划失败: 429" } };
  const planReady = { event_id: 2, event_type: "plan_ready", payload: { revision: 2 } };

  function ev(id: number, type: string, payload: Record<string, unknown> = {}) {
    return { event_id: id, event_type: type, payload } as never;
  }

  it("状态已离开 waiting_human：旧失败原因不再显示", () => {
    const events = [ev(1, "task_waiting_human", { reason: "规划失败: 429" }), ev(2, "plan_ready", { revision: 2 })];
    expect(latestWaitingReason(events, "waiting_approval")).toBe("");
    expect(latestWaitingReason(events, "running")).toBe("");
    expect(latestWaitingReason(events, "completed")).toBe("");
  });

  it("仍在 waiting_human：原因照常显示", () => {
    const events = [ev(1, "task_waiting_human", { reason: "规划失败: 429" })];
    expect(latestWaitingReason(events, "waiting_human")).toBe("规划失败: 429");
    expect(latestWaitingReason(events)).toBe("规划失败: 429");
  });
});
