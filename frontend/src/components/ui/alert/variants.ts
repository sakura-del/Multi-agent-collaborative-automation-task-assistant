import { cva, type VariantProps } from "class-variance-authority";

/**
 * 提示条变体（shadcn-vue 风格 `Alert`）。
 *
 * 替代各页面自研的 `.banner` / `ErrorBanner`——统一错误与成功提示的视觉。
 */
export const alertVariants = cva(
  [
    "relative w-full rounded-lg border px-3 py-2.5 text-sm",
    "[&>svg]:size-4 [&>svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        default: "bg-background text-foreground border-border",
        destructive: "border-destructive/40 bg-destructive/8 text-destructive",
        success: "border-success/40 bg-success/8 text-success",
        warning: "border-warning/40 bg-warning/10 text-warning",
        muted: "border-border bg-muted text-muted-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  }
);

export type AlertVariants = VariantProps<typeof alertVariants>;
