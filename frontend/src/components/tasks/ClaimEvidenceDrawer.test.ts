import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import type { BibliographyEntry, ClaimCheckResult } from "@/api/types";
import ClaimEvidenceDrawer from "@/components/tasks/ClaimEvidenceDrawer.vue";

const sampleEntry: BibliographyEntry = {
  index: 1,
  title: "Multi-Agent RAG Verification",
  url: "https://example.com/paper.pdf",
  verification_status: "verified",
  source_type: "peer_reviewed",
};

describe("ClaimEvidenceDrawer（Issue #35 出处定位）", () => {
  it("有 locator 时展示出处定位微标，无 locator 或摘要核验时不渲染", () => {
    const claims: ClaimCheckResult[] = [
      {
        claim_id: "n2:c1",
        sentence_text: "实验准确率在 Table 2 中提升至 94.2% [E1]。",
        cited_ref_ids: ["E1"],
        verdict: "supported",
        confidence: 0.92,
        evidence_quote: "提升至 94.2%",
        rationale: "全文明确给出数据",
        evidence_scope: "full_text",
        evidence_passage: "如 Table 2 所示，本方法准确率提升至 94.2%。",
        locator: {
          page_start: 5,
          page_end: 5,
          section_title: "3.2 实验设置",
          table_ref: "Table 2",
        },
      },
      {
        claim_id: "n2:c2",
        sentence_text: "历史任务或仅摘要引用不伪造页码 [E1]。",
        cited_ref_ids: ["E1"],
        verdict: "unverifiable",
        confidence: 0.3,
        rationale: "只有题录摘要",
        evidence_scope: "abstract_only",
        evidence_passage: "Abstract only content.",
        locator: null,
      },
    ];

    const wrapper = mount(ClaimEvidenceDrawer, {
      props: {
        open: true,
        index: 1,
        entry: sampleEntry,
        claims,
      },
      global: {
        stubs: { Teleport: true },
      },
    });

    const cards = wrapper.findAll('[data-test="claim-card"]');
    expect(cards).toHaveLength(2);

    // 按严重度排序：unverifiable (4) 先于 supported (5)
    const locatorNodes = wrapper.findAll('[data-test="claim-locator"]');
    expect(locatorNodes).toHaveLength(1);
    expect(locatorNodes[0].text()).toBe("出处定位：PDF 第 5 页 · 3.2 实验设置 · Table 2");

    // 无 locator 的卡片内部不含 claim-locator 节点
    expect(cards[0].find('[data-test="claim-locator"]').exists()).toBe(false);
    expect(cards[1].find('[data-test="claim-locator"]').text()).toBe(
      "出处定位：PDF 第 5 页 · 3.2 实验设置 · Table 2",
    );
  });
});
