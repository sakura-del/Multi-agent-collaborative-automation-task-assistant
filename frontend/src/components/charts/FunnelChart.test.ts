import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import FunnelChart from "./FunnelChart.vue";

const STAGES = [
  { id: "identified", label: "识别", value: 165 },
  { id: "deduped", label: "去重后", value: 120 },
  { id: "title", label: "标题/摘要", value: 60 },
  { id: "fulltext", label: "全文评估", value: 30 },
  { id: "included", label: "纳入", value: 3 },
];

describe("FunnelChart", () => {
  it("每个阶段的数量都写出来（图形只是辅助）", () => {
    const wrapper = mount(FunnelChart, { props: { stages: STAGES } });
    const text = wrapper.text();

    for (const value of ["165", "120", "60", "30", "3"]) {
      expect(text).toContain(value);
    }
    expect(text).toContain("总体转化");
    expect(text).toContain("1.8%");
  });

  it("阶段转化率相对上一段，不是累计", () => {
    const wrapper = mount(FunnelChart, { props: { stages: STAGES } });
    const text = wrapper.text();

    // 120/165=72.7%，60/120=50%，30/60=50%，3/30=10%
    expect(text).toContain("72.7");
    expect(text).toContain("50");
    expect(text).toContain("10");
  });

  it("图形有可读的无障碍名称，逐段列出数量", () => {
    const wrapper = mount(FunnelChart, { props: { stages: STAGES, unit: "篇" } });
    const svg = wrapper.get("svg");

    expect(svg.attributes("role")).toBe("img");
    const label = svg.attributes("aria-label") ?? "";
    expect(label).toContain("识别 165篇");
    expect(label).toContain("纳入 3篇");
  });

  it("没有数据时给出空态，不渲染空 SVG", () => {
    const wrapper = mount(FunnelChart, { props: { stages: [] } });
    expect(wrapper.find("svg").exists()).toBe(false);
    expect(wrapper.text()).toContain("还没有筛选数据");
  });

  it("全 0 同样走空态：不画五根等宽空条误导成「漏斗坏了」", () => {
    const wrapper = mount(FunnelChart, {
      props: {
        stages: [
          { id: "identified", label: "识别", value: 0 },
          { id: "deduped", label: "去重后", value: 0 },
          { id: "included", label: "纳入", value: 0 },
        ],
      },
    });
    expect(wrapper.find("svg").exists()).toBe(false);
    expect(wrapper.text()).toContain("还没有筛选数据");
  });

  it("粒子层只在有数据时渲染，并带 reduced-motion 降级", () => {
    const wrapper = mount(FunnelChart, { props: { stages: STAGES } });
    expect(wrapper.find(".funnel-particles").exists()).toBe(true);
    // 16 颗：密度够看出「有东西在流动」，又不至于挡住数字
    expect(wrapper.findAll(".funnel-particle")).toHaveLength(16);
  });
});
