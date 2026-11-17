import { describe, expect, it } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import ArcPicker from "@/components/motion/ArcPicker.vue";

const OPTIONS = [
  { value: "tasks", label: "任务" },
  { value: "knowledge", label: "知识库" },
  { value: "styles", label: "引用样式" },
  { value: "templates", label: "Word 模板" },
  { value: "plan", label: "我的套餐" },
];

describe("ArcPicker", () => {
  it("渲染 radiogroup 与全部选项，当前值被选中", () => {
    const wrapper = mount(ArcPicker, {
      props: { options: OPTIONS, modelValue: "knowledge" },
    });

    expect(wrapper.attributes("role")).toBe("radiogroup");
    expect(wrapper.findAll('[data-slot="arc-picker-option"]').length).toBe(OPTIONS.length);
    const selected = wrapper.get('[data-slot="arc-picker-option"][data-selected="true"]');
    expect(selected.attributes("data-value")).toBe("knowledge");
    expect(selected.attributes("aria-checked")).toBe("true");
    expect(wrapper.findAll('[data-slot="arc-picker-bracket"]').length).toBe(2);
  });

  it("点击选项立即上报 update:modelValue", async () => {
    const wrapper = mount(ArcPicker, {
      props: { options: OPTIONS, modelValue: "knowledge" },
    });

    await wrapper.get('[data-value="plan"]').trigger("click");
    await flushPromises();

    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["plan"]);
  });

  it("方向键逐项移动并上报，Home/End 跳首尾", async () => {
    const wrapper = mount(ArcPicker, {
      props: { options: OPTIONS, modelValue: "knowledge" },
    });

    await wrapper.trigger("keydown", { key: "ArrowDown" });
    await flushPromises();
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["styles"]);

    await wrapper.trigger("keydown", { key: "End" });
    await flushPromises();
    expect(wrapper.emitted("update:modelValue")?.[1]).toEqual(["plan"]);

    await wrapper.trigger("keydown", { key: "Home" });
    await flushPromises();
    expect(wrapper.emitted("update:modelValue")?.[2]).toEqual(["tasks"]);
  });

  it("滚轮先移动，停稳后才上报一次", async () => {
    const wrapper = mount(ArcPicker, {
      props: { options: OPTIONS, modelValue: "tasks" },
    });

    await wrapper.trigger("wheel", { deltaY: 120 });
    await flushPromises();
    // 滚动过程中不跳页
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();

    await new Promise((resolve) => setTimeout(resolve, 300));
    await flushPromises();
    const emitted = wrapper.emitted("update:modelValue");
    expect(emitted?.length).toBe(1);
    expect(emitted?.[0]?.[0]).not.toBe("tasks");
  });

  it("禁用项不可选，也不会成为选中值", async () => {
    const wrapper = mount(ArcPicker, {
      props: {
        options: [...OPTIONS.slice(0, 2), { value: "locked", label: "锁定", disabled: true }],
        modelValue: "locked",
      },
    });

    const selected = wrapper.get('[data-slot="arc-picker-option"][data-selected="true"]');
    expect(selected.attributes("data-value")).toBe("tasks");
    const locked = wrapper.get('[data-value="locked"]');
    expect(locked.attributes("disabled")).toBeDefined();
  });
});
