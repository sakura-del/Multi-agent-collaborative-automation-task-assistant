import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { initTheme, useTheme } from "./useTheme";

interface MockMediaQuery {
  matches: boolean;
  media: string;
  trigger: (matches: boolean) => void;
}

function installMatchMediaMock(initialDark: boolean): MockMediaQuery {
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  const state: MockMediaQuery = {
    matches: initialDark,
    media: "(prefers-color-scheme: dark)",
    trigger(next: boolean) {
      state.matches = next;
      const event = { matches: next, media: state.media } as MediaQueryListEvent;
      for (const listener of listeners) {
        listener(event);
      }
    },
  };

  vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
    get matches() {
      return query.includes("prefers-color-scheme: dark") ? state.matches : false;
    },
    media: query,
    onchange: null,
    addListener: (cb: (event: MediaQueryListEvent) => void) => listeners.add(cb),
    removeListener: (cb: (event: MediaQueryListEvent) => void) => listeners.delete(cb),
    addEventListener: (_: string, cb: EventListenerOrEventListenerObject) => {
      if (typeof cb === "function") {
        listeners.add(cb as (event: MediaQueryListEvent) => void);
      }
    },
    removeEventListener: (_: string, cb: EventListenerOrEventListenerObject) => {
      if (typeof cb === "function") {
        listeners.delete(cb as (event: MediaQueryListEvent) => void);
      }
    },
    dispatchEvent: () => true,
  }));

  return state;
}

describe("composables/useTheme 三态主题与两态快捷切换", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark");
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    document.documentElement.classList.remove("dark");
    initTheme();
  });

  it("默认初始化为 light 且同时暴露 mode 与 themeMode 别名", () => {
    installMatchMediaMock(false);
    initTheme();
    const { mode, themeMode, isDark } = useTheme();

    expect(mode.value).toBe("light");
    expect(themeMode.value).toBe("light");
    expect(isDark.value).toBe(false);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("setTheme('dark') 写入存储并挂载 .dark 类", () => {
    installMatchMediaMock(false);
    initTheme();
    const { themeMode, isDark, setTheme } = useTheme();

    setTheme("dark");
    expect(themeMode.value).toBe("dark");
    expect(isDark.value).toBe(true);
    expect(localStorage.getItem("vp:theme")).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("setTheme('system') 自动响应系统配色变化事件（无需预先手动调 initTheme）", () => {
    const mq = installMatchMediaMock(false);
    const { setTheme, isDark, themeMode } = useTheme();

    setTheme("system");
    expect(themeMode.value).toBe("system");
    expect(isDark.value).toBe(false);
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    mq.trigger(true);
    expect(isDark.value).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    mq.trigger(false);
    expect(isDark.value).toBe(false);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("toggleTheme() 在 system 深色下点一次切为 light、再点切为 dark，守卫两态切换", () => {
    installMatchMediaMock(true);
    initTheme();
    const { setTheme, toggleTheme, mode, isDark } = useTheme();

    setTheme("system");
    expect(isDark.value).toBe(true);

    toggleTheme();
    expect(mode.value).toBe("light");
    expect(isDark.value).toBe(false);
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    toggleTheme();
    expect(mode.value).toBe("dark");
    expect(isDark.value).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});
