import { cva, type VariantProps } from "class-variance-authority";

/**
 * 按钮变体（shadcn-vue / Nova 预设）。
 *
 * 用 CVA 把「外观」与「HTML 结构」解耦：调用方只传 variant/size，
 * 不再手写颜色/间距/圆角——这样全站按钮风格必然一致（AGENTS.md §6.5.3）。
 *
 * 尺寸偏紧凑（h-8/h-9）：工作台信息密度高，不适合大按钮。
 */
export const buttonVariants = cva(
  // 基础类：布局、焦点、禁用态、过渡
  [
    "inline-flex items-center justify-center gap-1.5 whitespace-nowrap",
    "rounded-md text-sm font-medium",
    "transition-colors duration-150",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
    "disabled:pointer-events-none disabled:opacity-50",
    "[&_svg]:pointer-events-none [&_svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        secondary: "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        outline:
          "border border-border bg-background hover:bg-muted hover:text-foreground",
        ghost: "hover:bg-muted hover:text-foreground",
        destructive:
          "bg-destructive text-destructive-foreground hover:bg-destructive/90",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        sm: "h-8 px-3 text-xs",
        default: "h-9 px-4",
        lg: "h-10 px-6",
        icon: "size-9",
        "icon-sm": "size-8",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

export type ButtonVariants = VariantProps<typeof buttonVariants>;
