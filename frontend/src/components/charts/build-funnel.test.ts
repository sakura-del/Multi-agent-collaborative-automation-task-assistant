import { describe, expect, it } from "vitest";

import { buildFunnel } from "./build-funnel";

describe("buildFunnel", () => {
  const stages = [
    { id: "identified", label: "识别", value: 200 },
    { id: "deduped", label: "去重后", value: 160 },
    { id: "title", label: "标题/摘要", value: 80 },
    { id: "fulltext", label: "全文评估", value: 40 },
    { id: "included", label: "纳入", value: 10 },
  ];

  it("比例相对首段，阶段转化率相对上一段", () => {
    const { rows, overall } = buildFunnel(stages);

    expect(rows.map((row) => row.proportion)).toEqual([1, 0.8, 0.4, 0.2, 0.05]);
    expect(rows[0].stepConversion).toBe(100);
    expect(rows[1].stepConversion).toBe(80);
    expect(rows[2].stepConversion).toBe(50);
    expect(rows[4].stepConversion).toBe(25);
    // 总体转化是末段/首段，不是各阶段相乘
    expect(overall).toBe(5);
  });

  it("按 id 去重且保持顺序", () => {
    const { rows } = buildFunnel([
      { id: "a", label: "A", value: 10 },
      { id: "a", label: "重复", value: 99 },
      { id: "b", label: "B", value: 5 },
    ]);
    expect(rows.map((row) => row.id)).toEqual(["a", "b"]);
    expect(rows[0].value).toBe(10);
  });

  it("丢弃负数与非有限值（否则 path 里会出现 NaN，整张图消失）", () => {
    const { rows } = buildFunnel([
      { id: "a", label: "A", value: 10 },
      { id: "bad", label: "负", value: -1 },
      { id: "nan", label: "NaN", value: Number.NaN },
      { id: "b", label: "B", value: 4 },
    ]);
    expect(rows.map((row) => row.id)).toEqual(["a", "b"]);
    expect(rows[1].stepConversion).toBe(40);
  });

  it("首段为 0 时不产生 NaN/Infinity", () => {
    const { rows, overall } = buildFunnel([
      { id: "a", label: "识别", value: 0 },
      { id: "b", label: "纳入", value: 0 },
    ]);
    expect(rows.every((row) => Number.isFinite(row.proportion))).toBe(true);
    expect(rows[0].conversion).toBe(0);
    expect(overall).toBe(0);
  });

  it("上一段为 0 时阶段转化率记 0，不除以 0", () => {
    const { rows } = buildFunnel([
      { id: "a", label: "A", value: 5 },
      { id: "b", label: "B", value: 0 },
      { id: "c", label: "C", value: 0 },
    ]);
    expect(rows[2].stepConversion).toBe(0);
  });

  it("空数组安全", () => {
    expect(buildFunnel([])).toEqual({ rows: [], overall: 0 });
  });
});
