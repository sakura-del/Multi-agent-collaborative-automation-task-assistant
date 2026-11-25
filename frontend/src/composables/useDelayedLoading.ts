import { onScopeDispose, readonly, ref, toValue, watch, type MaybeRefOrGetter, type Ref } from "vue";

import { DURATION } from "@/lib/ease";

export interface DelayedLoadingOptions {
  /** 延迟出现骨架屏的毫秒数，默认 180ms（快速请求不闪出） */
  delayMs?: number;
  /** 骨架屏一旦出现后的最小展示毫秒数，默认 280ms（防止闪出即退） */
  minShowMs?: number;
}

/**
 * 延迟骨架屏状态机：消除短请求的骨架闪烁与临界请求闪出即退。
 */
export function useDelayedLoading(
  loading: MaybeRefOrGetter<boolean>,
  options: DelayedLoadingOptions = {},
): Readonly<Ref<boolean>> {
  const delayMs = options.delayMs ?? DURATION.skeletonDelayMs;
  const minShowMs = options.minShowMs ?? DURATION.skeletonMinShowMs;

  const showSkeleton = ref(false);
  let delayTimer: ReturnType<typeof setTimeout> | null = null;
  let minShowTimer: ReturnType<typeof setTimeout> | null = null;
  let shownAt = 0;

  function clearDelayTimer() {
    if (delayTimer !== null) {
      clearTimeout(delayTimer);
      delayTimer = null;
    }
  }

  function clearMinShowTimer() {
    if (minShowTimer !== null) {
      clearTimeout(minShowTimer);
      minShowTimer = null;
    }
  }

  function revealSkeleton() {
    clearDelayTimer();
    if (!showSkeleton.value) {
      showSkeleton.value = true;
      shownAt = Date.now();
    }
  }

  watch(
    () => Boolean(toValue(loading)),
    (next) => {
      if (next) {
        clearMinShowTimer();
        if (showSkeleton.value) {
          return;
        }
        clearDelayTimer();
        if (delayMs <= 0) {
          revealSkeleton();
        } else {
          delayTimer = setTimeout(() => {
            delayTimer = null;
            revealSkeleton();
          }, delayMs);
        }
        return;
      }

      clearDelayTimer();
      if (!showSkeleton.value) {
        return;
      }

      const elapsed = Date.now() - shownAt;
      const remaining = Math.min(minShowMs, Math.max(0, minShowMs - elapsed));
      if (remaining === 0) {
        clearMinShowTimer();
        showSkeleton.value = false;
      } else if (minShowTimer === null) {
        minShowTimer = setTimeout(() => {
          minShowTimer = null;
          showSkeleton.value = false;
        }, remaining);
      }
    },
    { immediate: true },
  );

  onScopeDispose(() => {
    clearDelayTimer();
    clearMinShowTimer();
  });

  return readonly(showSkeleton);
}
