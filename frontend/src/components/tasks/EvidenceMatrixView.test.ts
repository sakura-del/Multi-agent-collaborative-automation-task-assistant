import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import EvidenceMatrixView from "@/components/tasks/EvidenceMatrixView.vue";
import type { EvidenceMatrix } from "@/api/types";

const sampleMatrix: EvidenceMatrix = {
  questions: [
    "多智能体协作是否提升检索召回率？",
    "医学影像跨模态幻觉控制是否存在研究空白？",
  ],
  cells: [
    {
      question_index: 0,
      bucket: "strong",
      claim_ids: ["n2:c1"],
      ref_indices: [1],
      evidence_kind: "excerpt",
      summary: "命中 1 条强支持论断，涉及 1 篇文献。",
      suggested_query: null,
    },
    {
      question_index: 0,
      bucket: "weak_or_caveat",
      claim_ids: ["n2:c2"],
      ref_indices: [2],
      evidence_kind: "metadata",
      summary: "命中 1 条弱支持/存疑论断（仅题录/摘要级）。",
      suggested_query: "multi-agent retrieval latency",
    },
    {
      question_index: 1,
      bucket: "gap",
      claim_ids: [],
      ref_indices: [],
      evidence_kind: "none",
      summary: "该子问题尚未命中可核验论断，建议执行定向补检索。",
      suggested_query: "medical VLM hallucination gap",
    },
  ],
};

describe("EvidenceMatrixView（Issue #32 Phase 3）", () => {
  it("渲染子问题行与四档证据强度列，显示「仅题录」微标，点击非空格子与一键补检索抛出正确事件", async () => {
    const wrapper = mount(EvidenceMatrixView, {
      props: { matrix: sampleMatrix },
    });

    expect(wrapper.find('[data-test="evidence-matrix"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="matrix-row-0"]').text()).toContain(
      "多智能体协作是否提升检索召回率",
    );
    expect(
      wrapper.find('[data-test="matrix-metadata-badge-0-weak_or_caveat"]').text(),
    ).toContain("仅题录");

    // 点击非空格子
    await wrapper.find('[data-test="matrix-cell-open-0-strong"]').trigger("click");
    const selected = wrapper.emitted("selectCell");
    expect(selected).toHaveLength(1);
    expect(selected?.[0]?.[0]).toMatchObject({
      question_index: 0,
      bucket: "strong",
      claim_ids: ["n2:c1"],
    });

    // 点击空格子「一键补检索」
    await wrapper.find('[data-test="matrix-fill-gap-1-gap"]').trigger("click");
    const filled = wrapper.emitted("fillGap");
    expect(filled).toHaveLength(1);
    expect(filled?.[0]).toEqual([1, "medical VLM hallucination gap"]);
  });
});
