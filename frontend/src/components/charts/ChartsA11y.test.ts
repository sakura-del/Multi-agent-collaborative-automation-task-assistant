import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import BarTrendChart from "./BarTrendChart.vue";
import DonutChart from "./DonutChart.vue";
import SegmentedBar from "./SegmentedBar.vue";

describe("Charts M5 无障碍与 GPU 过渡规范", () => {
  it("BarTrendChart 支持键盘 tabindex=0 / Enter / Space 与触屏点击切换读数，且无 transition-all", async () => {
    const wrapper = mount(BarTrendChart, {
      props: {
        items: [
          { key: "t1", label: "任务一", total: 1200, meta: "10 秒", testid: "bar-1" },
          { key: "t2", label: "任务二", total: 2400, meta: "20 秒", testid: "bar-2" },
        ],
        clickable: false,
      },
    });

    const bar1 = wrapper.get('[data-test="bar-1"]');
    expect(bar1.attributes("tabindex")).toBe("0");
    expect(bar1.attributes("aria-label")).toContain("任务一");

    // 非 clickable 模式下点击切换固定读数
    await bar1.trigger("click");
    expect(wrapper.text()).toContain("任务一 · 1.2k");
    await bar1.trigger("click");
    expect(wrapper.text()).not.toContain("任务一 · 1.2k");

    // 键盘 Enter 切换读数
    await bar1.trigger("keydown.enter");
    expect(wrapper.text()).toContain("任务一 · 1.2k");

    // focus / blur 联动读数
    const bar2 = wrapper.get('[data-test="bar-2"]');
    await bar2.trigger("focus");
    expect(wrapper.text()).toContain("任务二 · 2.4k");
    await bar2.trigger("blur");
    expect(wrapper.text()).not.toContain("任务二 · 2.4k");

    expect(wrapper.html()).not.toContain("transition-all");
  });

  it("DonutChart 消除 transition-all 与 stroke-width 动画，支持圆弧和图例键盘聚焦与点击", async () => {
    const wrapper = mount(DonutChart, {
      props: {
        segments: [
          { id: "ok", label: "已完成", value: 3, color: "var(--success)", hint: "3 个" },
          { id: "err", label: "失败", value: 1, color: "var(--destructive)", hint: "1 个" },
        ],
        centerValue: "75.0%",
        centerLabel: "完结成功率",
        strokeWidth: 14,
      },
    });

    expect(wrapper.html()).not.toContain("transition-all");
    const circles = wrapper.findAll("svg circle[tabindex='0']");
    expect(circles).toHaveLength(2);
    expect(circles[0].attributes("role")).toBe("button");
    expect(circles[0].attributes("stroke-width")).toBe("14");

    await circles[0].trigger("focus");
    // 选中后中心切换到该弧段百分比且 stroke-width 不变化（无 stroke-width 动画）
    expect(wrapper.text()).toContain("75.0%");
    expect(wrapper.text()).toContain("已完成");
    expect(circles[0].attributes("stroke-width")).toBe("14");

    await circles[0].trigger("blur");
    expect(wrapper.text()).toContain("完结成功率");

    // 图例也支持键盘与点击
    const legendItems = wrapper.findAll("ul li[tabindex='0']");
    expect(legendItems).toHaveLength(2);
    await legendItems[1].trigger("click");
    expect(wrapper.text()).toContain("25.0%");
  });

  it("SegmentedBar 消除 transition-all 并带 motion-reduce:transition-none", () => {
    const wrapper = mount(SegmentedBar, {
      props: {
        segments: [
          { id: "a", label: "A", value: 2, color: "var(--primary)" },
          { id: "b", label: "B", value: 2, color: "var(--success)" },
        ],
      },
    });

    const html = wrapper.html();
    expect(html).not.toContain("transition-all");
    expect(html).toContain("transition-opacity");
    expect(html).toContain("motion-reduce:transition-none");
  });
});
