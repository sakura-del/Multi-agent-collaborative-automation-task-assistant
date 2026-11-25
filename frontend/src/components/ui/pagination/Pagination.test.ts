import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import Pagination from "./Pagination.vue";

function mountPager(page = 6, totalPages = 20) {
  return mount(Pagination, { props: { modelValue: page, totalPages } });
}

describe("Pagination", () => {
  it("当前页标 aria-current，其余不标", () => {
    const wrapper = mountPager();
    const current = wrapper.findAll('[aria-current="page"]');
    expect(current).toHaveLength(1);
    expect(current[0].text()).toBe("6");
  });

  it("点页码上报新页，当前页点了不重复上报", async () => {
    const wrapper = mountPager();
    await wrapper.get('[aria-label="第 7 页"]').trigger("click");

    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([7]);
    expect(wrapper.emitted("change")?.[0]).toEqual([7]);

    await wrapper.get('[aria-label="第 6 页"]').trigger("click");
    expect(wrapper.emitted("update:modelValue")).toHaveLength(1);
  });

  it("前/后省略号各跳 jump 页", async () => {
    const wrapper = mountPager(10, 30);
    await wrapper.get('[aria-label="向前 5 页"]').trigger("click");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([5]);

    await wrapper.get('[aria-label="向后 5 页"]').trigger("click");
    expect(wrapper.emitted("update:modelValue")?.[1]).toEqual([15]);
  });

  it("首尾禁用上一页/下一页", () => {
    const first = mountPager(1, 20);
    expect(first.get('[aria-label="上一页"]').attributes("disabled")).toBeDefined();
    expect(first.get('[aria-label="下一页"]').attributes("disabled")).toBeUndefined();

    const last = mountPager(20, 20);
    expect(last.get('[aria-label="下一页"]').attributes("disabled")).toBeDefined();
  });

  it("上一页/下一页按 1 步移动", async () => {
    const wrapper = mountPager(6, 20);
    await wrapper.get('[aria-label="上一页"]').trigger("click");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([5]);
    await wrapper.get('[aria-label="下一页"]').trigger("click");
    expect(wrapper.emitted("update:modelValue")?.[1]).toEqual([7]);
  });

  it("总页数为 1 时不出省略号，两侧都禁用", () => {
    const wrapper = mountPager(1, 1);
    expect(wrapper.text()).not.toContain("…");
    expect(wrapper.get('[aria-label="上一页"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('[aria-label="下一页"]').attributes("disabled")).toBeDefined();
  });

  it("导航有可读的无障碍名称", () => {
    const wrapper = mountPager();
    expect(wrapper.get("nav").attributes("aria-label")).toBe("分页");
  });

  it("透传 prevTestid 与 nextTestid，且 disabled=true 时禁用全部分页按钮并拦截跳转", async () => {
    const wrapper = mount(Pagination, {
      props: {
        modelValue: 10,
        totalPages: 30,
        prevTestid: "admin-prev",
        nextTestid: "admin-next",
        disabled: true,
      },
    });

    const prev = wrapper.get('[data-test="admin-prev"]');
    const next = wrapper.get('[data-test="admin-next"]');
    expect(prev.attributes("disabled")).toBeDefined();
    expect(next.attributes("disabled")).toBeDefined();
    expect(wrapper.get('[aria-label="第 11 页"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('[aria-label="向前 5 页"]').attributes("disabled")).toBeDefined();

    await prev.trigger("click");
    await next.trigger("click");
    await wrapper.get('[aria-label="第 11 页"]').trigger("click");
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
    expect(wrapper.emitted("change")).toBeUndefined();
  });
});
