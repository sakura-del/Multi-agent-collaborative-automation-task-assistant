import { computed, ref } from "vue";

export type ThemeMode = "light" | "dark" | "system";

const STORAGE_KEY = "vp:theme";

const mode = ref<ThemeMode>(readStoredTheme());
const systemDark = ref(
  typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)").matches
    : false,
);

let listenerBound = false;

function readStoredTheme(): ThemeMode {
  if (typeof localStorage === "undefined") return "light";
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === "light" || saved === "dark" || saved === "system") {
    return saved;
  }
  return "light";
}

function applyDomTheme(isDark: boolean) {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", isDark);
}

export function initTheme() {
  if (typeof window !== "undefined" && typeof window.matchMedia === "function" && !listenerBound) {
    listenerBound = true;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    systemDark.value = mq.matches;
    mq.addEventListener("change", (event) => {
      systemDark.value = event.matches;
      if (mode.value === "system") {
        applyDomTheme(systemDark.value);
      }
    });
  }
  const resolved = mode.value === "dark" || (mode.value === "system" && systemDark.value);
  applyDomTheme(resolved);
}

/**
 * 全局主题管理（默认浅色 Notion 风，支持一键切换 Linear 深色或跟随系统）。
 */
export function useTheme() {
  const isDark = computed(() =>
    mode.value === "dark" || (mode.value === "system" && systemDark.value),
  );

  function setTheme(next: ThemeMode) {
    mode.value = next;
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(STORAGE_KEY, next);
    }
    applyDomTheme(isDark.value);
  }

  function toggleTheme() {
    setTheme(isDark.value ? "light" : "dark");
  }

  return {
    mode,
    isDark,
    setTheme,
    toggleTheme,
  };
}
