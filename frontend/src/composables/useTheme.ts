import { computed, ref } from "vue";

export type ThemeMode = "light" | "dark" | "system";

const STORAGE_KEY = "vp:theme";

const mode = ref<ThemeMode>(readStoredTheme());
const systemDark = ref(
  typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)").matches
    : false,
);

let boundMq: MediaQueryList | null = null;
let boundListener: ((event: MediaQueryListEvent) => void) | null = null;

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

/**
 * 确保始终绑定到当前有效的 matchMedia('(prefers-color-scheme: dark)') 对象。
 */
function ensureMediaListener() {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  if (mq === boundMq) return;

  if (boundMq && boundListener) {
    if (typeof boundMq.removeEventListener === "function") {
      boundMq.removeEventListener("change", boundListener);
    } else if (typeof boundMq.removeListener === "function") {
      boundMq.removeListener(boundListener);
    }
  }

  boundMq = mq;
  systemDark.value = mq.matches;
  boundListener = (event: MediaQueryListEvent) => {
    systemDark.value = event.matches;
    if (mode.value === "system") {
      applyDomTheme(systemDark.value);
    }
  };

  if (typeof mq.addEventListener === "function") {
    mq.addEventListener("change", boundListener);
  } else if (typeof mq.addListener === "function") {
    mq.addListener(boundListener);
  }
}

/**
 * 初始化主题状态、绑定系统偏好监听器并同步 DOM `.dark` 类。
 */
export function initTheme() {
  ensureMediaListener();
  mode.value = readStoredTheme();
  const resolved = mode.value === "dark" || (mode.value === "system" && systemDark.value);
  applyDomTheme(resolved);
}

/**
 * 全局主题管理（默认浅色 Notion 风，支持浅色、深色与跟随系统三态）。
 */
export function useTheme() {
  ensureMediaListener();

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
    themeMode: mode,
    isDark,
    setTheme,
    toggleTheme,
  };
}
