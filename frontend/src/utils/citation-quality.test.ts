import { describe, expect, it } from "vitest";

import {
  QUALITY_FLAG_LABELS,
  markRetractedCitations,
  parseQualityFlags,
  parseSourceType,
  retractedIndices,
} from "@/utils/citation-quality";

describe("parseQualityFlags", () => {
  it("keeps only known flags in backend order and de-duplicates", () => {
    expect(parseQualityFlags(["retracted", "preprint", "retracted", "bogus", 1])).toEqual([
      "retracted",
      "preprint",
    ]);
  });

  it("returns an empty list for missing or non-array values", () => {
    expect(parseQualityFlags(undefined)).toEqual([]);
    expect(parseQualityFlags(null)).toEqual([]);
    expect(parseQualityFlags("retracted")).toEqual([]);
  });

  it("labels every supported flag", () => {
    expect(QUALITY_FLAG_LABELS).toEqual({
      retracted: "已撤稿",
      preprint: "预印本",
      low_citation: "低被引",
    });
  });
});

describe("parseSourceType", () => {
  it("accepts the three legal values and rejects everything else", () => {
    expect(parseSourceType("peer_reviewed")).toBe("peer_reviewed");
    expect(parseSourceType("preprint")).toBe("preprint");
    expect(parseSourceType("other")).toBe("other");
    expect(parseSourceType("journal")).toBeNull();
    expect(parseSourceType(null)).toBeNull();
  });
});

describe("retractedIndices", () => {
  it("collects only entries explicitly marked retracted", () => {
    expect(
      retractedIndices([
        { index: 1, is_retracted: true },
        { index: 2, is_retracted: false },
        { index: 3, is_retracted: null },
        { index: 4 },
      ]),
    ).toEqual([1]);
  });
});

describe("markRetractedCitations", () => {
  const span = (marker: string) =>
    '<span class="rounded-[2px] bg-destructive/10 px-0.5 font-semibold text-destructive" ' +
    'title="该文献已被撤稿" data-test="retracted-citation">' +
    marker +
    "</span>";

  it("wraps only the retracted citation markers", () => {
    const html = "<p>结论 [E1]，对照 [E2]，另见 [E3]。</p>";

    expect(markRetractedCitations(html, [1, 3])).toBe(
      `<p>结论 ${span("[E1]")}，对照 [E2]，另见 ${span("[E3]")}。</p>`,
    );
  });

  it("replaces every occurrence of the same marker", () => {
    const out = markRetractedCitations("<p>[E2] 与 [E2]</p>", [2]);
    expect(out).toBe(`<p>${span("[E2]")} 与 ${span("[E2]")}</p>`);
  });

  it("leaves html untouched when nothing is retracted", () => {
    const html = "<p>[E1] [E2]</p>";
    expect(markRetractedCitations(html, [])).toBe(html);
    expect(markRetractedCitations(html, [9])).toBe(html);
  });

  it("does not touch non-citation brackets or other numbers", () => {
    const html = "<p>[1] 与 [E10] 不是 [E1x]</p>";
    expect(markRetractedCitations(html, [1])).toBe(`<p>[1] 与 [E10] 不是 [E1x]</p>`);
  });

  it("accepts any iterable of indices", () => {
    expect(markRetractedCitations("<p>[E4]</p>", new Set([4]))).toContain("retracted-citation");
  });
});
