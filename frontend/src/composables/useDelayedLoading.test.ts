import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { effectScope, ref } from "vue";

import { useDelayedLoading } from "./useDelayedLoading";

describe("useDelayedLoading 防闪烁状态机", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("180ms 内快速完成的请求全程不闪出骨架屏", async () => {
    const loading = ref(true);
    const scope = effectScope();
    const showSkeleton = scope.run(() => useDelayedLoading(loading))!;

    expect(showSkeleton.value).toBe(false);
    await vi.advanceTimersByTimeAsync(120);
    expect(showSkeleton.value).toBe(false);

    loading.value = false;
    await vi.advanceTimersByTimeAsync(400);
    expect(showSkeleton.value).toBe(false);

    scope.stop();
  });

  it("超过 180ms 仍处于 loading 时展示骨架屏", async () => {
    const loading = ref(true);
    const scope = effectScope();
    const showSkeleton = scope.run(() => useDelayedLoading(loading))!;

    await vi.advanceTimersByTimeAsync(179);
    expect(showSkeleton.value).toBe(false);

    await vi.advanceTimersByTimeAsync(1);
    expect(showSkeleton.value).toBe(true);

    scope.stop();
  });

  it("骨架屏一旦出现至少维持 280ms 最小展示期", async () => {
    const loading = ref(true);
    const scope = effectScope();
    const showSkeleton = scope.run(() => useDelayedLoading(loading))!;

    await vi.advanceTimersByTimeAsync(180);
    expect(showSkeleton.value).toBe(true);

    // 仅展示 20ms 后请求结束（总计 200ms）
    await vi.advanceTimersByTimeAsync(20);
    loading.value = false;
    await vi.advanceTimersByTimeAsync(0);
    expect(showSkeleton.value).toBe(true);

    // 距离出现还差 260ms，尚未到 280ms 不得关闭
    await vi.advanceTimersByTimeAsync(259);
    expect(showSkeleton.value).toBe(true);

    // 满 280ms 后关闭
    await vi.advanceTimersByTimeAsync(1);
    expect(showSkeleton.value).toBe(false);

    scope.stop();
  });

  it("在最小展示等待收起期间重新置为 loading=true 时无缝续接", async () => {
    const loading = ref(true);
    const scope = effectScope();
    const showSkeleton = scope.run(() => useDelayedLoading(loading))!;

    await vi.advanceTimersByTimeAsync(180);
    expect(showSkeleton.value).toBe(true);

    await vi.advanceTimersByTimeAsync(20);
    loading.value = false;
    await vi.advanceTimersByTimeAsync(100);
    expect(showSkeleton.value).toBe(true);

    // 尚未收起前再次发起请求
    loading.value = true;
    await vi.advanceTimersByTimeAsync(300);
    expect(showSkeleton.value).toBe(true);

    scope.stop();
  });

  it("支持自定义 delayMs=0 同步展示并在 scope 销毁时清理定时器", async () => {
    const loading = ref(false);
    const scope = effectScope();
    const showSkeleton = scope.run(() =>
      useDelayedLoading(() => loading.value, { delayMs: 0, minShowMs: 100 }),
    )!;

    expect(showSkeleton.value).toBe(false);
    loading.value = true;
    await vi.advanceTimersByTimeAsync(0);
    expect(showSkeleton.value).toBe(true);

    loading.value = false;
    await vi.advanceTimersByTimeAsync(0);
    scope.stop();

    // 销毁后推进时间不应抛出异常
    await vi.advanceTimersByTimeAsync(200);
    expect(showSkeleton.value).toBe(true);
  });
});
