import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import BeTraceWaterfall, { type TraceSpan } from "@/components/motion/BeTraceWaterfall.vue";

const SPANS: TraceSpan[] = [
  { id: "n1", name: "规划检索策略", kind: "planner", startMs: 0, durationMs: 500, status: "completed" },
  { id: "n2", name: "检索文献", kind: "search", startMs: 500, durationMs: 1200, status: "completed" },
  { id: "n3", name: "校验引文", kind: "validate", startMs: 1700, durationMs: 300, status: "failed" },
];

describe("BeTraceWaterfall", () => {
  it("渲染全中文表头、类型过滤标签与响应式容器（无 min-w-[576px]）", () => {
    const wrapper = mount(BeTraceWaterfall, {
      props: { runName: "task-1", spans: SPANS },
    });

    expect(wrapper.text()).toContain("节点执行轨迹");
    expect(wrapper.text()).toContain("总耗时");
    expect(wrapper.text()).toContain("节点数");
    expect(wrapper.text()).toContain("智能体");
    expect(wrapper.text()).toContain("文献检索");
    expect(wrapper.text()).toContain("质量校验");
    expect(wrapper.text()).toContain("共 3 个节点");
    expect(wrapper.text()).not.toContain("Node execution trace");
    expect(wrapper.text()).not.toContain("Duration");
    expect(wrapper.find(".min-w-\\[576px\\]").exists()).toBe(false);
    expect(wrapper.find('[aria-label="失败"]').exists()).toBe(true);
  });

  it("拖拽播放头进度条时保持播放头竖线可见", async () => {
    const wrapper = mount(BeTraceWaterfall, {
      props: { runName: "task-1", spans: SPANS },
    });

    expect(wrapper.find('[data-test="trace-playhead-line"]').exists()).toBe(false);

    const range = wrapper.get('input[type="range"]');
    await range.setValue("800");
    await range.trigger("input");

    expect(wrapper.find('[data-test="trace-playhead-line"]').exists()).toBe(true);
  });

  it("播放头竖线与容器使用响应式 --trace-col-offset 变量以对齐 sm/md 断点轨道", async () => {
    const wrapper = mount(BeTraceWaterfall, {
      props: { runName: "task-1", spans: SPANS },
    });

    const container = wrapper.find('[class*="--trace-col-offset"]');
    expect(container.exists()).toBe(true);
    expect(container.classes()).toContain("[--trace-col-offset:12.75rem]");
    expect(container.classes()).toContain("md:[--trace-col-offset:14.75rem]");

    const range = wrapper.get('input[type="range"]');
    await range.setValue("800");
    await range.trigger("input");

    const track = wrapper.get('[data-test="trace-playhead-track"]');
    expect(track.classes()).toContain("left-[var(--trace-col-offset,12.75rem)]");
    const playhead = wrapper.get('[data-test="trace-playhead-line"]');
    expect(playhead.attributes("style")).toContain("left: 40%");
  });
});
