import { cva, type VariantProps } from "class-variance-authority";

/**
 * 提示条变体（beUI Animated Alert 风格）。
 *
 * 与旧版的差异：去掉边框，改为低透明度语义底色 + 同色文字；
 * 动画与关闭按钮在 `Alert.vue` 里统一实现，所有调用方自动获得。
 */
export const alertVariants = cva(
  "relative w-full rounded-xl p-4 text-sm transition-colors duration-200",
  {
    variants: {
      variant: {
        default: "bg-muted/70 text-foreground",
        info: "bg-primary/5 text-primary",
        success: "bg-success/5 text-success",
        warning: "bg-warning/8 text-warning",
        destructive: "bg-destructive/5 text-destructive",
        muted: "bg-muted text-muted-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  }
);

export type AlertVariants = VariantProps<typeof alertVariants>;
