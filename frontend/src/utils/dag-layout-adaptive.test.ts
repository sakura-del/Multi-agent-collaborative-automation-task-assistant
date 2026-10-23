import { describe, expect, it } from "vitest";

import {
  DAG_NODE_HEIGHT,
  DAG_NODE_WIDTH,
  dagBounds,
  layoutDag,
  layoutDagAdaptive,
} from "@/utils/dag-layout";
import type { PlanNode } from "@/api/types";

/**
 * DAG 自适应布局（2026-10-23 任务详情页重设计）。
 *
 * ## 缺陷背景
 *
 * 旧实现固定横向铺列。链式任务（n1→n2→n3→n4→n5）5 个节点 = 5 列，
 * 宽度 5×200 + 4×96 = **1384px**，而容器只有约 1100px →
 * 右侧 n4/n5 被裁掉。截图实测：**5 个节点只显示 3 个**。
 *
 * 横向滚动条在视觉上几乎不可见，用户会以为「任务只执行了 3 步」。
 *
 * ## 修法
 *
 * 横向放不下时换向为纵向（深度铺成行）—— 链式任务自然变成一列从上到下，
 * 不需要任何滚动。
 */

function node(id: string, dependencies: string[] = []): PlanNode {
  return {
    id,
    title: `节点 ${id}`,
    type: "search",
    dependencies,
  } as PlanNode;
}

/** 链式：n1 → n2 → n3 → n4 → n5（真实任务就是这种形状） */
const CHAIN: PlanNode[] = [
  node("n1"),
  node("n2", ["n1"]),
  node("n3", ["n2"]),
  node("n4", ["n3"]),
  node("n5", ["n4"]),
];

/** 宽扁形：3 个并列节点（横向 3 列就够） */
const WIDE: PlanNode[] = [node("a"), node("b"), node("c")];

describe("layoutDag 行为不变（回归保护）", () => {
  it("横向布局仍是按深度分列", () => {
    const positions = layoutDag(CHAIN);
    expect(positions.n1.x).toBe(0);
    expect(positions.n2.x).toBeGreaterThan(positions.n1.x);
    // 链式在同一行
    expect(positions.n2.y).toBe(positions.n1.y);
  });

  it("确定性：同样输入两次结果相同", () => {
    expect(layoutDag(CHAIN)).toEqual(layoutDag(CHAIN));
  });
});

describe("layoutDagAdaptive：宽度够时保持横向", () => {
  it("宽扁图在宽容器里保持横向", () => {
    const layout = layoutDagAdaptive(WIDE, 1400);
    expect(layout.orientation).toBe("horizontal");
    const bounds = dagBounds(layout.positions);
    expect(layout.width).toBe(bounds.width);
    expect(layout.height).toBe(bounds.height);
  });

  it("横向坐标与 layoutDag 完全一致（不改变既定几何）", () => {
    const layout = layoutDagAdaptive(WIDE, 1400);
    expect(layout.positions).toEqual(layoutDag(WIDE));
  });
});

describe("layoutDagAdaptive：宽度不够时换向纵向", () => {
  it("**核心**：链式 5 节点在窄容器里换向纵向", () => {
    // 1384px 宽 vs 1100px 容器 —— 正是实测被裁切的场景
    const layout = layoutDagAdaptive(CHAIN, 1100);
    expect(layout.orientation).toBe("vertical");
  });

  it("纵向后宽度收敛到单列（不再超出容器）", () => {
    const layout = layoutDagAdaptive(CHAIN, 1100);
    // 单列纵向：宽度应只有一个节点宽的量级，远小于横向的 1384
    expect(layout.width).toBeLessThanOrEqual(1100);
    expect(layout.width).toBeLessThan(dagBounds(layoutDag(CHAIN)).width);
  });

  it("纵向后高度按节点数增长（可纵向滚动，不会丢节点）", () => {
    const layout = layoutDagAdaptive(CHAIN, 1100);
    expect(layout.height).toBeGreaterThanOrEqual(CHAIN.length * DAG_NODE_HEIGHT * 0.8);
  });

  it("纵向布局里每个节点都有坐标（不能丢节点）", () => {
    const layout = layoutDagAdaptive(CHAIN, 1100);
    for (const item of CHAIN) {
      expect(layout.positions[item.id]).toBeDefined();
    }
    expect(Object.keys(layout.positions)).toHaveLength(CHAIN.length);
  });

  it("纵向布局按深度自上而下（n1 在最上，n5 在最下）", () => {
    const layout = layoutDagAdaptive(CHAIN, 1100);
    const ys = CHAIN.map((item) => layout.positions[item.id].y);
    for (let i = 1; i < ys.length; i += 1) {
      expect(ys[i]).toBeGreaterThan(ys[i - 1]);
    }
  });

  it("极端窄容器也要能放下（渲染为纵向单列）", () => {
    const layout = layoutDagAdaptive(CHAIN, 320);
    expect(layout.orientation).toBe("vertical");
    expect(layout.width).toBeLessThanOrEqual(320);
  });
});

describe("边界", () => {
  it("空节点集不抛异常", () => {
    const layout = layoutDagAdaptive([], 800);
    expect(Object.keys(layout.positions)).toHaveLength(0);
    expect(layout.width).toBe(DAG_NODE_WIDTH);
  });

  it("单节点始终保持横向（没有换向的必要）", () => {
    const layout = layoutDagAdaptive([node("only")], 200);
    expect(Object.keys(layout.positions)).toHaveLength(1);
    expect(layout.orientation).toBe("horizontal");
  });
});
