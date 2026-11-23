import { describe, expect, it } from "vitest";

import {
  QUALITY_FLAG_LABELS,
  decorateCitationMarkers,
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
  it("collects entries explicitly marked retracted (flag or boolean)", () => {
    expect(
      retractedIndices([
        { index: 1, is_retracted: true },
        { index: 2, is_retracted: false },
        { index: 3, is_retracted: null },
        { index: 4, quality_flags: ["retracted"] },
        { index: 5, quality_flags: ["preprint"] },
      ]),
    ).toEqual([1, 4]);
  });
});

describe("decorateCitationMarkers", () => {
  const marker = (index: number) =>
    `<button type="button" class="citation-marker inline cursor-pointer rounded-[2px] px-0.5 ` +
    `font-semibold transition-colors text-primary hover:bg-primary/10" data-cite="${index}" ` +
    `title="查看 [E${index}] 的来源与论断核验" data-test="citation-marker">[E${index}]</button>`;

  const retractedMarker = (index: number) =>
    `<button type="button" class="citation-marker inline cursor-pointer rounded-[2px] px-0.5 ` +
    `font-semibold transition-colors bg-destructive/10 text-destructive" data-cite="${index}" ` +
    `title="该文献已被撤稿：点击查看来源与论断核验" data-test="retracted-citation">[E${index}]</button>`;

  it("turns every citation marker into a clickable button", () => {
    const html = "<p>结论 [E1]，对照 [E2]。</p>";
    expect(decorateCitationMarkers(html)).toBe(`<p>结论 ${marker(1)}，对照 ${marker(2)}。</p>`);
  });

  it("styles retracted citations with the destructive tone", () => {
    const html = "<p>[E1] 与 [E2]</p>";
    expect(decorateCitationMarkers(html, { retracted: [2] })).toBe(
      `<p>${marker(1)} 与 ${retractedMarker(2)}</p>`,
    );
  });

  it("replaces every occurrence of the same marker", () => {
    expect(decorateCitationMarkers("<p>[E2] 与 [E2]</p>", { retracted: [2] })).toBe(
      `<p>${retractedMarker(2)} 与 ${retractedMarker(2)}</p>`,
    );
  });

  it("leaves unrelated brackets and non-citation numbers untouched", () => {
    const html = "<p>[1] 与 [E10] 不是 [E1x]</p>";
    expect(decorateCitationMarkers(html)).toBe(`<p>[1] 与 ${marker(10)} 不是 [E1x]</p>`);
  });

  it("accepts any iterable of retracted indices", () => {
    expect(decorateCitationMarkers("<p>[E4]</p>", { retracted: new Set([4]) })).toContain(
      "retracted-citation",
    );
  });

  it("returns empty input unchanged", () => {
    expect(decorateCitationMarkers("")).toBe("");
  });
});
