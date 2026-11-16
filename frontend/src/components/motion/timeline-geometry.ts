/**
 * beUI Timeline 的几何与动效常量（与源码一一对应）。
 *
 * 独立成模块的原因：`cardX` / `cardScale` 是纯函数，单测可以直接锁行为，
 * 不用为验证几何而挂载整个组件。
 */

export const GLIDE_SPRING = { stiffness: 120, damping: 24, mass: 1 };
export const FLICK_MOMENTUM = 0.3;
export const MAX_FLICK_EVENTS = 4;
export const DRAG_THRESHOLD = 4;
export const RUBBER_BAND = 0.3;
export const SIDE_SCALE = 0.72;
export const SIDE_DIM = 0.45;
export const YEAR_PARALLAX = 0.5;
export const YEAR_INSET = 0.33;
export const CARD_CENTER = 0.5;
export const GAP_TICKS = 5;
export const GAP_TICK_SPAN = 0.7;
export const TICK_SHORT = 0.13;
export const TICK_TALL = 0.2;
export const CABLE_REACH = 0.8;
export const CABLE_HOLD = 1;
export const CABLE_FADE = 1.6;

export interface TimelineGeometry {
  cardWidth: number;
  gap: number;
  step: number;
  near: number;
  halfWidth: number;
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/** 卡片的横移：第一张相邻卡走 near，之后按 step 等距排开。 */
export function cardX(offset: number, geometry: TimelineGeometry): number {
  const a = Math.abs(offset);
  return (
    Math.sign(offset) * (a <= 1 ? a * geometry.near : geometry.near + (a - 1) * geometry.step)
  );
}

export function cardScale(offset: number): number {
  return 1 - (1 - SIDE_SCALE) * Math.min(Math.abs(offset), 1);
}
