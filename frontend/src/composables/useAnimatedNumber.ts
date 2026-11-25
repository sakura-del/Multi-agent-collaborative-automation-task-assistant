import { onScopeDispose, readonly, ref, toValue, watch, type MaybeRefOrGetter, type Ref } from "vue";
import { useReducedMotion } from "motion-v";

import { DURATION, EASE_OUT } from "@/lib/ease";

function sanitizeNumber(value: unknown): number {
  const num = typeof value === "number" ? value : Number(value);
  return Number.isFinite(num) ? num : 0;
}

/**
 * 求解标准三次贝塞尔曲线（对应 EASE_OUT [x1, y1, x2, y2]）的缓动输出。
 */
function solveCubicBezier(t: number, p1x: number, p1y: number, p2x: number, p2y: number): number {
  if (t <= 0) return 0;
  if (t >= 1) return 1;

  const sampleX = (u: number) =>
    ((1 - 3 * p2x + 3 * p1x) * u + (3 * p2x - 6 * p1x)) * u * u + 3 * p1x * u;
  const sampleDX = (u: number) =>
    3 * (1 - 3 * p2x + 3 * p1x) * u * u + 2 * (3 * p2x - 6 * p1x) * u + 3 * p1x;
  const sampleY = (u: number) =>
    ((1 - 3 * p2y + 3 * p1y) * u + (3 * p2y - 6 * p1y)) * u * u + 3 * p1y * u;

  let u = t;
  for (let i = 0; i < 5; i += 1) {
    const xErr = sampleX(u) - t;
    if (Math.abs(xErr) < 1e-5) {
      return sampleY(u);
    }
    const dx = sampleDX(u);
    if (Math.abs(dx) < 1e-6) break;
    u = Math.min(1, Math.max(0, u - xErr / dx));
  }

  let lo = 0;
  let hi = 1;
  u = t;
  for (let i = 0; i < 10; i += 1) {
    const x = sampleX(u);
    if (Math.abs(x - t) < 1e-5) break;
    if (t > x) lo = u;
    else hi = u;
    u = (lo + hi) * 0.5;
  }
  return sampleY(u);
}

/**
 * 数值平滑插值（RECHECK-B4：初值同步直达目标，仅后续变更启动 rAF + EASE_OUT 动画）。
 */
export function useAnimatedNumber(
  source: MaybeRefOrGetter<number>,
  durationMs = DURATION.number * 1000,
): Readonly<Ref<number>> {
  const reducedMotion = useReducedMotion();
  const displayed = ref<number>(sanitizeNumber(toValue(source)));
  let currentTarget = displayed.value;
  let rafId: number | null = null;

  function cancelPendingFrame() {
    if (rafId !== null) {
      if (typeof cancelAnimationFrame === "function") {
        cancelAnimationFrame(rafId);
      }
      rafId = null;
    }
  }

  watch(
    () => sanitizeNumber(toValue(source)),
    (next) => {
      currentTarget = next;
      cancelPendingFrame();

      if (
        reducedMotion.value ||
        !Number.isFinite(durationMs) ||
        durationMs <= 0 ||
        typeof requestAnimationFrame !== "function" ||
        next === displayed.value
      ) {
        displayed.value = next;
        return;
      }

      const from = displayed.value;
      const startTime = Date.now();
      const [x1, y1, x2, y2] = EASE_OUT;

      const step = () => {
        if (reducedMotion.value) {
          rafId = null;
          displayed.value = currentTarget;
          return;
        }
        const progress = Math.min(1, Math.max(0, (Date.now() - startTime) / durationMs));
        if (progress >= 1) {
          rafId = null;
          displayed.value = next;
          return;
        }
        const eased = solveCubicBezier(progress, x1, y1, x2, y2);
        displayed.value = from + (next - from) * eased;
        rafId = requestAnimationFrame(step);
      };

      rafId = requestAnimationFrame(step);
    },
    { immediate: false },
  );

  watch(reducedMotion, (isReduced) => {
    if (isReduced && rafId !== null) {
      cancelPendingFrame();
      displayed.value = currentTarget;
    }
  });

  onScopeDispose(() => {
    cancelPendingFrame();
  });

  return readonly(displayed);
}
