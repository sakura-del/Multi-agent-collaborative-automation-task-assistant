/**
 * 全站统一动效语言与物理曲线常量（R2 单一事实源）。
 *
 * 严禁在业务组件内重复硬编码 `[0.16, 1, 0.3, 1]` 或各自独立的 duration。
 * 所有状态变化（入场、退场、折叠、共享布局、跟手拖拽、微交互）统一从此导入。
 */

export interface SpringConfig {
  type: "spring";
  stiffness: number;
  damping: number;
  mass: number;
}

/** 1. 物理弹簧曲线（仅用于跟手拖拽 / 共享布局 / 按压回弹） */
export const SPRING_PRESS: SpringConfig = {
  type: "spring",
  stiffness: 500,
  damping: 30,
  mass: 0.6,
};

export const SPRING_SWAP: SpringConfig = {
  type: "spring",
  stiffness: 460,
  damping: 30,
  mass: 0.55,
};

export const SPRING_PANEL: SpringConfig = {
  type: "spring",
  stiffness: 420,
  damping: 40,
  mass: 0.5,
};

export const SPRING_LAYOUT: SpringConfig = {
  type: "spring",
  stiffness: 360,
  damping: 32,
  mass: 0.6,
};

export const SPRING_MOUSE: SpringConfig = {
  type: "spring",
  stiffness: 200,
  damping: 15,
  mass: 0.3,
};

export const SPRING_GLIDE: SpringConfig = {
  type: "spring",
  stiffness: 700,
  damping: 50,
  mass: 0.5,
};

/** 侧栏展开/折叠：16rem ↔ 4.25rem，行程长，用偏软的 spring 避免抖动。 */
export const SPRING_MORPH: SpringConfig = {
  type: "spring",
  stiffness: 380,
  damping: 35,
  mass: 0.75,
};

/** 时间线拖拽吸附：行程更长，质量给到 1，收尾更稳。 */
export const SPRING_TIMELINE_GLIDE: SpringConfig = {
  type: "spring",
  stiffness: 120,
  damping: 24,
  mass: 1,
};

/** 2. 三次贝塞尔曲线（全站唯一源头） */
/** 入场、展开、状态浮现（快起慢落） */
export const EASE_OUT = [0.16, 1, 0.3, 1] as const;
/** 退场、关闭（干净利落收起） */
export const EASE_IN = [0.4, 0, 1, 1] as const;
/** 双向切换、内容交叉替换 */
export const EASE_IN_OUT = [0.77, 0, 0.175, 1] as const;
/** 抽屉 / 侧滑面板（Sheet 手感） */
export const EASE_DRAWER = [0.32, 0.72, 0, 1] as const;

export const EASE_OUT_CSS = "cubic-bezier(0.16, 1, 0.3, 1)";
export const EASE_IN_CSS = "cubic-bezier(0.4, 0, 1, 1)";
export const EASE_IN_OUT_CSS = "cubic-bezier(0.77, 0, 0.175, 1)";
export const EASE_DRAWER_CSS = "cubic-bezier(0.32, 0.72, 0, 1)";

/** 3. 统一时长刻度（秒；延迟与最小展示期为毫秒） */
export const DURATION = {
  /** 高频微交互：Copy→Check 图标交叉、分支角标切换、筛选 Hover */
  micro: 0.08,
  /** 极短瞬态别名（向后兼容语义，与 micro 同值） */
  instant: 0.08,
  /** 容器退场、Popover、ConfirmBar 退场、失败边框提示 */
  fast: 0.14,
  /** 标准容器：Dialog、Alert、表单折叠、路由视图入场 */
  normal: 0.2,
  /** 大面积滑入：MotionDrawer、移动端 Navigation Sheet */
  panel: 0.26,
  /** KPI / 进度 / 计数滚动插值上限 */
  number: 0.35,
  /** ms：加载防闪烁延迟（<180ms 完成的请求不闪出骨架屏） */
  skeletonDelayMs: 180,
  /** ms：骨架屏一旦出现，至少保持展示 280ms 避免瞬间闪退 */
  skeletonMinShowMs: 280,
} as const;

/** 4. 位移与缩放幅值（克制，避免夸张弹跳） */
export const OFFSET = {
  /** 图标 / 数字 / 行内标签替换位移 (px) */
  microY: 4,
  /** 顶部 Alert / Banner 入场偏移 (px) */
  alertY: -6,
  /** 居中弹窗入场偏移 (px) */
  dialogY: 8,
  /** 居中弹窗初始缩放 */
  dialogScale: 0.96,
  /** 轻量面板展开缩放 */
  collapseScale: 0.985,
  /** 卡片 / 常规按钮按下缩放 */
  pressScale: 0.985,
  /** 图标按钮（关闭、分页角标）按下缩放 */
  pressScaleSm: 0.95,
} as const;

/** 按压缩放幅度（兼容已有导入） */
export const PRESS_SCALE = OFFSET.pressScale;

/** 5. 预设 Transition 组合（供业务组件与原语直接引用） */
export const TRANSITION_MICRO = { duration: DURATION.micro, ease: EASE_OUT } as const;
export const TRANSITION_ENTER = { duration: DURATION.normal, ease: EASE_OUT } as const;
export const TRANSITION_EXIT = { duration: DURATION.fast, ease: EASE_IN } as const;
export const TRANSITION_DRAWER_ENTER = { duration: DURATION.panel, ease: EASE_DRAWER } as const;
export const TRANSITION_DRAWER_EXIT = { duration: DURATION.normal, ease: EASE_IN } as const;
export const TRANSITION_SWAP = { duration: DURATION.fast, ease: EASE_IN_OUT } as const;
