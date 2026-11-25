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

const push = vi.fn();

vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { id: "w1" } }),
  useRouter: () => ({ push }),
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
    // 先点移除只弹出确认条，不直接删
    await wrapper.get('[data-test="member-remove-u2"]').trigger("click");
    await flushPromises();
    expect(removeMember).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="member-remove-confirm-u2"]').exists()).toBe(true);

    // 点取消收回确认条
    await wrapper.get('[data-test="member-remove-cancel-u2"]').trigger("click");
    await flushPromises();
    expect(removeMember).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="member-remove-confirm-u2"]').exists()).toBe(false);

    // 再次点移除并确认执行
    await wrapper.get('[data-test="member-remove-u2"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-test="member-remove-confirm-u2"]').trigger("click");
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

describe("WorkspaceDetailView：真实缺陷修复（2026-10-26）", () => {
  /**
   * ## 缺陷 6：退出团队后报「工作区不存在」
   *
   * `handleLeave()` 成功后调用 `refresh()`，而 `refresh()` 会 `GET /workspaces/{id}`
   * —— 此时用户**已不是成员**，后端按「不泄漏存在性」统一返回 404。
   *
   * 于是用户刚成功退出，立刻看到一条错误横幅「工作区不存在」。
   * 正确行为：退出成功后回到团队列表。
   */
  it("退出团队成功后跳回列表（不再 refresh 到 404）", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace({ my_role: "member" }) as never);
    vi.mocked(listMembers).mockResolvedValue([member({ role: "member" })] as never);
    vi.mocked(leaveWorkspace).mockResolvedValue(undefined as never);

    const wrapper = mountView();
    await flushPromises();

    const leaveBtn = wrapper.find('[data-test="workspace-leave"]');
    expect(leaveBtn.exists()).toBe(true);
    await leaveBtn.trigger("click");
    await flushPromises();

    // 先弹出二次确认条，支持取消
    expect(push).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="workspace-leave-confirm"]').exists()).toBe(true);
    await wrapper.get('[data-test="workspace-leave-cancel"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-test="workspace-leave-confirm"]').exists()).toBe(false);

    // 确认退出后跳走（不再 refresh 到 404）
    await wrapper.get('[data-test="workspace-leave"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-test="workspace-leave-confirm"]').trigger("click");
    await flushPromises();

    // 必须跳走（而不是留在原地显示 404 错误）
    expect(push).toHaveBeenCalled();
    // 目标是团队列表：`{ name: "workspaces" }`（具名路由，不是字符串路径）
    const target = vi.mocked(push).mock.calls[0][0] as { name?: string };
    expect(target?.name).toBe("workspaces");
  });

  it("退出请求失败时留在原页并显示错误（不能假装成功跳走）", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace({ my_role: "member" }) as never);
    vi.mocked(listMembers).mockResolvedValue([member({ role: "member" })] as never);
    vi.mocked(leaveWorkspace).mockRejectedValue(new Error("网络错误"));
    vi.mocked(push).mockClear();

    const wrapper = mountView();
    await flushPromises();

    await wrapper.find('[data-test="workspace-leave"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-test="workspace-leave-confirm"]').trigger("click");
    await flushPromises();

    expect(push).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="workspace-detail-error"]').text()).toContain("网络错误");
  });

  /**
   * ## 缺陷 7：席位已满提示用了硬编码颜色
   *
   * 原代码 `<span class="text-amber-600">` —— 违反 AGENTS §6.5.3
   * 「只用 tokens，不写一次性颜色」。深色模式下更明显（amber-600 在深底上偏暗）。
   */
  it("席位已满提示用 token（text-warning）而不是硬编码颜色", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(
      workspace({ seat_used: 5, seat_limit: 5 }) as never,
    );
    vi.mocked(listMembers).mockResolvedValue([member()] as never);

    const wrapper = mountView();
    await flushPromises();

    const usage = wrapper.find('[data-test="workspace-seat-usage"]');
    expect(usage.text()).toContain("已满");
    // 不能出现一次性 Tailwind 调色板类
    expect(usage.html()).not.toMatch(/text-amber-\d/);
    expect(usage.html()).toMatch(/text-warning/);
  });

  /**
   * ## 缺陷 8：邀请码有效期未展示
   *
   * 接口返回 `expires_at`（默认 7 天），页面只显示码与角色 ——
   * 用户以为永久有效，过期后对方兑不了还找不到原因。
   */
  it("生成的邀请码要显示有效期", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace() as never);
    vi.mocked(listMembers).mockResolvedValue([member()] as never);
    vi.mocked(createInvite).mockResolvedValue({
      invite_id: "i1",
      code: "CODE456",
      role: "member",
      expires_at: "2026-11-02T00:00:00Z",
      workspace_id: "w1",
    } as never);

    const wrapper = mountView();
    await flushPromises();

    await wrapper.find('[data-test="workspace-generate-invite"]').trigger("click");
    await flushPromises();

    const box = wrapper.find('[data-test="workspace-detail-invite"]');
    expect(box.exists()).toBe(true);
    expect(box.text()).toMatch(/2026|有效期|过期/);
  });

  it("改角色乐观更新失败时立即还原原角色并展示 800ms 行级温和回弹提示", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(getWorkspace).mockResolvedValue(workspace() as never);
      vi.mocked(listMembers).mockResolvedValue([
        member(),
        member({ user_id: "u2", username: "李四", role: "member" }),
      ] as never);
      vi.mocked(changeMemberRole).mockRejectedValue(new Error("权限不足"));

      const wrapper = mountView();
      await flushPromises();

      const row = wrapper.get('[data-test="member-row-u2"]');
      expect(wrapper.get('[data-test="member-role-u2"]').text()).toBe("成员");

      await wrapper.get('[data-test="member-role-select-u2"]').setValue("viewer");
      await flushPromises();

      expect(wrapper.get('[data-test="member-role-u2"]').text()).toBe("成员");
      expect(row.classes()).toContain("ring-destructive/60");
      expect(wrapper.get('[data-test="workspace-detail-error"]').text()).toContain("权限不足");

      vi.advanceTimersByTime(850);
      await flushPromises();
      expect(row.classes()).not.toContain("ring-destructive/60");
    } finally {
      vi.useRealTimers();
    }
  });

  it("同时修改两位成员角色时各自保持独立行级锁定，互不提前解锁", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace() as never);
    vi.mocked(listMembers).mockResolvedValue([
      member(),
      member({ user_id: "u2", username: "李四", role: "member" }),
      member({ user_id: "u3", username: "王五", role: "member" }),
    ] as never);

    let resolveU2!: (v: unknown) => void;
    let resolveU3!: (v: unknown) => void;
    vi.mocked(changeMemberRole)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveU2 = resolve;
          }) as never,
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveU3 = resolve;
          }) as never,
      );

    const wrapper = mountView();
    await flushPromises();

    const sel2 = wrapper.get('[data-test="member-role-select-u2"]');
    const sel3 = wrapper.get('[data-test="member-role-select-u3"]');
    const rm2 = wrapper.get('[data-test="member-remove-u2"]');

    await sel2.setValue("viewer");
    await sel3.setValue("admin");
    await wrapper.vm.$nextTick();

    // u2 与 u3 均在请求中，各自控件保持 disabled
    expect(sel2.attributes("disabled")).toBeDefined();
    expect(rm2.attributes("disabled")).toBeDefined();
    expect(sel3.attributes("disabled")).toBeDefined();

    // u3 先完成：u3 解锁，但 u2 仍必须保持 disabled
    resolveU3(member({ user_id: "u3", role: "admin" }));
    await flushPromises();
    expect(sel3.attributes("disabled")).toBeUndefined();
    expect(sel2.attributes("disabled")).toBeDefined();
    expect(rm2.attributes("disabled")).toBeDefined();

    // u2 完成后解锁
    resolveU2(member({ user_id: "u2", role: "viewer" }));
    await flushPromises();
    expect(sel2.attributes("disabled")).toBeUndefined();
    expect(rm2.attributes("disabled")).toBeUndefined();
  });
});
