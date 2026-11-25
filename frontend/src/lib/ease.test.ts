import { describe, expect, it } from "vitest";

import * as ease from "./ease";

describe("lib/ease 动效语言单一事实源", () => {
  it("保留全部既有物理弹簧与按压缩放导出", () => {
    expect(ease.SPRING_PRESS).toEqual({
      type: "spring",
      stiffness: 500,
      damping: 30,
      mass: 0.6,
    });
    expect(ease.SPRING_SWAP).toEqual({
      type: "spring",
      stiffness: 460,
      damping: 30,
      mass: 0.55,
    });
    expect(ease.SPRING_PANEL).toEqual({
      type: "spring",
      stiffness: 420,
      damping: 40,
      mass: 0.5,
    });
    expect(ease.SPRING_LAYOUT).toEqual({
      type: "spring",
      stiffness: 360,
      damping: 32,
      mass: 0.6,
    });
    expect(ease.SPRING_MOUSE).toEqual({
      type: "spring",
      stiffness: 200,
      damping: 15,
      mass: 0.3,
    });
    expect(ease.SPRING_GLIDE).toEqual({
      type: "spring",
      stiffness: 700,
      damping: 50,
      mass: 0.5,
    });
    expect(ease.SPRING_MORPH).toEqual({
      type: "spring",
      stiffness: 380,
      damping: 35,
      mass: 0.75,
    });
    expect(ease.SPRING_TIMELINE_GLIDE).toEqual({
      type: "spring",
      stiffness: 120,
      damping: 24,
      mass: 1,
    });
    expect(ease.PRESS_SCALE).toBe(0.985);
  });

  it("导出完整的三次贝塞尔曲线与 CSS 字符串常量", () => {
    expect(ease.EASE_OUT).toEqual([0.16, 1, 0.3, 1]);
    expect(ease.EASE_IN).toEqual([0.4, 0, 1, 1]);
    expect(ease.EASE_IN_OUT).toEqual([0.77, 0, 0.175, 1]);
    expect(ease.EASE_DRAWER).toEqual([0.32, 0.72, 0, 1]);

    expect(ease.EASE_OUT_CSS).toBe("cubic-bezier(0.16, 1, 0.3, 1)");
    expect(ease.EASE_IN_CSS).toBe("cubic-bezier(0.4, 0, 1, 1)");
    expect(ease.EASE_IN_OUT_CSS).toBe("cubic-bezier(0.77, 0, 0.175, 1)");
    expect(ease.EASE_DRAWER_CSS).toBe("cubic-bezier(0.32, 0.72, 0, 1)");
  });

  it("导出定版 DURATION、OFFSET 与预设 TRANSITION 组合", () => {
    expect(ease.DURATION).toEqual({
      micro: 0.08,
      instant: 0.08,
      fast: 0.14,
      normal: 0.2,
      panel: 0.26,
      number: 0.35,
      skeletonDelayMs: 180,
      skeletonMinShowMs: 280,
    });

    expect(ease.OFFSET).toEqual({
      microY: 4,
      alertY: -6,
      dialogY: 8,
      dialogScale: 0.96,
      collapseScale: 0.985,
      pressScale: 0.985,
      pressScaleSm: 0.95,
    });

    expect(ease.TRANSITION_MICRO).toEqual({
      duration: ease.DURATION.micro,
      ease: ease.EASE_OUT,
    });
    expect(ease.TRANSITION_ENTER).toEqual({
      duration: ease.DURATION.normal,
      ease: ease.EASE_OUT,
    });
    expect(ease.TRANSITION_EXIT).toEqual({
      duration: ease.DURATION.fast,
      ease: ease.EASE_IN,
    });
    expect(ease.TRANSITION_DRAWER_ENTER).toEqual({
      duration: ease.DURATION.panel,
      ease: ease.EASE_DRAWER,
    });
    expect(ease.TRANSITION_DRAWER_EXIT).toEqual({
      duration: ease.DURATION.normal,
      ease: ease.EASE_IN,
    });
    expect(ease.TRANSITION_SWAP).toEqual({
      duration: ease.DURATION.fast,
      ease: ease.EASE_IN_OUT,
    });
  });
});
