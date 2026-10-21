import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import WorkspaceDetailView from "@/views/WorkspaceDetailView.vue";

vi.mock("@/api/collaboration", () => ({
  getWorkspace: vi.fn(),
  listMembers: vi.fn(),
  removeMember: vi.fn(),
  changeMemberRole: vi.fn(),
  leaveWorkspace: vi.fn(),
  createInvite: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { id: "w1" } }),
}));

import {
  changeMemberRole,
  createInvite,
  getWorkspace,
  leaveWorkspace,
  listMembers,
  removeMember,
} from "@/api/collaboration";

const stubs = {
  RouterLink: { template: "<a><slot /></a>", props: ["to"] },
};

function workspace(overrides: Record<string, unknown> = {}) {
  return {
    workspace_id: "w1",
    name: "张三课题组",
    slug: "zhangsan-lab",
    owner_id: "u1",
    seat_limit: 5,
    seat_used: 2,
    my_role: "owner",
    created_at: "2026-10-21T00:00:00Z",
    ...overrides,
  };
}

function member(overrides: Record<string, unknown> = {}) {
  return {
    user_id: "u1",
    username: "张三",
    email: "zhangsan@example.com",
    role: "owner",
    joined_at: "2026-10-21T00:00:00Z",
    ...overrides,
  };
}

function mountView() {
  return mount(WorkspaceDetailView, { global: { stubs } });
}

beforeEach(() => {
  for (const fn of [
    getWorkspace,
    listMembers,
    removeMember,
    changeMemberRole,
    leaveWorkspace,
    createInvite,
  ]) {
    vi.mocked(fn).mockReset();
  }
});

describe("WorkspaceDetailView", () => {
  it("展示工作区名与席位用量", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace() as never);
    vi.mocked(listMembers).mockResolvedValue([member()] as never);

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="workspace-detail-name"]').text()).toBe("张三课题组");
    expect(wrapper.find('[data-test="workspace-seat-usage"]').text()).toContain("席位 2/5");
  });

  it("席位满时提示需扩容且禁用生成邀请码", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace({ seat_used: 5, seat_limit: 5 }) as never);
    vi.mocked(listMembers).mockResolvedValue([member()] as never);

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="workspace-seat-usage"]').text()).toContain("已满");
    const btn = wrapper.get('[data-test="workspace-generate-invite"]');
    expect(btn.attributes("disabled")).toBeDefined();
  });

  it("owner 能改成员角色与移除成员", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace() as never);
    vi.mocked(listMembers).mockResolvedValue([
      member(),
      member({ user_id: "u2", username: "李四", role: "member" }),
    ] as never);

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="member-role-select-u2"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="member-remove-u2"]').exists()).toBe(true);
    // owner 自己不能被移除
    expect(wrapper.find('[data-test="member-remove-u1"]').exists()).toBe(false);
    // owner 自己不能改角色
    expect(wrapper.find('[data-test="member-role-select-u1"]').exists()).toBe(false);
  });

  it("admin 不能改角色，但能移除 member", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace({ my_role: "admin" }) as never);
    vi.mocked(listMembers).mockResolvedValue([
      member(),
      member({ user_id: "u2", username: "李四", role: "member" }),
    ] as never);

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="member-role-select-u2"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="member-remove-u2"]').exists()).toBe(true);
  });

  it("admin 不能移除同级 admin", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace({ my_role: "admin" }) as never);
    vi.mocked(listMembers).mockResolvedValue([
      member(),
      member({ user_id: "u3", username: "王五", role: "admin" }),
    ] as never);

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="member-remove-u3"]').exists()).toBe(false);
  });

  it("member / viewer 看不到邀请与移除入口，只看到退出", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace({ my_role: "viewer" }) as never);
    vi.mocked(listMembers).mockResolvedValue([
      member(),
      member({ user_id: "u2", username: "李四", role: "viewer" }),
    ] as never);

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="workspace-generate-invite"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="member-remove-u2"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="workspace-leave"]').exists()).toBe(true);
  });

  it("owner 看不到退出按钮（必须先转让）", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace() as never);
    vi.mocked(listMembers).mockResolvedValue([member()] as never);

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="workspace-leave"]').exists()).toBe(false);
  });

  it("移除成员后刷新列表", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace() as never);
    vi.mocked(listMembers).mockResolvedValue([
      member(),
      member({ user_id: "u2", username: "李四", role: "member" }),
    ] as never);
    vi.mocked(removeMember).mockResolvedValue(undefined as never);

    const wrapper = mountView();
    await flushPromises();

    vi.mocked(listMembers).mockResolvedValue([member()] as never);
    await wrapper.get('[data-test="member-remove-u2"]').trigger("click");
    await flushPromises();

    expect(removeMember).toHaveBeenCalledWith("w1", "u2");
    expect(wrapper.find('[data-test="member-row-u2"]').exists()).toBe(false);
  });

  it("改角色调用接口", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace() as never);
    vi.mocked(listMembers).mockResolvedValue([
      member(),
      member({ user_id: "u2", username: "李四", role: "member" }),
    ] as never);
    vi.mocked(changeMemberRole).mockResolvedValue(
      member({ user_id: "u2", role: "viewer" }) as never
    );

    const wrapper = mountView();
    await flushPromises();

    await wrapper.get('[data-test="member-role-select-u2"]').setValue("viewer");
    await flushPromises();

    expect(changeMemberRole).toHaveBeenCalledWith("w1", "u2", "viewer");
  });

  it("加载失败显示错误", async () => {
    vi.mocked(getWorkspace).mockRejectedValue(new Error("工作区不存在"));

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="workspace-detail-error"]').text()).toContain("工作区不存在");
  });

  it("生成邀请码后展示码", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace() as never);
    vi.mocked(listMembers).mockResolvedValue([member()] as never);
    vi.mocked(createInvite).mockResolvedValue({
      invite_id: "i1",
      code: "xyz789",
      role: "member",
      expires_at: "2026-10-28T00:00:00Z",
      workspace_id: "w1",
    } as never);

    const wrapper = mountView();
    await flushPromises();

    await wrapper.get('[data-test="workspace-generate-invite"]').trigger("click");
    await flushPromises();

    const box = wrapper.find('[data-test="workspace-detail-invite"]');
    expect(box.exists()).toBe(true);
    expect(box.text()).toContain("xyz789");
  });
});
