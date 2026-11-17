import type { ComputedRef, InjectionKey } from "vue";

import type { CardSize } from "./Card.vue";

/**
 * Card 把 `size` 注入给部件，部件据此决定水平内边距。
 *
 * 为什么不把 size 当 prop 逐个传：`<Card size="compact"><CardHeader>` 时
 * 调用方不该重复写一遍——漏写就会一半 20px、一半 16px，边缘错位。
 */
export const CARD_SIZE_KEY: InjectionKey<ComputedRef<CardSize>> = Symbol("card-size");

/** 各部件在水平方向自管内边距，与根容器的垂直内边距分离。 */
export const INSET_CLASS: Record<CardSize, string> = {
  default: "px-5",
  compact: "px-4",
};

/**
 * 铺到卡片里**唯一**承载状态的控件上，把它变成整卡热区。
 *
 * 用法：`<Checkbox :class="CARD_TARGET_CLASS" />`。
 * 卡内其它可交互元素必须加 `relative z-10`，否则会被这层伪元素盖住。
 */
export const CARD_TARGET_CLASS = "after:absolute after:inset-0";
