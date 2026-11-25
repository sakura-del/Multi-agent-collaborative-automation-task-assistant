import { describe, expect, it } from "vitest";

import { deriveExtractionKey } from "./extraction-key";

describe("deriveExtractionKey", () => {
  it("将英文短语规范化为小写下划线标识", () => {
    expect(deriveExtractionKey("Sample Size")).toBe("sample_size");
    expect(deriveExtractionKey("  Code URL (GitHub)  ")).toBe("code_url_github");
  });

  it("纯中文、空串或数字开头输入生成确定性 col_<6hex> 哈希标识", () => {
    const zhKey = deriveExtractionKey("样本量");
    expect(zhKey).toMatch(/^col_[0-9a-f]{6}$/);
    expect(deriveExtractionKey("样本量")).toBe(zhKey);

    expect(deriveExtractionKey("")).toMatch(/^col_[0-9a-f]{6}$/);
    expect(deriveExtractionKey("123指标")).toMatch(/^col_[0-9a-f]{6}$/);
    expect(deriveExtractionKey("---")).toMatch(/^col_[0-9a-f]{6}$/);
  });

  it("英文前缀超过 40 字符时截断并去掉尾部下划线", () => {
    const longLabel = "a".repeat(38) + "_very_long_suffix_that_exceeds_limit";
    const key = deriveExtractionKey(longLabel);
    expect(key.length).toBeLessThanOrEqual(40);
    expect(key).toMatch(/^[a-z][a-z0-9_]{0,47}$/);
    expect(key.endsWith("_")).toBe(false);
  });

  it("存在冲突时自动附加 _2、_3 后缀并保证总长度 <= 48", () => {
    expect(deriveExtractionKey("Sample Size", ["sample_size"])).toBe("sample_size_2");
    expect(deriveExtractionKey("Sample Size", ["SAMPLE_SIZE", "sample_size_2"])).toBe(
      "sample_size_3",
    );

    const longBase = "a".repeat(47);
    const derived = deriveExtractionKey(longBase, [longBase.slice(0, 40)]);
    expect(derived.length).toBeLessThanOrEqual(48);
    expect(derived).toMatch(/^[a-z][a-z0-9_]{0,47}$/);
  });
});
