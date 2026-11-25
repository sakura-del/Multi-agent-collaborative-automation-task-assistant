import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";

import AnimatedCollapse from "./AnimatedCollapse.vue";

function mockReducedMotion(reduced: boolean) {
  return vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
    matches: query.includes("prefers-reduced-motion") ? reduced : false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(() => true),
  }));
}

describe("AnimatedCollapse 折叠展开原语", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockReducedMotion(false);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("按 open 状态切换 Grid 0fr/1fr 与 opacity 类", async () => {
    const wrapper = mount(AnimatedCollapse, {
      props: { open: false },
      slots: { default: "<p>高级设置</p>" },
    });

    const grid = wrapper.element as HTMLElement;
    expect(grid.className).toContain("grid-rows-[0fr]");
    expect(grid.className).toContain("opacity-0");

    await wrapper.setProps({ open: true });
    expect(grid.className).toContain("grid-rows-[1fr]");
    expect(grid.className).toContain("opacity-100");
  });

  it("keepMounted=true 在收起稳态保留 DOM 控件可读写且添加 invisible 防误聚焦", async () => {
    const wrapper = mount(AnimatedCollapse, {
      props: { open: false, keepMounted: true },
      slots: { default: '<input data-test="kept-input" />' },
    });

    const input = wrapper.find('[data-test="kept-input"]');
    expect(input.exists()).toBe(true);
    await input.setValue("2024");
    expect((input.element as HTMLInputElement).value).toBe("2024");

    const inner = wrapper.element.firstElementChild as HTMLElement;
    expect(inner.className).toContain("invisible");
    expect(inner.className).toContain("pointer-events-none");
    expect(inner.getAttribute("aria-hidden")).toBe("true");
  });

  it("keepMounted=false 在收起过渡结束后卸载内部插槽", async () => {
    const wrapper = mount(AnimatedCollapse, {
      props: { open: true, keepMounted: false },
      slots: { default: '<div data-test="ephemeral">内容</div>' },
    });
    expect(wrapper.find('[data-test="ephemeral"]').exists()).toBe(true);

    await wrapper.setProps({ open: false });
    await vi.advanceTimersByTimeAsync(220);
    await nextTick();
    expect(wrapper.find('[data-test="ephemeral"]').exists()).toBe(false);
  });

  it("R5 焦点环保护: 展开完成后切 overflow-visible，收起同步立刻切回 overflow-hidden", async () => {
    const wrapper = mount(AnimatedCollapse, {
      props: { open: false, keepMounted: true },
      slots: { default: '<input data-test="ring-input" />' },
    });

    const inner = () => wrapper.element.firstElementChild as HTMLElement;
    expect(inner().className).toContain("overflow-hidden");
    expect(inner().className).not.toContain("p-0.5");
    expect(inner().className).not.toContain("-m-0.5");

    // 开始展开：动画期间保持 overflow-hidden 且附加焦点环外扩缓冲区
    await wrapper.setProps({ open: true });
    expect(inner().className).toContain("overflow-hidden");
    expect(inner().className).not.toContain("overflow-visible");
    expect(inner().className).toContain("p-0.5");
    expect(inner().className).toContain("-m-0.5");

    // 220ms 兜底完成后切为 overflow-visible
    await vi.advanceTimersByTimeAsync(220);
    await nextTick();
    expect(inner().className).toContain("overflow-visible");
    expect(inner().className).not.toContain("overflow-hidden");
    expect(inner().className).toContain("p-0.5");

    // 收起必须同步立刻切回 overflow-hidden（收起动画期间保留缓冲区，结束后移除）
    await wrapper.setProps({ open: false });
    expect(inner().className).toContain("overflow-hidden");
    expect(inner().className).not.toContain("overflow-visible");
    expect(inner().className).toContain("p-0.5");

    await vi.advanceTimersByTimeAsync(220);
    await nextTick();
    expect(inner().className).not.toContain("p-0.5");
    expect(inner().className).not.toContain("-m-0.5");
  });

  it("prefers-reduced-motion: reduce 下展开无需等待立即处于 overflow-visible", async () => {
    mockReducedMotion(true);
    const wrapper = mount(AnimatedCollapse, {
      props: { open: false, keepMounted: true },
      slots: { default: "<p>内容</p>" },
    });

    await wrapper.setProps({ open: true });
    await nextTick();
    const inner = wrapper.element.firstElementChild as HTMLElement;
    expect(inner.className).toContain("overflow-visible");
  });
});
