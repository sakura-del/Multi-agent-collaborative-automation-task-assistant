import { describe, expect, it } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import BeTimeline from "@/components/motion/BeTimeline.vue";
import { cardScale, cardX, SIDE_SCALE } from "@/components/motion/timeline-geometry";

const GEOMETRY = {
  cardWidth: 340,
  gap: 72,
  step: 340 * SIDE_SCALE + 72,
  near: (340 * (1 + SIDE_SCALE)) / 2 + 72,
  halfWidth: 512,
};

const EVENTS = [
  { id: "a", date: "2026-01-18", title: "事件 A" },
  { id: "b", date: "2026-04-06", title: "事件 B" },
  { id: "c", date: "2026-07-12", title: "事件 C" },
  { id: "d", date: "2026-09-30", title: "事件 D" },
  { id: "e", date: "2026-11-16", title: "事件 E" },
];

describe("timeline geometry", () => {
  it("聚焦卡在原点，相邻卡走 near，之后按 step 排开", () => {
    expect(cardX(0, GEOMETRY)).toBe(0);
    expect(cardX(1, GEOMETRY)).toBeCloseTo(GEOMETRY.near, 6);
    expect(cardX(-1, GEOMETRY)).toBeCloseTo(-GEOMETRY.near, 6);
    expect(cardX(2, GEOMETRY)).toBeCloseTo(GEOMETRY.near + GEOMETRY.step, 6);
  });

  it("侧卡缩放：中心 1，相邻起固定 0.72（更远不再缩）", () => {
    expect(cardScale(0)).toBe(1);
    expect(cardScale(1)).toBeCloseTo(SIDE_SCALE, 6);
    expect(cardScale(2)).toBeCloseTo(SIDE_SCALE, 6);
  });
});

describe("BeTimeline", () => {
  it("渲染全部卡片，首张是聚焦卡", () => {
    const wrapper = mount(BeTimeline, {
      props: { events: EVENTS, height: 400, itemWidth: 300 },
    });

    expect(wrapper.find("[data-test='be-timeline']").exists()).toBe(true);
    expect(wrapper.findAll("[data-test^='be-timeline-card-']").length).toBe(EVENTS.length);

    const first = wrapper.get("[data-test='be-timeline-card-a']");
    expect(first.attributes("data-active")).toBeDefined();
    expect(first.get("button").attributes("aria-current")).toBe("step");
    expect(first.get("button").attributes("tabindex")).toBe("0");
  });

  it("点击卡片才上报 activate（业务方据此跳转）", async () => {
    const wrapper = mount(BeTimeline, {
      props: { events: EVENTS, height: 400, itemWidth: 300 },
    });

    await wrapper.get("[data-test='be-timeline-card-c'] button").trigger("click");
    await flushPromises();

    const activated = wrapper.emitted("activate");
    expect(activated?.[activated.length - 1]).toEqual(["c"]);
    const third = wrapper.get("[data-test='be-timeline-card-c']");
    expect(third.attributes("data-active")).toBeDefined();
  });

  it("方向键只移动焦点，不触发 activate（不能浏览即跳转）", async () => {
    const wrapper = mount(BeTimeline, {
      props: { events: EVENTS, height: 400, itemWidth: 300 },
    });

    await wrapper
      .get("[data-test='be-timeline-card-a'] button")
      .trigger("keydown", { key: "ArrowRight" });
    await flushPromises();

    const emittedB = wrapper.emitted("change");
    expect(emittedB?.[emittedB.length - 1]).toEqual(["b"]);
    expect(wrapper.emitted("activate")).toBeUndefined();

    await wrapper
      .get("[data-test='be-timeline-card-b'] button")
      .trigger("keydown", { key: "ArrowLeft" });
    await flushPromises();

    const emittedA = wrapper.emitted("change");
    expect(emittedA?.[emittedA.length - 1]).toEqual(["a"]);
    expect(wrapper.emitted("activate")).toBeUndefined();
  });

  it("滚轮浏览只改变焦点，不触发 activate", async () => {
    const wrapper = mount(BeTimeline, {
      props: { events: EVENTS, height: 400, itemWidth: 300 },
    });

    await wrapper.get("[data-test='be-timeline']").trigger("wheel", { deltaX: 300 });
    await new Promise((resolve) => setTimeout(resolve, 250));
    await flushPromises();

    expect(wrapper.emitted("change")?.length).toBeGreaterThan(0);
    expect(wrapper.emitted("activate")).toBeUndefined();
  });

  it("卡片内容通过 slot 注入", () => {
    const wrapper = mount(BeTimeline, {
      props: { events: EVENTS, height: 400, itemWidth: 300 },
      slots: {
        media: `<div data-test="slot-media">媒体</div>`,
        meta: `<span data-test="slot-meta">元信息</span>`,
      },
    });

    expect(wrapper.findAll("[data-test='slot-media']").length).toBe(EVENTS.length);
    expect(wrapper.findAll("[data-test='slot-meta']").length).toBe(EVENTS.length);
  });

  it("事件列表变化时聚焦索引被钳制", async () => {
    const wrapper = mount(BeTimeline, {
      props: { events: EVENTS, height: 400, itemWidth: 300 },
    });
    await wrapper.get("[data-test='be-timeline-card-e'] button").trigger("click");
    await flushPromises();

    await wrapper.setProps({ events: EVENTS.slice(0, 2) });
    await flushPromises();

    const active = wrapper
      .findAll("[data-test^='be-timeline-card-']")
      .find((node) => node.attributes("data-active") !== undefined);
    expect(active?.attributes("data-test")).toBe("be-timeline-card-b");
  });

  it("空列表不渲染卡片也不报错", () => {
    const wrapper = mount(BeTimeline, {
      props: { events: [], height: 400, itemWidth: 300 },
    });
    expect(wrapper.findAll("[data-test^='be-timeline-card-']").length).toBe(0);
  });

  it("click 只上报一次 activate", async () => {
    const wrapper = mount(BeTimeline, {
      props: { events: EVENTS, height: 400, itemWidth: 300 },
    });

    await wrapper.get("[data-test='be-timeline-card-b'] button").trigger("click");
    await flushPromises();
    expect(wrapper.emitted("activate")?.length).toBe(1);
    expect(wrapper.emitted("activate")?.[0]).toEqual(["b"]);
  });
  it("initialIndex 直接聚焦指定卡（详情页默认最新一条）", () => {
    const wrapper = mount(BeTimeline, {
      props: { events: EVENTS, height: 400, itemWidth: 300, initialIndex: 3 },
    });

    const active = wrapper
      .findAll("[data-test^='be-timeline-card-']")
      .find((node) => node.attributes("data-active") !== undefined);
    expect(active?.attributes("data-test")).toBe("be-timeline-card-d");
  });

  it("事件数组动态追加后新卡可用（详情页 WS 场景）", async () => {
    const wrapper = mount(BeTimeline, {
      props: { events: EVENTS.slice(0, 2), height: 400, itemWidth: 300 },
    });
    expect(wrapper.findAll("[data-test^='be-timeline-card-']").length).toBe(2);

    await wrapper.setProps({ events: EVENTS });
    await flushPromises();
    expect(wrapper.findAll("[data-test^='be-timeline-card-']").length).toBe(5);

    await wrapper.get("[data-test='be-timeline-card-e'] button").trigger("click");
    await flushPromises();
    const emitted = wrapper.emitted("activate");
    expect(emitted?.[emitted.length - 1]).toEqual(["e"]);
  });
});
