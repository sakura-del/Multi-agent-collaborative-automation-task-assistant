import { describe, expect, it } from "vitest";

import {
  CLAIM_VERDICT_LABELS,
  claimSeverity,
  formatEvidenceLocator,
  parseClaimChecks,
} from "@/utils/claim-verdict";

describe("parseClaimChecks", () => {
  it("keeps valid checks and clamps confidence", () => {
    const [check] = parseClaimChecks([
      {
        claim_id: "n2:c1",
        sentence_text: "该文指出方法显著优于基线 [E1]。",
        cited_ref_ids: ["E1"],
        verdict: "overstated",
        confidence: 1.7,
        rationale: "证据只说观察到改善",
        suggested_fix: "把「显著优于」改为「观察到改善」",
        evidence_scope: "abstract_only",
        evidence_passage: "We observed an improvement.",
      },
    ]);
    expect(check.verdict).toBe("overstated");
    expect(check.confidence).toBe(1);
    expect(check.cited_ref_ids).toEqual(["E1"]);
  });

  it("drops entries with unknown verdicts or missing identity", () => {
    const checks = parseClaimChecks([
      { claim_id: "a", sentence_text: "x", verdict: "totally_bogus" },
      { claim_id: "", sentence_text: "x", verdict: "supported" },
      { claim_id: "b", sentence_text: "", verdict: "supported" },
      { claim_id: "c", sentence_text: "y", verdict: "supported", cited_ref_ids: ["E2", 3] },
    ]);
    expect(checks).toHaveLength(1);
    expect(checks[0].claim_id).toBe("c");
    expect(checks[0].cited_ref_ids).toEqual(["E2"]);
  });

  it("returns an empty list for non-array payloads", () => {
    expect(parseClaimChecks(undefined)).toEqual([]);
    expect(parseClaimChecks({ claim_checks: [] })).toEqual([]);
  });

  it("labels every verdict and orders the most urgent first", () => {
    expect(CLAIM_VERDICT_LABELS.contradicted).toBe("矛盾");
    expect(claimSeverity("contradicted")).toBeLessThan(claimSeverity("overstated"));
    expect(claimSeverity("supported")).toBeGreaterThan(claimSeverity("unverifiable"));
  });

  it("parses valid locator and drops empty or invalid locator fields", () => {
    const checks = parseClaimChecks([
      {
        claim_id: "c-loc",
        sentence_text: "实验提升显著 [E1]。",
        verdict: "supported",
        confidence: 0.9,
        locator: {
          page_start: 5,
          page_end: 6,
          section_title: " 3.2 实验设置 ",
          table_ref: " Table 2 ",
        },
      },
      {
        claim_id: "c-bad",
        sentence_text: "非法页码被过滤 [E2]。",
        verdict: "unverifiable",
        confidence: 0.2,
        locator: { page_start: 0, page_end: -1, section_title: "   ", table_ref: "" },
      },
    ]);

    expect(checks).toHaveLength(2);
    expect(checks[0].locator).toEqual({
      page_start: 5,
      page_end: 6,
      section_title: "3.2 实验设置",
      table_ref: "Table 2",
    });
    expect(checks[1].locator).toBeNull();
  });
});

describe("formatEvidenceLocator", () => {
  it("formats single page, cross-page, section-only, and null locators", () => {
    expect(
      formatEvidenceLocator({
        page_start: 5,
        page_end: 5,
        section_title: "3.2 实验设置",
        table_ref: "Table 2",
      }),
    ).toBe("PDF 第 5 页 · 3.2 实验设置 · Table 2");

    expect(
      formatEvidenceLocator({
        page_start: 3,
        page_end: 4,
        table_ref: "Table 1",
      }),
    ).toBe("PDF 第 3–4 页 · Table 1");

    expect(formatEvidenceLocator({ section_title: "引言" })).toBe("引言");
    expect(formatEvidenceLocator(null)).toBeNull();
    expect(formatEvidenceLocator({})).toBeNull();
  });
});
