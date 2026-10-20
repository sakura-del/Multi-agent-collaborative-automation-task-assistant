import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * 合并 Tailwind 类名（shadcn 标准做法）。
 *
 * - `clsx`：处理条件类名（对象/数组/假值）；
 * - `twMerge`：解决冲突——后写的同类属性覆盖前面的
 *   （如 `px-2` 与 `px-4` 只保留后者），避免样式互相打架。
 *
 * 所有 UI 组件都必须用它来拼类名（AGENTS.md §6.5.3）。
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
