import { cva, type VariantProps } from "class-variance-authority";

/**
 * 徽标变体（shadcn-vue / Nova 预设）。
 *
 * 用于状态标识（运行中/完成/失败、预置/自定义等）。
 * 一律用 `variant` 表达语义，**不要**再写一次性颜色类。
 */
export const badgeVariants = cva(
  [
    "inline-flex items-center rounded-md border px-1.5 py-0.5",
    "text-xs font-medium whitespace-nowrap",
    "transition-colors",
  ],
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        outline: "border-border text-foreground",
        success: "border-transparent bg-success/12 text-success",
        warning: "border-transparent bg-warning/15 text-warning",
        destructive: "border-transparent bg-destructive/12 text-destructive",
        muted: "border-transparent bg-muted text-muted-foreground",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
);

export type BadgeVariants = VariantProps<typeof badgeVariants>;
