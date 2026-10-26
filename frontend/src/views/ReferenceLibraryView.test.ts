import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import {
  clearLibrary,
  deleteLibraryItem,
  getLibrarySummary,
  listLibraryItems,
  updateLibraryItem,
} from "@/api/reference-library";
import type { LibraryItemOut } from "@/api/types";
import ReferenceLibraryView from "@/views/ReferenceLibraryView.vue";

vi.mock("@/api/reference-library", () => ({
  listLibraryItems: vi.fn(),
  getLibrarySummary: vi.fn(),
  deleteLibraryItem: vi.fn(),
  clearLibrary: vi.fn(),
  updateLibraryItem: vi.fn(),
}));

// 引用库页（2026-10-26 引用库计划 §3.6）。
//
// 这一页存在的理由：导入页此前解析完什么也做不了。现在链路是
// 导入 → 保存 → 本页可查可删 → 创建任务时选「我的引用库」。

function libraryItem(overrides: Partial<LibraryItemOut> = {}): LibraryItemOut {
  return {
    item_id: "item-1",
    ref_id: "doi:10.1234/a",
    title: "检索增强生成评估方法综述",
    authors: ["牛耕田", "李四"],
    year: 2024,
    container_title: "计算机学报",
    doi: "10.1234/a",
    type: "article-journal",
    ...overrides,
  };
}

async function mountView() {
  const wrapper = mount(ReferenceLibraryView);
  await flushPromises();
  return wrapper;
}

describe("ReferenceLibraryView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listLibraryItems).mockResolvedValue({
      items: [libraryItem(), libraryItem({ item_id: "item-2", ref_id: "doi:10.1/b", title: "第二篇" })],
      total: 2,
    });
    vi.mocked(getLibrarySummary).mockResolvedValue({ total: 2, usable_as_source: 2 });
  });

  it("列出条目并展示关键字段", async () => {
    const wrapper = await mountView();

    const row = wrapper.find('[data-test="library-row-item-1"]');
    expect(row.exists()).toBe(true);
    expect(row.text()).toContain("检索增强生成评估方法综述");
    expect(row.text()).toContain("牛耕田");
    expect(row.text()).toContain("2024");
    expect(row.text()).toContain("10.1234/a");
  });

  it("展示摘要（总条数 + 可作文献源条数）", async () => {
    const wrapper = await mountView();

    const summary = wrapper.find('[data-test="library-summary"]');
    expect(summary.exists()).toBe(true);
    expect(summary.text()).toContain("2");
  });

  it("空态给出明确引导（指向导入页）", async () => {
    vi.mocked(listLibraryItems).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(getLibrarySummary).mockResolvedValue({ total: 0, usable_as_source: 0 });

    const wrapper = await mountView();

    const empty = wrapper.find('[data-test="library-empty"]');
    expect(empty.exists()).toBe(true);
    expect(empty.text()).toMatch(/导入/);
  });

  it("空库时摘要仍然显示（0 条是有效信息，不能藏起来）", async () => {
    // 真实浏览器验证抓到的缺陷：原先 `v-if="summary.total > 0"` 把摘要卡
    // 和清空按钮一起藏了，空库时页面只剩一句「还是空的」，看不到计数。
    vi.mocked(listLibraryItems).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(getLibrarySummary).mockResolvedValue({ total: 0, usable_as_source: 0 });

    const wrapper = await mountView();

    const summary = wrapper.find('[data-test="library-summary"]');
    expect(summary.exists()).toBe(true);
    expect(summary.text()).toContain("0");
  });

  it("空库时不显示清空按钮（没什么可清）", async () => {
    vi.mocked(listLibraryItems).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(getLibrarySummary).mockResolvedValue({ total: 0, usable_as_source: 0 });

    const wrapper = await mountView();

    expect(wrapper.find('[data-test="library-clear"]').exists()).toBe(false);
  });

  it("页面说明要讲清「能怎么用」（创建任务时选它）", async () => {
    const wrapper = await mountView();

    const hint = wrapper.find('[data-test="library-hint"]');
    expect(hint.exists()).toBe(true);
    expect(hint.text()).toMatch(/创建任务/);
  });

  it("删除需要二次确认", async () => {
    vi.mocked(deleteLibraryItem).mockResolvedValue(undefined);

    const wrapper = await mountView();
    await wrapper.find('[data-test="library-delete-item-1"]').trigger("click");
    await flushPromises();

    // 第一次点击不能直接删
    expect(deleteLibraryItem).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="library-delete-confirm-item-1"]').exists()).toBe(true);
  });

  it("确认后才真正删除", async () => {
    vi.mocked(deleteLibraryItem).mockResolvedValue(undefined);

    const wrapper = await mountView();
    await wrapper.find('[data-test="library-delete-item-1"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-test="library-delete-confirm-item-1"]').trigger("click");
    await flushPromises();

    expect(deleteLibraryItem).toHaveBeenCalledWith("item-1");
  });

  it("可以取消删除", async () => {
    vi.mocked(deleteLibraryItem).mockResolvedValue(undefined);

    const wrapper = await mountView();
    await wrapper.find('[data-test="library-delete-item-1"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-test="library-delete-cancel-item-1"]').trigger("click");
    await flushPromises();

    expect(deleteLibraryItem).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="library-delete-confirm-item-1"]').exists()).toBe(false);
  });

  it("清空也要二次确认", async () => {
    vi.mocked(clearLibrary).mockResolvedValue({ deleted: 2 });

    const wrapper = await mountView();
    await wrapper.find('[data-test="library-clear"]').trigger("click");
    await flushPromises();

    expect(clearLibrary).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="library-clear-confirm"]').exists()).toBe(true);
  });

  it("确认清空后调用接口", async () => {
    vi.mocked(clearLibrary).mockResolvedValue({ deleted: 2 });

    const wrapper = await mountView();
    await wrapper.find('[data-test="library-clear"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-test="library-clear-confirm"]').trigger("click");
    await flushPromises();

    expect(clearLibrary).toHaveBeenCalledTimes(1);
  });

  it("列表加载失败时展示错误横幅", async () => {
    vi.mocked(listLibraryItems).mockRejectedValue(new Error("网络错误"));

    const wrapper = await mountView();

    expect(wrapper.find('[data-test="library-load-error"]').text()).toContain("网络错误");
  });

  it("摘要失败不连坐列表（局部失败不连坐）", async () => {
    // AGENTS §5 #23：Promise.all 会让一个接口失败吞掉另一个的结果
    vi.mocked(getLibrarySummary).mockRejectedValue(new Error("summary 500"));

    const wrapper = await mountView();

    expect(wrapper.find('[data-test="library-row-item-1"]').exists()).toBe(true);
  });

  it("加载中不显示空态（避免把加载误报成没有条目）", async () => {
    let resolveList: (value: unknown) => void = () => {};
    vi.mocked(listLibraryItems).mockReturnValue(
      new Promise((resolve) => {
        resolveList = resolve;
      }) as never
    );

    const wrapper = mount(ReferenceLibraryView);
    await flushPromises();

    expect(wrapper.find('[data-test="library-empty"]').exists()).toBe(false);

    resolveList({ items: [libraryItem()], total: 1 });
    await flushPromises();
    expect(wrapper.find('[data-test="library-row-item-1"]').exists()).toBe(true);
  });
});

/**
 * 条目编辑（2026-10-26 引用库遗留计划 §3.5）。
 *
 * 真实需求是「改个错字」和「补一个漏掉的年份」，不是完整书目编辑器。
 * 关键约束：**只发用户真正改了的字段** —— 发全量会把没动的字段覆盖成 null。
 */
describe("ReferenceLibraryView 编辑条目", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listLibraryItems).mockResolvedValue({
      items: [libraryItem()],
      total: 1,
    });
    vi.mocked(getLibrarySummary).mockResolvedValue({ total: 1, usable_as_source: 1 });
    vi.mocked(updateLibraryItem).mockResolvedValue(libraryItem());
  });

  it("点编辑展开表单并预填当前值", async () => {
    const wrapper = await mountView();

    await wrapper.find('[data-test="library-edit-item-1"]').trigger("click");
    await flushPromises();

    const form = wrapper.find('[data-test="library-edit-form-item-1"]');
    expect(form.exists()).toBe(true);
    expect(
      (wrapper.find('[data-test="library-edit-title-item-1"]').element as HTMLInputElement).value
    ).toBe("检索增强生成评估方法综述");
    expect(
      (wrapper.find('[data-test="library-edit-year-item-1"]').element as HTMLInputElement).value
    ).toBe("2024");
  });

  it("只发改过的字段（不覆盖未修改的字段）", async () => {
    const wrapper = await mountView();

    await wrapper.find('[data-test="library-edit-item-1"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-test="library-edit-title-item-1"]').setValue("改过的题名");
    await wrapper.find('[data-test="library-edit-save-item-1"]').trigger("click");
    await flushPromises();

    expect(updateLibraryItem).toHaveBeenCalledWith("item-1", { title: "改过的题名" });
  });

  it("没改任何字段时不发请求", async () => {
    const wrapper = await mountView();

    await wrapper.find('[data-test="library-edit-item-1"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-test="library-edit-save-item-1"]').trigger("click");
    await flushPromises();

    expect(updateLibraryItem).not.toHaveBeenCalled();
  });

  it("改年份时发 issued_year（不是 year）", async () => {
    const wrapper = await mountView();

    await wrapper.find('[data-test="library-edit-item-1"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-test="library-edit-year-item-1"]').setValue("2023");
    await wrapper.find('[data-test="library-edit-save-item-1"]').trigger("click");
    await flushPromises();

    expect(updateLibraryItem).toHaveBeenCalledWith("item-1", { issued_year: 2023 });
  });

  it("年份留空时发 null（后端会拒绝并给出原因）", async () => {
    const wrapper = await mountView();

    await wrapper.find('[data-test="library-edit-item-1"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-test="library-edit-year-item-1"]').setValue("");
    await wrapper.find('[data-test="library-edit-save-item-1"]').trigger("click");
    await flushPromises();

    expect(updateLibraryItem).toHaveBeenCalledWith("item-1", { issued_year: null });
  });

  it("年份非法时本地拦截，不发请求", async () => {
    const wrapper = await mountView();

    await wrapper.find('[data-test="library-edit-item-1"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-test="library-edit-year-item-1"]').setValue("abc");
    await wrapper.find('[data-test="library-edit-save-item-1"]').trigger("click");
    await flushPromises();

    expect(updateLibraryItem).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="library-action-error"]').text()).toMatch(/年份/);
  });

  it("后端拒绝时如实展示原因（准入检查在服务端）", async () => {
    vi.mocked(updateLibraryItem).mockRejectedValue(
      new Error("修改后不符合入库条件：缺出版年份：无法确定发表时间，不能作为文献源使用")
    );

    const wrapper = await mountView();

    await wrapper.find('[data-test="library-edit-item-1"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-test="library-edit-year-item-1"]').setValue("");
    await wrapper.find('[data-test="library-edit-save-item-1"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-test="library-action-error"]').text()).toMatch(/缺出版年份/);
  });

  it("可以取消编辑", async () => {
    const wrapper = await mountView();

    await wrapper.find('[data-test="library-edit-item-1"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-test="library-edit-cancel-item-1"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-test="library-edit-form-item-1"]').exists()).toBe(false);
    expect(updateLibraryItem).not.toHaveBeenCalled();
  });
});
