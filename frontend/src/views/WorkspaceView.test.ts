import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import WorkspaceView from "@/views/WorkspaceView.vue";

vi.mock("@/api/collaboration", () => ({
  listWorkspaces: vi.fn(),
  createWorkspace: vi.fn(),
  createInvite: vi.fn(),
  acceptInvite: vi.fn(),
}));

import {
  acceptInvite,
  createInvite,
  createWorkspace,
  listWorkspaces,
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

function mountView() {
  return mount(WorkspaceView, { global: { stubs } });
}

beforeEach(() => {
  vi.mocked(listWorkspaces).mockReset();
  vi.mocked(createWorkspace).mockReset();
  vi.mocked(createInvite).mockReset();
  vi.mocked(acceptInvite).mockReset();
});

describe("WorkspaceView", () => {
  it("展示团队列表与席位用量", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([workspace()] as never);

    const wrapper = mountView();
    await flushPromises();

    const row = wrapper.find('[data-test="workspace-row-w1"]');
    expect(row.exists()).toBe(true);
    expect(row.text()).toContain("张三课题组");
    // 席位是计费单位，必须显式展示
    expect(row.text()).toContain("席位 2/5");
  });

  it("席位满时给出「需扩容」提示", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([
      workspace({ seat_used: 5, seat_limit: 5 }),
    ] as never);

    const wrapper = mountView();
    await flushPromises();

    const row = wrapper.find('[data-test="workspace-row-w1"]');
    expect(row.text()).toContain("席位已满 5/5");
    expect(row.text()).toContain("需扩容");
  });

  it("空态给出「创建或粘贴邀请码」的引导", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([] as never);

    const wrapper = mountView();
    await flushPromises();

    const empty = wrapper.find('[data-test="workspaces-empty"]');
    expect(empty.exists()).toBe(true);
    // 空态必须给下一步（不能只写「暂无数据」）
    expect(empty.text()).toContain("邀请码");
  });

  it("加载失败显示错误而非空态", async () => {
    vi.mocked(listWorkspaces).mockRejectedValue(new Error("后端 500"));

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="workspaces-error"]').text()).toContain("后端 500");
    expect(wrapper.find('[data-test="workspaces-empty"]').exists()).toBe(false);
  });

  it("创建团队后刷新列表", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([] as never);
    vi.mocked(createWorkspace).mockResolvedValue(workspace() as never);

    const wrapper = mountView();
    await flushPromises();

    // 打开创建表单
    expect(wrapper.find('[data-test="workspaces-empty"]').exists()).toBe(true);
    const createBtn = wrapper.findAll("button").find((b) => b.text().includes("创建团队"));
    expect(createBtn).toBeTruthy();
    await createBtn!.trigger("click");

    const form = wrapper.find('[data-test="workspace-create-form"]');
    expect(form.exists()).toBe(true);

    await wrapper.get("#ws-name").setValue("张三课题组");
    await wrapper.get("#ws-slug").setValue("zhangsan-lab");

    vi.mocked(listWorkspaces).mockResolvedValue([workspace()] as never);
    await form.trigger("submit");
    await flushPromises();

    expect(createWorkspace).toHaveBeenCalledWith({
      name: "张三课题组",
      slug: "zhangsan-lab",
      seat_limit: 5,
    });
    expect(wrapper.find('[data-test="workspace-row-w1"]').exists()).toBe(true);
  });

  it("生成邀请码后展示码（只显示一次，给复制入口）", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([workspace()] as never);
    vi.mocked(createInvite).mockResolvedValue({
      invite_id: "i1",
      code: "abc123def456",
      role: "member",
      expires_at: "2026-10-28T00:00:00Z",
      workspace_id: "w1",
    } as never);

    const wrapper = mountView();
    await flushPromises();

    const inviteBtn = wrapper
      .findAll("button")
      .find((b) => b.text().includes("生成邀请码"));
    expect(inviteBtn).toBeTruthy();
    await inviteBtn!.trigger("click");
    await flushPromises();

    const box = wrapper.find('[data-test="workspace-new-invite"]');
    expect(box.exists()).toBe(true);
    expect(box.text()).toContain("abc123def456");
    expect(box.text()).toContain("只显示这一次");
  });

  it("粘贴邀请码加入", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([] as never);
    vi.mocked(acceptInvite).mockResolvedValue(workspace({ my_role: "member" }) as never);

    const wrapper = mountView();
    await flushPromises();

    await wrapper.get('[data-test="workspace-invite-input"]').setValue("  abc123  ");
    vi.mocked(listWorkspaces).mockResolvedValue([workspace({ my_role: "member" })] as never);
    // 按钮在 form 内、type=submit；jsdom 里 trigger("click") 不会冒泡成 submit，
    // 直接触发 form 的 submit 才是真实用户路径
    await wrapper.get('[data-test="workspace-invite-input"]').element.closest("form")!
      .dispatchEvent(new Event("submit"));
    await flushPromises();

    // 前后空白要 trim 掉（用户从聊天软件复制常带空格）
    expect(acceptInvite).toHaveBeenCalledWith("abc123");
  });

  it("非 owner/admin 不显示「生成邀请码」按钮", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([workspace({ my_role: "viewer" })] as never);

    const wrapper = mountView();
    await flushPromises();

    const inviteBtn = wrapper
      .findAll("button")
      .find((b) => b.text().includes("生成邀请码"));
    expect(inviteBtn).toBeUndefined();
  });

  it("手动填过 slug 后，改名字不覆盖 slug", async () => {
    /**
     * **回归测试**（真实缺陷）：自动化工具与真实用户都会「先填名字、再填 slug」。
     * 首版派生挂在 `@blur` 上，Playwright `fill()` 的时序让派生值插进输入中途，
     * 实测 slug 变成 `e2e-1792584014145-mobile-chromee2e-team-...`（两段拼接）。
     *
     * 修法：派生改挂 `@input`（与用户输入同步发生，没有"派生值晚到"的窗口）
     * + `slugTouched` 标记（用户动过就不再自动改）。
     * 回退 `slugTouched` 后本用例变红。
     */
    vi.mocked(listWorkspaces).mockResolvedValue([] as never);

    const wrapper = mountView();
    await flushPromises();

    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("创建团队"))!
      .trigger("click");

    // 用户手动填 slug（setValue + 显式 input，模拟真实输入触发 slugTouched）
    await wrapper.get("#ws-slug").setValue("my-custom-slug");
    await wrapper.get("#ws-slug").trigger("input");
    // 再改名字（触发 input → syncSlug）
    await wrapper.get("#ws-name").setValue("张三课题组");
    await wrapper.get("#ws-name").trigger("input");

    expect((wrapper.get("#ws-slug").element as HTMLInputElement).value).toBe("my-custom-slug");
  });

  it("没手动填 slug 时，名字自动派生 slug", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([] as never);

    const wrapper = mountView();
    await flushPromises();

    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("创建团队"))!
      .trigger("click");

    await wrapper.get("#ws-name").setValue("Zhang Lab 2026");
    // `setValue` 在 shadcn Input（透传 v-model）上不保证触发组件根的 @input，
    // 显式补一次，模拟真实用户逐字输入
    await wrapper.get("#ws-name").trigger("input");

    expect((wrapper.get("#ws-slug").element as HTMLInputElement).value).toBe("zhang-lab-2026");
  });
});
