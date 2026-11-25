import { toast, type ExternalToast } from "vue-sonner";

function resolveDuration(baseMs: number, options?: ExternalToast): number {
  if (typeof options?.duration === "number") return options.duration;
  if (options?.action) return Math.max(baseMs, 5000);
  return baseMs;
}

/**
 * 成功轻提示（默认 2.8s；带撤销动作延长至至少 5s）。
 */
export function toastSuccess(message: string, options?: ExternalToast) {
  return toast.success(message, {
    ...options,
    duration: resolveDuration(2800, options),
  });
}

/**
 * 错误轻提示（默认 4.5s）。
 */
export function toastError(message: string, options?: ExternalToast) {
  return toast.error(message, {
    ...options,
    duration: resolveDuration(4500, options),
  });
}

/**
 * 信息轻提示（默认 3s）。
 */
export function toastInfo(message: string, options?: ExternalToast) {
  return toast.info(message, {
    ...options,
    duration: resolveDuration(3000, options),
  });
}

/**
 * 警告轻提示（默认 3.8s）。
 */
export function toastWarning(message: string, options?: ExternalToast) {
  return toast.warning(message, {
    ...options,
    duration: resolveDuration(3800, options),
  });
}

export { toast };
