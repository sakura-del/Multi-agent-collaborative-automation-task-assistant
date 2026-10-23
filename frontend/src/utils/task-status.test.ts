import { describe, expect, it } from "vitest";

import {
  isActive,
  needsUserAction,
  splitTaskTitle,
} from "@/utils/task-status";

/**
 * 标题拆分与状态分组的纯函数测试（2026-10-23 任务列表重设计）。
 *
 * 这两个函数是列表页信息架构的基础：
 * - `splitTaskTitle` 负责把「同质化墙」拆成两个视觉层级；
 * - `needsUserAction` 负责识别「唯一需要用户行动」的状态。
 *
 * 抽成纯函数测，比在组件里断言 DOM 更稳（改版式不会误伤）。
 */
describe("splitTaskTitle", () => {
  it("拆掉末尾全角括号作为次级标记", () => {
    expect(splitTaskTitle("检索增强生成评估方法综述（Issue #6 复验 #3）")).toEqual({
      main: "检索增强生成评估方法综述",
      meta: "Issue #6 复验 #3",
    });
  });

  it("支持半角括号", () => {
    expect(splitTaskTitle("Literature Review (draft)")).toEqual({
      main: "Literature Review",
      meta: "draft",
    });
  });

  it("嵌套括号只拆最外层（不能把主题切碎）", () => {
    expect(splitTaskTitle("综述（第一轮（含附录））")).toEqual({
      main: "综述",
      meta: "第一轮（含附录）",
    });
  });

  it("没有括号时不拆", () => {
    expect(splitTaskTitle("P1D 配额验收任务 481869")).toEqual({
      main: "P1D 配额验收任务 481869",
      meta: null,
    });
  });

  it("括号不在末尾时不拆（标题中间括注属于主题的一部分）", () => {
    expect(splitTaskTitle("综述（草稿）补充说明")).toEqual({
      main: "综述（草稿）补充说明",
      meta: null,
    });
  });

  it("括号内容为空时不拆（避免产生空标记）", () => {
    expect(splitTaskTitle("综述（）")).toEqual({ main: "综述（）", meta: null });
  });

  it("整个标题就是括号时不拆（没有主题可留）", () => {
    expect(splitTaskTitle("（全部）")).toEqual({ main: "（全部）", meta: null });
  });

  it("空标题安全返回", () => {
    expect(splitTaskTitle("")).toEqual({ main: "", meta: null });
    expect(splitTaskTitle("   ")).toEqual({ main: "", meta: null });
  });

  it("首尾空白被归一化（否则主标题会带多余空格）", () => {
    expect(splitTaskTitle("  综述（x）  ")).toEqual({ main: "综述", meta: "x" });
  });
});

describe("needsUserAction", () => {
  it("只有待确认规划与待人工介入需要用户行动", () => {
    expect(needsUserAction("waiting_approval")).toBe(true);
    expect(needsUserAction("waiting_human")).toBe(true);
  });

  it("终态与系统推进态都不需要用户行动", () => {
    for (const status of ["created", "planning", "running", "completed", "failed"] as const) {
      expect(needsUserAction(status)).toBe(false);
    }
  });
});

describe("isActive", () => {
  it("进行中排除终态与待用户处理", () => {
    expect(isActive("running")).toBe(true);
    expect(isActive("planning")).toBe(true);
    expect(isActive("waiting_approval")).toBe(false);
    expect(isActive("completed")).toBe(false);
  });
});
