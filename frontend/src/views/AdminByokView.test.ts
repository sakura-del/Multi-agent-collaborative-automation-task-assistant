import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import AdminByokView from "@/views/AdminByokView.vue";

vi.mock("@/api/admin", () => ({
  getByokOverview: vi.fn(),
}));

import { getByokOverview } from "@/api/admin";

function overview(overrides: Record<string, unknown> = {}) {
  return {
    window_days: 30,
    period_start: "2026-10-17T00:00:00Z",
    period_end: "2026-11-16T00:00:00Z",
    totals: {
      tasks: 3,
      tokens: 350,
      byok_tasks: 1,
      byok_tokens: 50,
      byok_task_share: 0.3333,
      byok_token_share: 0.1429,
    },
    users: {
      active: 2,
      byok_active: 1,
      with_credentials: 2,
      with_active_credentials: 1,
      byok_penetration: 0.5,
      credential_penetration: 1,
    },
    credentials: { total: 2, active: 1, invalid: 1, disabled: 0 },
    trend: [
      { date: "2026-11-15", tasks: 2, tokens: 250, byok_tasks: 0, byok_tokens: 0 },
      { date: "2026-11-16", tasks: 1, tokens: 100, byok_tasks: 1, byok_tokens: 50 },
    ],
    providers: [{ provider: "deepseek", model: "deepseek-chat", tasks: 1, tokens: 50 }],
    top_users: [
      {
        user_id: "u1",
        username: "alice",
        tasks: 2,
        tokens: 150,
        byok_tasks: 1,
        byok_tokens: 50,
        byok_token_share: 0.3333,
      },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(getByokOverview).mockReset();
});

describe("AdminByokView", () => {
  it("四个渗透率各自带分母，不能只显示一个数字", async () => {
    vi.mocked(getByokOverview).mockResolvedValue(overview() as never);

    const wrapper = mount(AdminByokView);
    await flushPromises();

    expect(wrapper.get("[data-test='byok-task-share']").text()).toBe("33.3%");
    expect(wrapper.get("[data-test='byok-token-share']").text()).toBe("14.3%");
    expect(wrapper.get("[data-test='byok-user-penetration']").text()).toBe("50.0%");
    expect(wrapper.get("[data-test='byok-credential-penetration']").text()).toBe("100.0%");

    expect(wrapper.text()).toContain("1 / 3 个任务");
    expect(wrapper.text()).toContain("跑过 BYOK：1 / 活跃 2 人");
    expect(wrapper.get("[data-test='byok-credentials']").text()).toContain("失效 1");
  });

  it("渲染趋势、厂商与 Top 用户", async () => {
    vi.mocked(getByokOverview).mockResolvedValue(overview() as never);

    const wrapper = mount(AdminByokView);
    await flushPromises();

    expect(wrapper.find("[data-test='byok-trend-row-2026-11-16']").exists()).toBe(true);
    expect(
      wrapper.get("[data-test='byok-provider-deepseek-deepseek-chat']").text(),
    ).toContain("deepseek-chat");
    expect(wrapper.get("[data-test='byok-top-user-alice']").text()).toContain("1 / 2");
  });

  it("切换窗口重新请求（7 天）", async () => {
    vi.mocked(getByokOverview).mockResolvedValue(overview() as never);

    const wrapper = mount(AdminByokView);
    await flushPromises();
    expect(getByokOverview).toHaveBeenCalledWith(30);

    await wrapper.get("[data-test='byok-days-7']").trigger("click");
    await flushPromises();

    expect(getByokOverview).toHaveBeenCalledWith(7);
  });

  it("窗口无任务时显示空态，不渲染一排 0", async () => {
    vi.mocked(getByokOverview).mockResolvedValue(
      overview({
        totals: {
          tasks: 0,
          tokens: 0,
          byok_tasks: 0,
          byok_tokens: 0,
          byok_task_share: 0,
          byok_token_share: 0,
        },
      }) as never,
    );

    const wrapper = mount(AdminByokView);
    await flushPromises();

    expect(wrapper.find("[data-test='byok-empty']").exists()).toBe(true);
    expect(wrapper.find("[data-test='byok-task-share']").exists()).toBe(false);
  });

  it("加载失败显示错误，不把看板伪装成空数据", async () => {
    vi.mocked(getByokOverview).mockRejectedValue(new Error("403 需要管理员权限"));

    const wrapper = mount(AdminByokView);
    await flushPromises();

    expect(wrapper.get("[data-test='byok-error']").text()).toContain("403");
    expect(wrapper.find("[data-test='byok-empty']").exists()).toBe(false);
  });
});
