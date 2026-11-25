import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import AdminConsoleView from "@/views/AdminConsoleView.vue";

vi.mock("@/api/admin", () => ({
  getAdminOverview: vi.fn(),
  listAdminTasks: vi.fn(),
  listAdminUsers: vi.fn(),
}));

const { toastSuccess } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
}));
vi.mock("@/lib/toast", () => ({
  toastSuccess,
  toastError: vi.fn(),
  toastInfo: vi.fn(),
  toastWarning: vi.fn(),
}));

import { getAdminOverview, listAdminTasks, listAdminUsers } from "@/api/admin";

function overview(overrides: Record<string, unknown> = {}) {
  return {
    days: 7,
    users: { total: 12, new_in_window: 3, active_in_window: 5 },
    tasks: {
      total: 40,
      created_in_window: 9,
      by_status: { completed: 20, failed: 4, running: 2 },
      failed_in_window: 2,
      finished_in_window: 8,
      failure_rate: 0.25,
      pending_action: 3,
    },
    tokens_used_in_window: 123456,
    byok_tasks_in_window: 2,
    byok_ratio: 0.2222,
    active_credentials: 4,
    ...overrides,
  };
}

const taskPage = {
  total: 2,
  items: [
    {
      task_id: "11111111-1111-1111-1111-111111111111",
      title: "平台模型任务",
      status: "completed",
      owner_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      owner_username: "alice",
      is_byok: false,
      model_label: null,
      used_tokens: 100,
      created_at: "2026-11-19T10:00:00Z",
      updated_at: "2026-11-19T10:10:00Z",
    },
    {
      task_id: "22222222-2222-2222-2222-222222222222",
      title: "BYOK 任务",
      status: "failed",
      owner_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
      owner_username: "bob",
      is_byok: true,
      model_label: "DeepSeek 官方（deepseek-chat）",
      used_tokens: 50,
      created_at: "2026-11-20T09:00:00Z",
      updated_at: "2026-11-20T09:05:00Z",
    },
  ],
};

const userPage = {
  total: 1,
  items: [
    {
      user_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
      username: "alice",
      email: "alice@example.com",
      role: "admin",
      is_active: true,
      created_at: "2026-11-01T00:00:00Z",
      plan_name: "专业版",
      task_count: 7,
      credential_count: 2,
    },
  ],
};

describe("AdminConsoleView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getAdminOverview).mockResolvedValue(overview() as never);
    vi.mocked(listAdminTasks).mockResolvedValue(taskPage as never);
    vi.mocked(listAdminUsers).mockResolvedValue(userPage as never);
  });

  it("使用 Pagination 分页（点击 admin-next 翻页）并在手动刷新后调用 toastSuccess", async () => {
    vi.mocked(listAdminTasks).mockResolvedValue({ ...taskPage, total: 45 } as never);
    const wrapper = mount(AdminConsoleView);
    await flushPromises();

    // 使用 Pagination 组件（含 nav[aria-label='任务列表分页'] 与 admin-next）
    expect(wrapper.find('nav[aria-label="任务列表分页"]').exists()).toBe(true);
    await wrapper.get('[data-test="admin-next"]').trigger("click");
    await flushPromises();
    expect(listAdminTasks).toHaveBeenCalledWith(
      expect.objectContaining({ offset: 20, limit: 20 }),
    );

    // 手动刷新触发 toastSuccess
    expect(toastSuccess).not.toHaveBeenCalled();
    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("刷新"))!
      .trigger("click");
    await flushPromises();
    expect(toastSuccess).toHaveBeenCalledWith("已刷新后台数据");
  });

  it("ErrorBanner 关闭后可复现同文报错", async () => {
    vi.mocked(getAdminOverview).mockRejectedValue(new Error("权限失效"));
    const wrapper = mount(AdminConsoleView);
    await flushPromises();

    expect(wrapper.get('[data-test="admin-error"]').text()).toContain("权限失效");
    await wrapper.get('[data-test="admin-error"] button').trigger("click");
    await flushPromises();

    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("刷新"))!
      .trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-test="admin-error"]').text()).toContain("权限失效");
  });

  it("渲染 6 张 KPI 卡", async () => {
    const wrapper = mount(AdminConsoleView);
    await flushPromises();

    expect(wrapper.get('[data-test="admin-kpi-users"]').text()).toBe("12");
    expect(wrapper.get('[data-test="admin-kpi-tasks"]').text()).toBe("40");
    expect(wrapper.get('[data-test="admin-kpi-failure"]').text()).toBe("25.0%");
    expect(wrapper.get('[data-test="admin-kpi-pending"]').text()).toBe("3");
    expect(wrapper.get('[data-test="admin-kpi-tokens"]').text()).toBe(
      123456..toLocaleString("zh-CN")
    );
    expect(wrapper.get('[data-test="admin-kpi-byok"]').text()).toBe("22.2%");
  });

  it("任务表展示所有者/状态/模型与分页总数", async () => {
    const wrapper = mount(AdminConsoleView);
    await flushPromises();

    const rows = wrapper.findAll('[data-test="admin-task-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0].text()).toContain("平台模型任务");
    expect(rows[0].text()).toContain("alice");
    expect(rows[0].text()).toContain("已完成");
    expect(rows[0].text()).toContain("平台模型");
    expect(rows[1].text()).toContain("BYOK");
    expect(rows[1].text()).toContain("失败");
    expect(rows[1].text()).toContain("DeepSeek 官方（deepseek-chat）");
    expect(wrapper.get('[data-test="admin-total"]').text()).toContain("2");
  });

  it("状态筛选回第一页并重新查询", async () => {
    const wrapper = mount(AdminConsoleView);
    await flushPromises();
    vi.mocked(listAdminTasks).mockClear();

    await wrapper.get('[data-test="admin-task-status"]').setValue("failed");
    await flushPromises();

    expect(listAdminTasks).toHaveBeenCalledWith(
      expect.objectContaining({ status: "failed", offset: 0 })
    );
  });

  it("切到用户表时加载并展示套餐与计数", async () => {
    const wrapper = mount(AdminConsoleView);
    await flushPromises();

    await wrapper.get('[data-test="admin-tab-users"]').trigger("click");
    await flushPromises();

    expect(listAdminUsers).toHaveBeenCalledTimes(1);
    const rows = wrapper.findAll('[data-test="admin-user-row"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].text()).toContain("alice");
    expect(rows[0].text()).toContain("管理员");
    expect(rows[0].text()).toContain("专业版");
    expect(rows[0].text()).toContain("7");
    expect(rows[0].text()).toContain("2");
  });

  it("状态筛选包含「已暂停」，且首屏加载完成前不误报共 0 条（Issue #56）", async () => {
    let resolveTasks!: (value: unknown) => void;
    vi.mocked(listAdminTasks).mockReturnValue(
      new Promise((resolve) => {
        resolveTasks = resolve;
      }) as never
    );

    const wrapper = mount(AdminConsoleView);
    expect(wrapper.get('[data-test="admin-total"]').text()).toContain("加载中");
    expect(wrapper.get('[data-test="admin-total"]').text()).not.toContain("共 0 条");

    const options = wrapper
      .findAll('[data-test="admin-task-status"] option')
      .map((opt) => ({ value: opt.attributes("value"), label: opt.text() }));
    expect(options).toContainEqual({ value: "paused", label: "已暂停" });

    resolveTasks(taskPage);
    await flushPromises();
    expect(wrapper.get('[data-test="admin-total"]').text()).toContain("共 2 条");
  });
});
