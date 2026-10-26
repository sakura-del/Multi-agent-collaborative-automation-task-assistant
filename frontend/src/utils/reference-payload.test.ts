import { describe, expect, it } from "vitest";

import type { ImportedReferenceItem } from "@/api/types";
import {
  toCslItemPayload,
  toCslItemPayloads,
  toCslName,
} from "@/utils/reference-payload";

// 导入预览 → CslItem 形态的转换（引用库保存用）。
//
// 这两个结构形状不同是有意的：导入响应面向展示（扁平、authors 是 string[]），
// 引用库面向存储（与 CSL 管线同构、author 是 CslName[]、字段名 issued_year）。
// 转换写错会被后端 `extra="forbid"` 拒掉，但那时用户看到的只是一句 400，
// 所以这里要测细。

function item(overrides: Partial<ImportedReferenceItem> = {}): ImportedReferenceItem {
  return {
    id: "doi:10.1234/a",
    type: "article-journal",
    title: "检索增强生成评估方法综述",
    authors: ["牛, 耕田", "李四"],
    year: 2024,
    container_title: "计算机学报",
    volume: "47",
    issue: "3",
    page: "1-20",
    publisher: null,
    doi: "10.1234/a",
    url: null,
    ...overrides,
  };
}

describe("toCslName", () => {
  it("拆分 Family, Given", () => {
    expect(toCslName("牛, 耕田")).toEqual({ family: "牛", given: "耕田" });
  });

  it("无逗号时整串当 family", () => {
    expect(toCslName("李四")).toEqual({ family: "李四", given: "" });
  });

  it("空串返回空姓名（调用方会过滤掉）", () => {
    expect(toCslName("   ")).toEqual({ family: "", given: "" });
  });

  it("多余逗号只按第一个拆", () => {
    expect(toCslName("Es, Shahul, Jr")).toEqual({ family: "Es", given: "Shahul, Jr" });
  });
});

describe("toCslItemPayload", () => {
  it("字段名映射正确（year → issued_year，authors → author）", () => {
    const payload = toCslItemPayload(item());

    expect(payload.id).toBe("doi:10.1234/a");
    expect(payload.type).toBe("article-journal");
    expect(payload.title).toBe("检索增强生成评估方法综述");
    expect(payload.issued_year).toBe(2024);
    expect(payload.author).toEqual([
      { family: "牛", given: "耕田" },
      { family: "李四", given: "" },
    ]);
    // 不能出现展示结构的字段名（后端 extra="forbid" 会拒）
    expect(payload).not.toHaveProperty("year");
    expect(payload).not.toHaveProperty("authors");
  });

  it("只写有值的可选字段（null 不写）", () => {
    const payload = toCslItemPayload(item({ publisher: null, url: null }));

    expect(payload).not.toHaveProperty("publisher");
    expect(payload).not.toHaveProperty("url");
    expect(payload.container_title).toBe("计算机学报");
  });

  it("year 为 null 时不写 issued_year（后端准入会拒绝入库并说明原因）", () => {
    const payload = toCslItemPayload(item({ year: null }));

    expect(payload).not.toHaveProperty("issued_year");
  });

  it("id 为 null 时用题名派生稳定标识", () => {
    // CslItem.id 必填且是引用库幂等键，不能留空
    const payload = toCslItemPayload(item({ id: null }));

    expect(typeof payload.id).toBe("string");
    expect(String(payload.id)).toMatch(/^title:/);
    expect(String(payload.id)).toContain("检索增强生成评估方法综述");
  });

  it("同一个题名两次派生出的 id 相同（幂等键必须稳定）", () => {
    const first = toCslItemPayload(item({ id: null }));
    const second = toCslItemPayload(item({ id: null }));

    expect(first.id).toBe(second.id);
  });

  it("空作者列表不产生空姓名条目", () => {
    const payload = toCslItemPayload(item({ authors: ["", "  "] }));

    expect(payload.author).toEqual([]);
  });
});

describe("toCslItemPayloads", () => {
  it("跳过没有题名的条目（CslItem.title 必填）", () => {
    const payloads = toCslItemPayloads([
      item({ id: "a", title: "有题名" }),
      item({ id: "b", title: "   " }),
    ]);

    expect(payloads).toHaveLength(1);
    expect(payloads[0].id).toBe("a");
  });

  it("全部有条名时全部保留", () => {
    const payloads = toCslItemPayloads([item({ id: "a" }), item({ id: "b" })]);
    expect(payloads).toHaveLength(2);
  });

  it("空输入返回空数组", () => {
    expect(toCslItemPayloads([])).toEqual([]);
  });
});
