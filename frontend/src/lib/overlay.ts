/**
 * 弹层与抽屉共享基础设施（M1 原语内部复用）：
 * - body 滚动锁引用计数，防止多层弹层叠加时提前恢复或覆盖原有 overflow；
 * - 顶层栈管理，确保 Esc 与焦点陷阱仅响应最顶层弹层；
 * - 焦点元素查找（兼容 happy-dom：不依赖 offsetParent）。
 */

const scrollLockTokens = new Set<symbol>();
let previousBodyOverflow: string | null = null;

const overlayStack: symbol[] = [];
let deferredRestoreFocus: HTMLElement | null = null;

export function acquireScrollLock(token: symbol): void {
  if (typeof document === "undefined") return;
  if (scrollLockTokens.has(token)) return;
  if (scrollLockTokens.size === 0) {
    previousBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
  }
  scrollLockTokens.add(token);
}

export function releaseScrollLock(token: symbol): void {
  if (typeof document === "undefined") return;
  if (!scrollLockTokens.has(token)) return;
  scrollLockTokens.delete(token);
  if (scrollLockTokens.size === 0) {
    document.body.style.overflow = previousBodyOverflow ?? "";
    previousBodyOverflow = null;
  }
}

export function pushOverlay(token: symbol): void {
  if (overlayStack.length === 0) {
    deferredRestoreFocus = null;
  }
  const existingIdx = overlayStack.indexOf(token);
  if (existingIdx !== -1) {
    overlayStack.splice(existingIdx, 1);
  }
  overlayStack.push(token);
}

export function removeOverlay(token: symbol): void {
  const idx = overlayStack.indexOf(token);
  if (idx !== -1) {
    overlayStack.splice(idx, 1);
  }
}

export function isTopOverlay(token: symbol): boolean {
  return overlayStack.length > 0 && overlayStack[overlayStack.length - 1] === token;
}

export function hasActiveOverlays(): boolean {
  return overlayStack.length > 0;
}

export function deferFocusRestore(el: HTMLElement | null): void {
  if (!el || !el.isConnected) return;
  if (!deferredRestoreFocus || !deferredRestoreFocus.isConnected) {
    deferredRestoreFocus = el;
  }
}

export function consumeDeferredFocusRestore(): HTMLElement | null {
  const target = deferredRestoreFocus;
  deferredRestoreFocus = null;
  return target && target.isConnected ? target : null;
}

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
  "[contenteditable='true']",
].join(",");

/**
 * 查找容器内可见且可聚焦的交互元素（兼容 happy-dom）。
 */
export function getFocusableElements(container: HTMLElement): HTMLElement[] {
  const candidates = Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
  return candidates.filter((el) => {
    if (el.getAttribute("tabindex") === "-1") return false;
    if (el.hasAttribute("hidden") || el.closest("[hidden]")) return false;
    if (el.getAttribute("aria-hidden") === "true" || el.closest("[aria-hidden='true']")) {
      return false;
    }
    if (typeof window !== "undefined") {
      if (window.getComputedStyle(el).visibility === "hidden") {
        return false;
      }
      let current: HTMLElement | null = el;
      while (current) {
        if (window.getComputedStyle(current).display === "none") {
          return false;
        }
        if (current === container) {
          break;
        }
        current = current.parentElement;
      }
    }
    return true;
  });
}
