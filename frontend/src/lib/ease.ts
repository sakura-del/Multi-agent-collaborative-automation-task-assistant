/**
 * 全站动效常量（照搬 beUI `lib/ease.ts`）。
 *
 * ## 为什么集中在一处
 *
 * 之前两套 spring 各写在组件里：时间线 `120/24/1`、侧栏 `380/35/0.75`。
 * 数值本身没有对错，但散落导致「按下没反馈」「面板比抽屉软」这类不一致
 * 只能靠肉眼发现，改一次要翻遍组件。集中后所有交互共享同一套手感，
 * 调参也只改这里。
 *
 * 每条曲线对应一种**物理场景**，不是随便挑的数：
 * - PRESS：手指按下去要立刻有回应，所以硬（500）且质量小（0.6）
 * - SWAP：文字/图标交换，略软于按压，避免闪烁
 * - PANEL：面板入场，阻尼大（40）保证不过冲
 * - LAYOUT：共享布局滑动（指示器、胶囊），临界阻尼附近
 * - MOUSE：光标跟随，必须极快且几乎无惯性，否则有拖影
 * - GLIDE：拖拽后吸附，接近临界阻尼，滑到位不弹
 */

export interface SpringConfig {
  type: "spring";
  stiffness: number;
  damping: number;
  mass: number;
}

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

/** 标准出场曲线：快进慢出，UI 元素入场/退场默认用它。 */
export const EASE_OUT = [0.16, 1, 0.3, 1] as const;

/** 双向过渡（开关、展开/收起），两端对称。 */
export const EASE_IN_OUT = [0.77, 0, 0.175, 1] as const;

/** 抽屉/侧滑：起手快、落位稳。 */
export const EASE_DRAWER = [0.32, 0.72, 0, 1] as const;

/** 需要写进 CSS 的场景用这条（Tailwind 任意值 / 内联 style）。 */
export const EASE_OUT_CSS = "cubic-bezier(0.16, 1, 0.3, 1)";

/** 按压缩放幅度：0.985 肉眼可感但不跳动，照搬 beUI。 */
export const PRESS_SCALE = 0.985;
