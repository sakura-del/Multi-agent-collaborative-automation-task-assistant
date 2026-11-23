import { describe, expect, it } from "vitest";

import { CLAIM_VERDICT_LABELS, claimSeverity, parseClaimChecks } from "@/utils/claim-verdict";

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
});
