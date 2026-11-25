import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { effectScope, nextTick, ref } from "vue";

import { useAnimatedNumber } from "./useAnimatedNumber";

function mockMatchMedia(reduced: boolean) {
  let currentReduced = reduced;
  const listeners = new Set<(event: MediaQueryListEvent) => void>();

  const spy = vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
    get matches() {
      return query.includes("prefers-reduced-motion") ? currentReduced : false;
    },
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn((type: string, cb: EventListenerOrEventListenerObject) => {
      if (type === "change" && query.includes("prefers-reduced-motion") && typeof cb === "function") {
        listeners.add(cb as (event: MediaQueryListEvent) => void);
      }
    }),
    removeEventListener: vi.fn((type: string, cb: EventListenerOrEventListenerObject) => {
      if (type === "change" && typeof cb === "function") {
        listeners.delete(cb as (event: MediaQueryListEvent) => void);
      }
    }),
    dispatchEvent: vi.fn(() => true),
  }) as MediaQueryList);

  return {
    spy,
    setReduced(next: boolean) {
      currentReduced = next;
      const event = {
        matches: next,
        media: "(prefers-reduced-motion: reduce)",
      } as MediaQueryListEvent;
      for (const listener of listeners) {
        listener(event);
      }
    },
  };
}

describe("useAnimatedNumber 数值插值", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    let rafId = 0;
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      rafId += 1;
      const id = rafId;
      setTimeout(() => cb(performance.now()), 16);
      return id;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => {
      clearTimeout(id);
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("RECHECK-B4: 初始挂载同步等于目标值，绝不从 0 起步", () => {
    mockMatchMedia(false);
    const source = ref(42);
    const scope = effectScope();
    const displayed = scope.run(() => useAnimatedNumber(source))!;

    expect(displayed.value).toBe(42);
    scope.stop();
  });

  it("后续数值变更使用 rAF 平滑插值并在 350ms 后精确直达目标值", async () => {
    mockMatchMedia(false);
    const source = ref(42);
    const scope = effectScope();
    const displayed = scope.run(() => useAnimatedNumber(source))!;

    source.value = 100;
    await nextTick();

    await vi.advanceTimersByTimeAsync(120);
    expect(displayed.value).toBeGreaterThan(42);
    expect(displayed.value).toBeLessThan(100);

    await vi.advanceTimersByTimeAsync(280);
    expect(displayed.value).toBe(100);

    scope.stop();
  });

  it("动画进行中目标值再次变化时从当前中间值无缝打断衔接", async () => {
    mockMatchMedia(false);
    const source = ref(0);
    const scope = effectScope();
    const displayed = scope.run(() => useAnimatedNumber(source, 350))!;

    source.value = 100;
    await nextTick();
    await vi.advanceTimersByTimeAsync(160);
    const midValue = displayed.value;
    expect(midValue).toBeGreaterThan(0);
    expect(midValue).toBeLessThan(100);

    source.value = 20;
    await nextTick();
    // 打断瞬间保持连贯，不回跳到 0
    expect(displayed.value).toBeGreaterThanOrEqual(20);

    await vi.advanceTimersByTimeAsync(400);
    expect(displayed.value).toBe(20);

    scope.stop();
  });

  it("开启 prefers-reduced-motion: reduce 时同步直达目标值且不调度 rAF", async () => {
    mockMatchMedia(true);
    const rafSpy = vi.fn();
    vi.stubGlobal("requestAnimationFrame", rafSpy);

    const source = ref(10);
    const scope = effectScope();
    const displayed = scope.run(() => useAnimatedNumber(source))!;

    source.value = 88;
    await nextTick();

    expect(displayed.value).toBe(88);
    expect(rafSpy).not.toHaveBeenCalled();

    scope.stop();
  });

  it("durationMs 为 NaN 或非有限正数时安全同步直达目标值，不出现 NaN", async () => {
    mockMatchMedia(false);
    const rafSpy = vi.fn();
    vi.stubGlobal("requestAnimationFrame", rafSpy);

    const source = ref(10);
    const scope = effectScope();
    const displayed = scope.run(() => useAnimatedNumber(source, Number.NaN))!;

    source.value = 100;
    await nextTick();

    expect(Number.isFinite(displayed.value)).toBe(true);
    expect(displayed.value).toBe(100);
    expect(rafSpy).not.toHaveBeenCalled();

    scope.stop();
  });

  it("动画进行中动态切换 prefers-reduced-motion: reduce 时立刻中止并直达目标值", async () => {
    const mm = mockMatchMedia(false);
    const source = ref(0);
    const scope = effectScope();
    const displayed = scope.run(() => useAnimatedNumber(source, 350))!;

    source.value = 100;
    await nextTick();
    await vi.advanceTimersByTimeAsync(100);
    expect(displayed.value).toBeGreaterThan(0);
    expect(displayed.value).toBeLessThan(100);

    // 动画中途开启系统减少动态效果
    mm.setReduced(true);
    await nextTick();

    expect(displayed.value).toBe(100);

    scope.stop();
  });
});
