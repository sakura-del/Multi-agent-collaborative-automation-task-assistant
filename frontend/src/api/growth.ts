import api from "./client";

export type FunnelEventName =
  | "landing_view"
  | "demo_view"
  | "register_view"
  | "auth_success"
  | "task_created"
  | "task_completed"
  | "demo_task_clicked";

export type FeedbackCategory = "bug" | "suggestion" | "question" | "other";

export interface FeedbackSubmitInput {
  category: FeedbackCategory;
  content: string;
  page_path?: string;
}

export interface FeedbackOut {
  id: string;
  category: string;
  content: string;
  page_path: string | null;
  status: string;
  created_at: string | null;
}

const VISITOR_ID_KEY = "vp:visitor_id";
let umamiInitialized = false;

function getOrCreateVisitorId(): string {
  try {
    if (typeof localStorage === "undefined") return "anon-ssr";
    const existing = localStorage.getItem(VISITOR_ID_KEY);
    if (existing && existing.length >= 4) return existing;
    const generated =
      typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `v_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
    localStorage.setItem(VISITOR_ID_KEY, generated);
    return generated;
  } catch {
    return "anon-fallback";
  }
}

/**
 * 预留 Umami 环境变量接入：仅在显式配置 `VITE_UMAMI_SCRIPT_URL` 与
 * `VITE_UMAMI_WEBSITE_ID` 时异步挂载脚本，未配置时零外呼。
 */
export function initUmami(): void {
  if (umamiInitialized || typeof document === "undefined") return;
  const scriptUrl = (import.meta.env.VITE_UMAMI_SCRIPT_URL as string | undefined)?.trim();
  const websiteId = (import.meta.env.VITE_UMAMI_WEBSITE_ID as string | undefined)?.trim();
  if (!scriptUrl || !websiteId) return;
  umamiInitialized = true;
  const script = document.createElement("script");
  script.defer = true;
  script.src = scriptUrl;
  script.dataset.websiteId = websiteId;
  document.head.appendChild(script);
}

/**
 * 上报轻量产品漏斗事件（站内入库 + 可选 Umami 双写；静默容错，不阻塞主流程）。
 */
export function trackEvent(
  eventName: FunnelEventName,
  meta?: Record<string, unknown>,
): void {
  if (typeof window === "undefined") return;
  const path = typeof window.location?.pathname === "string" ? window.location.pathname : undefined;

  const umami = (window as unknown as { umami?: { track?: (n: string, d?: Record<string, unknown>) => void } }).umami;
  if (umami && typeof umami.track === "function") {
    try {
      umami.track(eventName, meta);
    } catch {
      /* 忽略第三方探针异常 */
    }
  }

  if (!api || typeof api.post !== "function") return;

  try {
    void Promise.resolve(
      api.post("/analytics/events", {
        event_name: eventName,
        visitor_id: getOrCreateVisitorId(),
        path,
        meta: meta ?? null,
      }),
    ).catch(() => {
      /* 埋点失败绝不弹窗打扰用户 */
    });
  } catch {
    /* 测试环境 mock 不完整时静默跳过 */
  }
}

/**
 * 提交站内意见反馈。
 */
export async function submitFeedback(payload: FeedbackSubmitInput): Promise<FeedbackOut> {
  const response = await api.post<FeedbackOut>("/feedback", payload);
  return response.data;
}
