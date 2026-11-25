import { afterEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";
import { mount } from "@vue/test-utils";

import BeTraceWaterfall, { type TraceSpan } from "@/components/motion/BeTraceWaterfall.vue";

const SPANS: TraceSpan[] = [
  { id: "n1", name: "规划检索策略", kind: "planner", startMs: 0, durationMs: 500, status: "completed" },
  { id: "n2", name: "检索文献", kind: "search", startMs: 500, durationMs: 1200, status: "completed" },
  { id: "n3", name: "校验引文", kind: "validate", startMs: 1700, durationMs: 300, status: "failed" },
];

function mockReducedMotion(initial: boolean) {
  let matches = initial;
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
    get matches() {
      return query.includes("prefers-reduced-motion") ? matches : false;
    },
    media: query,
    onchange: null,
    addListener: vi.fn((fn) => listeners.add(fn)),
    removeListener: vi.fn((fn) => listeners.delete(fn)),
    addEventListener: vi.fn((_, fn) => listeners.add(fn as (event: MediaQueryListEvent) => void)),
    removeEventListener: vi.fn((_, fn) => listeners.delete(fn as (event: MediaQueryListEvent) => void)),
    dispatchEvent: vi.fn(() => true),
  }));
  return {
    setMatches(next: boolean) {
      matches = next;
      const evt = { matches: next, media: "(prefers-reduced-motion: reduce)" } as MediaQueryListEvent;
      for (const fn of listeners) fn(evt);
    },
  };
}

function mockCanvas2d() {
  const ctx = {
    setTransform: vi.fn(),
    clearRect: vi.fn(),
    fillText: vi.fn(),
    fillStyle: "",
    font: "",
    textBaseline: "",
  };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
    (() => ctx) as unknown as typeof HTMLCanvasElement.prototype.getContext,
  );
  return ctx;
}

const originalDpr = Object.getOwnPropertyDescriptor(window, "devicePixelRatio");

describe("BeTraceWaterfall", () => {
  afterEach(() => {
    document.documentElement.classList.remove("dark");
    if (originalDpr) {
      Object.defineProperty(window, "devicePixelRatio", originalDpr);
    } else {
      delete (window as unknown as { devicePixelRatio?: number }).devicePixelRatio;
    }
    vi.restoreAllMocks();
  });

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
    expect(playhead.attributes("style")).toContain("--playhead-ratio: 0.4");
    expect(playhead.attributes("style")).toContain("translate3d(40%, 0, 0)");
  });

  it("reducedMotion 下点击回放立即跳至末帧并同步 <input type=\"range\"> DOM 值", async () => {
    mockReducedMotion(true);
    const wrapper = mount(BeTraceWaterfall, {
      props: { runName: "task-1", spans: SPANS },
    });

    const range = wrapper.get('input[type="range"]').element as HTMLInputElement;
    // 默认未播放前 range 值为 "0"（由 onMounted syncRangeValue 设置）
    expect(range.value).toBe("0");

    await wrapper.get('[data-test="trace-replay"]').trigger("click");
    await nextTick();

    // 总时长为 2000ms，reducedMotion 下直达末帧且滑块 DOM value 必须同步为 "2000"
    expect(range.value).toBe("2000");
    expect(wrapper.get('input[type="range"]').attributes("aria-valuetext")).toBe("2.0s / 2.0s");
  });

  it("播放中切换主题时失效颜色缓存并立即以新主题色重绘 <canvas>", async () => {
    mockReducedMotion(false);
    const ctx = mockCanvas2d();
    vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    const originalGetComputedStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, "getComputedStyle").mockImplementation((el, pseudo) => {
      const base = originalGetComputedStyle(el, pseudo);
      if (el instanceof HTMLCanvasElement) {
        return {
          ...base,
          color: document.documentElement.classList.contains("dark")
            ? "rgb(180, 184, 192)"
            : "rgb(89, 91, 94)",
          fontFamily: "monospace",
        } as CSSStyleDeclaration;
      }
      return base;
    });

    const wrapper = mount(BeTraceWaterfall, {
      props: { runName: "task-1", spans: SPANS },
    });

    await wrapper.get('[data-test="trace-replay"]').trigger("click");
    await nextTick();
    expect(ctx.fillText).toHaveBeenCalled();
    expect(ctx.fillStyle).toBe("rgb(89, 91, 94)");

    const callsBeforeTheme = ctx.fillText.mock.calls.length;
    document.documentElement.classList.add("dark");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await nextTick();

    expect(ctx.fillText.mock.calls.length).toBeGreaterThan(callsBeforeTheme);
    expect(ctx.fillStyle).toBe("rgb(180, 184, 192)");
  });

  it("播放与拖拽期间冻结 <span> 具有 aria-hidden=\"true\" 且 range 具备实时 aria-valuetext", async () => {
    mockReducedMotion(false);
    mockCanvas2d();
    const rafQueue: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      rafQueue.push(cb);
      return rafQueue.length;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    vi.spyOn(performance, "now").mockReturnValue(1000);

    const wrapper = mount(BeTraceWaterfall, {
      props: { runName: "task-1", spans: SPANS },
    });

    const settledSpan = wrapper.get('[data-test="trace-waterfall"] .relative.h-4 > span');
    const range = wrapper.get('input[type="range"]');

    expect(settledSpan.attributes("aria-hidden")).toBeUndefined();
    expect(range.attributes("aria-valuetext")).toBe("0ms / 2.0s");

    // 拖拽 scrub 时 liveTimeActive 为 true
    await range.setValue("800");
    await range.trigger("input");
    await nextTick();

    expect(settledSpan.attributes("aria-hidden")).toBe("true");
    expect(range.attributes("aria-valuetext")).toBe("800ms / 2.0s");

    // change 释放后恢复非活跃态
    await range.trigger("change");
    await nextTick();
    expect(settledSpan.attributes("aria-hidden")).toBeUndefined();

    // 开始播放期间也必须 aria-hidden="true"
    await wrapper.get('[data-test="trace-replay"]').trigger("click");
    await nextTick();
    expect(settledSpan.attributes("aria-hidden")).toBe("true");

    // 推进到播放结束
    rafQueue.shift()?.(3500);
    await nextTick();
    expect(settledSpan.attributes("aria-hidden")).toBeUndefined();
    expect(range.attributes("aria-valuetext")).toBe("2.0s / 2.0s");
  });

  it("仅触发 range input 未发 change 后点击 replay，播完后 isScrubbing 自动复位且恢复可见 <span>", async () => {
    mockReducedMotion(false);
    mockCanvas2d();
    const rafQueue: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      rafQueue.push(cb);
      return rafQueue.length;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    vi.spyOn(performance, "now").mockReturnValue(1000);

    const wrapper = mount(BeTraceWaterfall, {
      props: { runName: "task-1", spans: SPANS },
    });

    const range = wrapper.get('input[type="range"]');
    const settledSpan = wrapper.get('[data-test="trace-waterfall"] .relative.h-4 > span');

    // 只派发 input，不派发 change
    await range.setValue("800");
    await range.trigger("input");
    await nextTick();
    expect(settledSpan.classes()).toContain("opacity-0");

    // 直接点击 replay，在播放启动和播完后 isScrubbing 都应已复位
    await wrapper.get('[data-test="trace-replay"]').trigger("click");
    await nextTick();
    rafQueue.shift()?.(3500);
    await nextTick();

    expect(settledSpan.classes()).toContain("opacity-100");
    expect(settledSpan.classes()).not.toContain("opacity-0");
    expect(settledSpan.attributes("aria-hidden")).toBeUndefined();
  });

  it("播放途中动态启用 prefers-reduced-motion: reduce 立即终止回放直达末帧，且画布适配高分 DPR", async () => {
    const rm = mockReducedMotion(false);
    const ctx = mockCanvas2d();
    Object.defineProperty(window, "devicePixelRatio", { configurable: true, value: 3 });

    const cancelSpy = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    const rafQueue: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      rafQueue.push(cb);
      return 42;
    });
    vi.spyOn(performance, "now").mockReturnValue(1000);

    const wrapper = mount(BeTraceWaterfall, {
      props: { runName: "task-1", spans: SPANS },
    });

    await wrapper.get('[data-test="trace-replay"]').trigger("click");
    await nextTick();

    const canvas = wrapper.get("canvas").element as HTMLCanvasElement;
    expect(canvas.width).toBe(312);
    expect(canvas.height).toBe(48);
    expect(ctx.setTransform).toHaveBeenCalledWith(3, 0, 0, 3, 0, 0);

    // 推进一帧到中间位置（500ms）
    rafQueue.shift()?.(1250);
    await nextTick();

    // 播放途中用户开启减动效
    rm.setMatches(true);
    // 再触发一次 rAF（或响应式即时响应）
    rafQueue.shift()?.(1300);
    await nextTick();

    expect(cancelSpy).toHaveBeenCalled();
    const range = wrapper.get('input[type="range"]');
    expect((range.element as HTMLInputElement).value).toBe("2000");
    expect(range.attributes("aria-valuetext")).toBe("2.0s / 2.0s");
    expect(wrapper.get('[data-test="trace-replay"]').attributes("aria-label")).toBe("回放");
  });
});
