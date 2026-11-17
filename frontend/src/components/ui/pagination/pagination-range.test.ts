import { describe, expect, it } from "vitest";

import { paginationRange } from "./pagination-range";

describe("paginationRange", () => {
  it("页数装得下时全列，不出现省略号", () => {
    expect(paginationRange(1, 7)).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(paginationRange(3, 5)).toEqual([1, 2, 3, 4, 5]);
  });

  it("靠前：只出现后省略号", () => {
    expect(paginationRange(2, 20)).toEqual([1, 2, 3, 4, 5, "ellipsis-end", 20]);
  });

  it("中间：两侧省略号，窗口跟手", () => {
    expect(paginationRange(6, 20)).toEqual([1, "ellipsis-start", 5, 6, 7, "ellipsis-end", 20]);
    expect(paginationRange(10, 20)).toEqual([1, "ellipsis-start", 9, 10, 11, "ellipsis-end", 20]);
  });

  it("靠后：只出现前省略号", () => {
    expect(paginationRange(19, 20)).toEqual([1, "ellipsis-start", 16, 17, 18, 19, 20]);
  });

  it("首尾页不越界", () => {
    expect(paginationRange(1, 20)).toEqual([1, 2, 3, 4, 5, "ellipsis-end", 20]);
    expect(paginationRange(20, 20)).toEqual([1, "ellipsis-start", 16, 17, 18, 19, 20]);
  });

  it("长度恒定：翻页时整行宽度不跳", () => {
    const lengths = new Set<number>();
    for (let page = 1; page <= 20; page += 1) {
      lengths.add(paginationRange(page, 20).length);
    }
    expect([...lengths]).toEqual([7]);
  });

  it("参数非法时收敛到合法范围", () => {
    expect(paginationRange(0, 20)[0]).toBe(1);
    const clamped = paginationRange(999, 20);
    expect(clamped[clamped.length - 1]).toBe(20);
    expect(paginationRange(1, 0)).toEqual([1]);
  });

  it("siblings=2 时窗口更宽，长度随之变化", () => {
    const items = paginationRange(10, 30, 2, 1);
    expect(items).toEqual([1, "ellipsis-start", 8, 9, 10, 11, 12, "ellipsis-end", 30]);
  });
});
