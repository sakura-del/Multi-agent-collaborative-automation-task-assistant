import { describe, expect, it, vi } from "vitest";

import { getObservabilityOverview } from "@/api/observability";

vi.mock("@/api/client", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

import api from "@/api/client";

const OVERVIEW = {
  tasks: { total: 4, completed: 2, failed: 0, cancelled: 1, waiting_human: 1 },
  success_rate: 0.5,
  tokens: { total_used: 45_000, avg_per_task: 11_250 },
  recent: [
    { task_id: "t1", status: "completed", used_tokens: 30_000, duration_seconds: 92 },
  ],
};

describe("observability api", () => {
  it("getObservabilityOverview 取回概览", async () => {
    vi.mocked(api.get).mockResolvedValue({ status: 200, data: OVERVIEW });

    const result = await getObservabilityOverview();

    expect(api.get).toHaveBeenCalledWith("/observability/overview");
    expect(result).toEqual(OVERVIEW);
  });

  it("原样透传后端字段（不做本地重算）", async () => {
    // 成功率与平均值由后端算：前端重算会与后端口径不一致（比如分母是
    // 「已进入终态的任务」而非「全部任务」），这里锁定「不重算」这一行为。
    vi.mocked(api.get).mockResolvedValue({
      status: 200,
      data: { ...OVERVIEW, success_rate: 0.75, tokens: { total_used: 1, avg_per_task: 99 } },
    });

    const result = await getObservabilityOverview();

    expect(result.success_rate).toBe(0.75);
    expect(result.tokens.avg_per_task).toBe(99);
  });

  it("空数据也能正常解析", async () => {
    vi.mocked(api.get).mockResolvedValue({
      status: 200,
      data: {
        tasks: { total: 0, completed: 0, failed: 0, cancelled: 0, waiting_human: 0 },
        success_rate: 0,
        tokens: { total_used: 0, avg_per_task: 0 },
        recent: [],
      },
    });

    const result = await getObservabilityOverview();

    expect(result.tasks.total).toBe(0);
    expect(result.recent).toEqual([]);
  });
});
