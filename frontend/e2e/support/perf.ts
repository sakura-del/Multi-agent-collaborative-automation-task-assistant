import type { CDPSession, Page } from "@playwright/test";

export interface CdpMetricSnapshot {
  LayoutCount: number;
  RecalcStyleCount: number;
  LayoutDuration: number;
  ScriptDuration: number;
}

export interface PerfActionResult {
  fps: number;
  p95FrameMs: number;
  droppedFramesPct: number;
  jankFramesCount: number;
  frameCount: number;
  cls: number;
  maxLongTaskMs: number;
  longTaskCount: number;
  deltaLayoutCount: number;
  deltaRecalcStyleCount: number;
  deltaLayoutDurationMs: number;
  deltaScriptDurationMs: number;
  cdpSupported: boolean;
}

interface BrowserPerfState {
  rafActive: boolean;
  rafId: number | null;
  lastTs: number | null;
  frameDeltas: number[];
  cls: number;
  longTasks: number[];
  observersInstalled: boolean;
}

async function readCdpMetrics(client: CDPSession): Promise<CdpMetricSnapshot> {
  const { metrics } = await client.send("Performance.getMetrics");
  const map = new Map<string, number>(metrics.map((m) => [m.name, m.value]));
  return {
    LayoutCount: map.get("LayoutCount") ?? 0,
    RecalcStyleCount: map.get("RecalcStyleCount") ?? 0,
    LayoutDuration: map.get("LayoutDuration") ?? 0,
    ScriptDuration: map.get("ScriptDuration") ?? 0,
  };
}

/**
 * 测量指定异步交互窗口内的真实浏览器性能指标：
 * - Chromium CDP `Performance.getMetrics`（`ΔLayoutCount`, `ΔRecalcStyleCount`, `ΔLayoutDuration`, `ΔScriptDuration`）
 * - `PerformanceObserver`（`layout-shift` 非输入偏移累加 `cls` + `longtask`）
 * - `requestAnimationFrame` 帧采样器（`fps`, `p95FrameMs`, `droppedFramesPct`, `jankFramesCount`）
 */
export async function measureActionPerf(
  page: Page,
  action: () => Promise<void>,
  options: { sampleRaf?: boolean } = {},
): Promise<PerfActionResult> {
  const sampleRaf = options.sampleRaf ?? true;
  let cdp: CDPSession | null = null;
  let cdpSupported = false;
  try {
    cdp = await page.context().newCDPSession(page);
    await cdp.send("Performance.enable");
    cdpSupported = true;
  } catch {
    cdp = null;
    cdpSupported = false;
  }

  // 在页面上下文安装并重置 PerformanceObserver 与可选 rAF 采样器
  await page.evaluate((enableRaf) => {
    const win = window as unknown as { __vpPerf?: BrowserPerfState };
    if (!win.__vpPerf) {
      const state: BrowserPerfState = {
        rafActive: false,
        rafId: null,
        lastTs: null,
        frameDeltas: [],
        cls: 0,
        longTasks: [],
        observersInstalled: false,
      };
      try {
        const clsObs = new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) {
            const shift = entry as PerformanceEntry & {
              hadRecentInput?: boolean;
              value?: number;
            };
            if (!shift.hadRecentInput && typeof shift.value === "number") {
              state.cls += shift.value;
            }
          }
        });
        clsObs.observe({ type: "layout-shift", buffered: false });
      } catch {
        // 个别环境不支持 layout-shift observer 时退化为 0
      }

      try {
        const ltObs = new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) {
            state.longTasks.push(entry.duration);
          }
        });
        ltObs.observe({ type: "longtask", buffered: false });
      } catch {
        // 忽略不支持 longtask 的环境
      }
      state.observersInstalled = true;
      win.__vpPerf = state;
    }

    const state = win.__vpPerf;
    if (state.rafId !== null) {
      cancelAnimationFrame(state.rafId);
      state.rafId = null;
    }
    state.cls = 0;
    state.longTasks = [];
    state.frameDeltas = [];
    state.lastTs = null;
    state.rafActive = enableRaf;

    if (enableRaf) {
      const tick = (now: number) => {
        if (!state.rafActive) return;
        if (state.lastTs !== null) {
          state.frameDeltas.push(now - state.lastTs);
        }
        state.lastTs = now;
        state.rafId = requestAnimationFrame(tick);
      };
      state.rafId = requestAnimationFrame(tick);
    }
  }, sampleRaf);

  const beforeCdp = cdp ? await readCdpMetrics(cdp) : null;

  try {
    await action();
  } finally {
    if (sampleRaf) {
      // 留出 2 帧缓冲以捕获末尾提交的 layout-shift 与长任务
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
    }
  }

  const afterCdp = cdp ? await readCdpMetrics(cdp) : null;
  if (cdp) {
    await cdp.detach().catch(() => undefined);
  }

  const browserResult = await page.evaluate(() => {
    const win = window as unknown as { __vpPerf: BrowserPerfState };
    const state = win.__vpPerf;
    state.rafActive = false;
    if (state.rafId !== null) {
      cancelAnimationFrame(state.rafId);
      state.rafId = null;
    }

    const deltas = state.frameDeltas.filter((d) => Number.isFinite(d) && d > 0);
    const frameCount = deltas.length;
    const avgDt = frameCount > 0 ? deltas.reduce((a, b) => a + b, 0) / frameCount : 16.67;
    const fps = frameCount > 0 ? Math.min(120, Math.round((1000 / avgDt) * 10) / 10) : 60;

    const sorted = [...deltas].sort((a, b) => a - b);
    const p95Idx = sorted.length > 0 ? Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95)) : 0;
    const p95FrameMs = sorted.length > 0 ? Math.round(sorted[p95Idx] * 100) / 100 : 16.67;

    const droppedCount = deltas.filter((d) => d > 33.4).length;
    const droppedFramesPct =
      frameCount > 0 ? Math.round((droppedCount / frameCount) * 10000) / 100 : 0;
    const jankFramesCount = deltas.filter((d) => d > 50).length;

    const maxLongTaskMs =
      state.longTasks.length > 0 ? Math.round(Math.max(...state.longTasks) * 100) / 100 : 0;

    return {
      fps,
      p95FrameMs,
      droppedFramesPct,
      jankFramesCount,
      frameCount,
      cls: Math.round(state.cls * 100000) / 100000,
      maxLongTaskMs,
      longTaskCount: state.longTasks.length,
    };
  });

  const deltaLayoutCount =
    beforeCdp && afterCdp ? Math.max(0, Math.round(afterCdp.LayoutCount - beforeCdp.LayoutCount)) : 0;
  const deltaRecalcStyleCount =
    beforeCdp && afterCdp
      ? Math.max(0, Math.round(afterCdp.RecalcStyleCount - beforeCdp.RecalcStyleCount))
      : 0;
  // CDP Performance.getMetrics 的 Duration 单位为秒，转换为毫秒
  const deltaLayoutDurationMs =
    beforeCdp && afterCdp
      ? Math.round((afterCdp.LayoutDuration - beforeCdp.LayoutDuration) * 1000 * 100) / 100
      : 0;
  const deltaScriptDurationMs =
    beforeCdp && afterCdp
      ? Math.round((afterCdp.ScriptDuration - beforeCdp.ScriptDuration) * 1000 * 100) / 100
      : 0;

  return {
    ...browserResult,
    deltaLayoutCount,
    deltaRecalcStyleCount,
    deltaLayoutDurationMs,
    deltaScriptDurationMs,
    cdpSupported,
  };
}
