import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";

import TaskResult from "@/components/tasks/TaskResult.vue";
import ReferenceVerificationPanel from "@/components/tasks/ReferenceVerificationPanel.vue";
import type {
  Artifact,
  BibliographyEntry,
  ReferenceVerificationSummary,
  ReviewReport,
} from "@/api/types";

const downloadArtifact = vi.fn();
const fillMatrixGap = vi.fn();
const renderTemplateMock = vi.fn();
vi.mock("@/api/tasks", () => ({
  taskApi: {
    list: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    approve: vi.fn(),
    control: vi.fn(),
    events: vi.fn(),
    wsTicket: vi.fn(),
    downloadArtifact: (...args: unknown[]) => downloadArtifact(...args),
    fillMatrixGap: (...args: unknown[]) => fillMatrixGap(...args),
  },
}));
vi.mock("@/api/templates", () => ({
  renderTemplate: (...args: unknown[]) => renderTemplateMock(...args),
}));

function artifact(kind: "markdown" | "docx"): Artifact {
  return {
    artifact_id: kind === "markdown" ? "md-1" : "docx-1",
    kind,
    filename: `review.${kind === "markdown" ? "md" : "docx"}`,
    download_name: `文献综述.${kind === "markdown" ? "md" : "docx"}`,
    size: 1024,
    sha256: "a".repeat(64),
    created_at: "2026-10-18T04:00:00Z",
  };
}

function report(overrides: Partial<ReviewReport> = {}): ReviewReport {
  return {
    final_markdown: "# 检索增强生成综述\n\n[E1] 结论",
    bibliography: [
      { index: 1, title: "RAG 评估", source_url: "https://arxiv.org/abs/2401.00001v1" },
      { index: 2, title: "危险链接", source_url: "javascript:alert(1)" },
      { index: 3, title: "无链接" },
    ],
    scores: { citation_traceability: 0.96, quality: 0.88 },
    issues: ["第 2 节引用密度偏低"],
    stats: { documents: 10, node_count: 7 },
    ...overrides,
  };
}

function mountResult(overrides: Record<string, unknown> = {}) {
  return mount(TaskResult, {
    props: {
      taskId: "task-1",
      report: report(),
      artifacts: [artifact("markdown"), artifact("docx")],
      ...overrides,
    },
  });
}

describe("TaskResult", () => {
  beforeEach(() => {
    downloadArtifact.mockReset();
    URL.createObjectURL = vi.fn(() => "blob:mock");
    URL.revokeObjectURL = vi.fn();
  });

  it("renders the final markdown as text, never as HTML", () => {
    const wrapper = mountResult({
      report: report({ final_markdown: "# 标题\n<script>alert(1)</script>" }),
    });

    expect(wrapper.find('[data-test="result-markdown"]').text()).toContain("<script>");
    expect(wrapper.find("script").exists()).toBe(false);
  });

  it("allows only http(s) bibliography links", () => {
    const wrapper = mountResult();
    const links = wrapper.findAll('[data-test^="bibliography-link-"]');

    expect(links).toHaveLength(1);
    expect(links[0].attributes("href")).toBe("https://arxiv.org/abs/2401.00001v1");
    // javascript: 链接退化为纯文本，不生成 <a>
    expect(wrapper.find('[data-test="bibliography-item-2"]').find("a").exists()).toBe(false);
    expect(wrapper.find('[data-test="bibliography-item-2"]').text()).toContain("危险链接");
  });

  it("places the reading sheet before the evidence rail", () => {
    const wrapper = mountResult();
    const paper = wrapper.find('[data-test="result-paper"]');
    const rail = wrapper.find('[data-test="evidence-rail"]');

    expect(paper.exists()).toBe(true);
    expect(rail.exists()).toBe(true);
    expect(
      paper.element.compareDocumentPosition(rail.element) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });

  it("labels quality scores in human-readable form", () => {
    const wrapper = mountResult();
    const scores = wrapper.find('[data-test="result-scores"]');

    expect(scores.text()).toContain("引用可追溯性");
    expect(scores.text()).toContain("96");
    expect(scores.text()).toContain("质量");
  });

  it("exposes only real score values on meter elements", () => {
    const wrapper = mountResult();
    const meters = wrapper.findAll('[role="meter"]');

    expect(meters).toHaveLength(2);
    expect(meters[0].attributes("aria-valuenow")).toBe("96");
    expect(meters[0].attributes("aria-label")).toBe("引用可追溯性");
    expect(meters[1].attributes("aria-valuenow")).toBe("88");
  });

  it.each([1.4, -0.1, "bad", null, "", false, true])("marks an invalid score as unavailable instead of inventing a meter: %s", (value) => {
    const wrapper = mountResult({
      report: report({
        scores: { citation_traceability: value as unknown as number, quality: 0.88 },
      }),
    });

    const meters = wrapper.findAll('[role="meter"]');
    expect(meters).toHaveLength(1);
    expect(meters[0].attributes("aria-valuenow")).toBe("88");
    expect(wrapper.find('[data-test="result-score-unavailable"]').text()).toContain("不可用");
    expect(wrapper.html()).not.toContain('aria-valuenow="140"');
  });

  it("shows an explicit empty state when no scores are available", () => {
    const wrapper = mountResult({ report: report({ scores: {} }) });
    expect(wrapper.find('[data-test="result-scores-empty"]').text()).toContain("暂无评分");
    expect(wrapper.findAll('[role="meter"]')).toHaveLength(0);
  });

  it("shows an explicit empty state when no bibliography is available", () => {
    const wrapper = mountResult({ report: report({ bibliography: [] }) });
    expect(wrapper.find('[data-test="result-bibliography-empty"]').text()).toContain(
      "暂无参考文献"
    );
    expect(wrapper.text()).not.toContain("0 ENTRIES");
  });

  it("lists review issues", () => {
    const wrapper = mountResult();
    expect(wrapper.find('[data-test="result-issues"]').text()).toContain("第 2 节引用密度偏低");
  });

  it("downloads via server artifact ids and revokes the object URL", async () => {
    downloadArtifact.mockResolvedValue({ blob: new Blob(["x"]), filename: "文献综述.md" });
    const wrapper = mountResult();

    await wrapper.find('[data-test="download-markdown"]').trigger("click");
    await wrapper.vm.$nextTick();

    expect(downloadArtifact).toHaveBeenCalledWith("task-1", "md-1");
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock");
  });

  it("uses the DOCX artifact id for the second command", async () => {
    downloadArtifact.mockResolvedValue({ blob: new Blob(["x"]), filename: "文献综述.docx" });
    const wrapper = mountResult();

    await wrapper.find('[data-test="download-docx"]').trigger("click");
    await wrapper.vm.$nextTick();

    expect(downloadArtifact).toHaveBeenCalledWith("task-1", "docx-1");
  });

  it("disables a download command when its artifact is missing", () => {
    const wrapper = mountResult({ artifacts: [artifact("markdown")] });

    expect(wrapper.find('[data-test="download-markdown"]').attributes("disabled")).toBeUndefined();
    expect(wrapper.find('[data-test="download-docx"]').attributes("disabled")).toBeDefined();
  });
});

// ===== 引用真实性核验集成（Issue #3 遗留项 / 计划 §1.2）=====

function verificationSummary(
  overrides: Partial<ReferenceVerificationSummary> = {}
): ReferenceVerificationSummary {
  return {
    total: 3,
    verified: 1,
    metadata_mismatch: 1,
    not_found: 1,
    unverified: 0,
    cached_hits: 1,
    dropped: 0,
    intercepted: false,
    ...overrides,
  };
}

describe("TaskResult · 引用核验集成", () => {
  it("passes the parsed summary and per-entry verdicts to the panel", () => {
    const summary = verificationSummary();
    const wrapper = mountResult({
      report: report({
        stats: { documents: 10, reference_verification: summary },
        bibliography: [
          { index: 1, title: "已验证文献", verification_status: "verified" },
          { index: 2, title: "元数据不符文献", verification_status: "metadata_mismatch" },
          { index: 3, title: "未核验文献" },
        ],
      }),
    });

    const panel = wrapper.findComponent(ReferenceVerificationPanel);
    expect(panel.exists()).toBe(true);
    // 解析侧总是补 cn_source_unverified（旧报告按 0），契约见 parseVerificationSummary
    expect(panel.props("summary")).toEqual({ ...summary, cn_source_unverified: 0 });
    expect(panel.props("entries")).toEqual([
      {
        index: 1,
        title: "已验证文献",
        url: null,
        verification_status: "verified",
        is_retracted: null,
      },
      {
        index: 2,
        title: "元数据不符文献",
        url: null,
        verification_status: "metadata_mismatch",
        is_retracted: null,
      },
      {
        index: 3,
        title: "未核验文献",
        url: null,
        verification_status: null,
        is_retracted: null,
      },
    ]);
  });

  it("maps retraction state from the report into panel entries", () => {
    const wrapper = mountResult({
      report: report({
        stats: { reference_verification: verificationSummary() },
        bibliography: [
          { index: 1, title: "撤稿文献", is_retracted: true },
          { index: 2, title: "未标记文献", is_retracted: false },
          { index: 3, title: "未知文献", is_retracted: null },
        ],
      }),
    });

    const entries = wrapper
      .findComponent(ReferenceVerificationPanel)
      .props("entries") as BibliographyEntry[];

    expect(entries.map((entry) => entry.is_retracted)).toEqual([true, false, null]);
  });

  it("renders the panel above the existing bibliography list", () => {
    const wrapper = mountResult({
      report: report({
        stats: { reference_verification: verificationSummary() },
      }),
    });

    const panelElement = wrapper.findComponent(ReferenceVerificationPanel).element;
    // 用 data-test 而非样式类定位：类名属于实现细节，改版式不应弄坏测试
    const listElement = wrapper.find('[data-test="result-bibliography"]').element;
    expect(
      panelElement.compareDocumentPosition(listElement) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });

  it("passes null when the task has no reference_verification stats", () => {
    // 离线任务默认不做核验：stats 里没有该键（计划 §0.2）
    const wrapper = mountResult();
    const panel = wrapper.findComponent(ReferenceVerificationPanel);

    expect(panel.exists()).toBe(true);
    expect(panel.props("summary")).toBeNull();
    expect(panel.props("entries")).toEqual([
      {
        index: 1,
        title: "RAG 评估",
        url: "https://arxiv.org/abs/2401.00001v1",
        verification_status: null,
        is_retracted: null,
      },
      {
        index: 2,
        title: "危险链接",
        url: null,
        verification_status: null,
        is_retracted: null,
      },
      {
        index: 3,
        title: "无链接",
        url: null,
        verification_status: null,
        is_retracted: null,
      },
    ]);
  });

  it.each([
    ["非对象", "verification"],
    ["缺字段", { verified: 1, total: 1 }],
    ["计数非数字", { ...verificationSummary(), verified: "1" }],
    ["intercepted 非布尔", { ...verificationSummary(), intercepted: "no" }],
  ])("treats a malformed summary as missing: %s", (_label, value) => {
    const wrapper = mountResult({
      report: report({ stats: { documents: 10, reference_verification: value } }),
    });

    // 类型不符 → null（不抛错、不臆造 0 值）
    expect(wrapper.findComponent(ReferenceVerificationPanel).props("summary")).toBeNull();
  });

  it("keeps unknown verification_status values as null", () => {
    const wrapper = mountResult({
      report: report({
        stats: { reference_verification: verificationSummary() },
        bibliography: [
          { index: 1, title: "未知状态", verification_status: "bogus" },
          { index: 2, title: "数字状态", verification_status: 7 },
        ],
      }),
    });

    const entries = wrapper.findComponent(ReferenceVerificationPanel).props("entries") as Array<{
      verification_status: string | null;
    }>;
    expect(entries.map((entry) => entry.verification_status)).toEqual([null, null]);
  });

  it("accepts every backend verdict value", () => {
    const wrapper = mountResult({
      report: report({
        stats: { reference_verification: verificationSummary() },
        bibliography: [
          { index: 1, title: "a", verification_status: "verified" },
          { index: 2, title: "b", verification_status: "metadata_mismatch" },
          { index: 3, title: "c", verification_status: "not_found" },
          { index: 4, title: "d", verification_status: "unverified" },
          { index: 5, title: "e", verification_status: "cn_source_unverified" },
        ],
      }),
    });

    const entries = wrapper.findComponent(ReferenceVerificationPanel).props("entries") as Array<{
      verification_status: string | null;
    }>;
    expect(entries.map((entry) => entry.verification_status)).toEqual([
      "verified",
      "metadata_mismatch",
      "not_found",
      "unverified",
      "cn_source_unverified",
    ]);
  });

  it("surfaces the Chinese-source count from the summary", () => {
    const wrapper = mountResult({
      report: report({
        stats: {
          reference_verification: verificationSummary({
            total: 1,
            verified: 0,
            metadata_mismatch: 0,
            not_found: 0,
            unverified: 0,
            cn_source_unverified: 1,
          }),
        },
        bibliography: [
          { index: 1, title: "检索增强生成在软件工程中的应用综述", verification_status: "cn_source_unverified" },
        ],
      }),
    });

    const panel = wrapper.findComponent(ReferenceVerificationPanel);
    expect(panel.props("summary")).toEqual(
      expect.objectContaining({ cn_source_unverified: 1 })
    );
    expect(
      wrapper.find('[data-test="verification-count-cn_source_unverified"]').text()
    ).toContain("中文源·未核验");
    // 旧任务解析路径不受影响：缺该键时不渲染第 5 枚徽标
    const legacy = mountResult({
      report: report({ stats: { reference_verification: verificationSummary() } }),
    });
    expect(
      legacy.find('[data-test="verification-count-cn_source_unverified"]').exists()
    ).toBe(false);
  });

  it("renders the real panel badges while keeping the bibliography list intact", () => {
    const wrapper = mountResult({
      report: report({
        stats: { reference_verification: verificationSummary() },
        bibliography: [
          { index: 1, title: "已验证文献", verification_status: "verified" },
          { index: 2, title: "未核验文献" },
        ],
      }),
    });

    // 徽标由 Panel 渲染（方案 A）：我这里不再重复渲染同一 data-test
    expect(wrapper.find('[data-test="verification-panel"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="verification-count-verified"]').text()).toContain("1");
    expect(wrapper.find('[data-test="bibliography-verdict-1"]').text()).toBe("已验证");
    expect(wrapper.find('[data-test="bibliography-verdict-2"]').exists()).toBe(false);
    // 既有参考文献列表仍由 TaskResult 渲染，未被 Panel 取代
    expect(wrapper.findAll('[data-test^="bibliography-item-"]')).toHaveLength(2);
  });

  it("shows the explicit empty state when the task skipped verification", () => {
    const wrapper = mountResult();

    expect(wrapper.find('[data-test="verification-empty"]').text()).toContain(
      "本次任务未做外部引用核验"
    );
    // 未核验 ≠ 核验 0 条：不得渲染统计数字或徽标
    expect(wrapper.find('[data-test="verification-summary"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="bibliography-verdict-1"]').exists()).toBe(false);
  });

  it("warns about retracted references and highlights their citation markers", () => {
    const wrapper = mountResult({
      report: report({
        final_markdown: "# 综述\n\n论断一 [E1]，论断二 [E2]。",
        bibliography: [
          {
            index: 1,
            title: "撤稿文献",
            is_retracted: true,
            quality_flags: ["retracted", "preprint"],
            retraction_reason: "数据不可复现",
            retraction_notice_doi: "10.1234/notice",
          },
          { index: 2, title: "正常文献", is_retracted: false },
        ],
      }),
    });

    const banner = wrapper.find('[data-test="retracted-banner"]');
    expect(banner.exists()).toBe(true);
    expect(banner.text()).toContain("1 篇已撤稿文献");
    expect(banner.text()).toContain("数据不可复现");
    expect(banner.text()).toContain("10.1234/notice");

    // 只标红被撤稿的角标，其他引用保持原样
    const highlighted = wrapper.findAll('[data-test="retracted-citation"]');
    expect(highlighted).toHaveLength(1);
    expect(highlighted[0].text()).toBe("[E1]");
    expect(wrapper.find('[data-test="result-markdown"]').text()).toContain("[E2]");
  });

  it("does not warn or highlight when no reference is retracted", () => {
    const wrapper = mountResult({
      report: report({ bibliography: [{ index: 1, title: "正常文献", is_retracted: false }] }),
    });

    expect(wrapper.find('[data-test="retracted-banner"]').exists()).toBe(false);
    expect(wrapper.findAll('[data-test="retracted-citation"]')).toHaveLength(0);
  });

  it("renders quality badges per reference and aggregated counts in the panel", () => {
    const wrapper = mountResult({
      report: report({
        stats: {
          reference_verification: verificationSummary({ retracted: 1, preprint: 2, low_citation: 1 }),
        },
        bibliography: [
          {
            index: 1,
            title: "预印本且低被引",
            quality_flags: ["preprint", "low_citation"],
            cited_by_count: 2,
          },
        ],
      }),
    });

    expect(wrapper.find('[data-test="bibliography-flag-1-preprint"]').text()).toBe("预印本");
    expect(wrapper.find('[data-test="bibliography-flag-1-low_citation"]').text()).toContain("2 次");
    expect(wrapper.find('[data-test="quality-count-retracted"]').text()).toContain("1");
    expect(wrapper.find('[data-test="quality-count-preprint"]').text()).toContain("2");
    expect(wrapper.find('[data-test="quality-count-low_citation"]').text()).toContain("1");
  });

  it("hides quality counts when the summary has none (old report format)", () => {
    const wrapper = mountResult({
      report: report({ stats: { reference_verification: verificationSummary() } }),
    });

    expect(wrapper.find('[data-test="quality-summary"]').exists()).toBe(false);
  });

  it("opens the claim drawer when a citation marker is clicked", async () => {
    const wrapper = mountResult({
      report: report({
        final_markdown: "# 标题\n\n该文指出方法显著优于基线 [E1]。",
        claim_checks: [
          {
            claim_id: "n2:c1",
            sentence_text: "该文指出方法显著优于基线 [E1]。",
            cited_ref_ids: ["E1"],
            verdict: "overstated",
            confidence: 0.72,
            rationale: "原文只说观察到改善，没有「显著」",
            suggested_fix: "改为「观察到改善」",
            evidence_scope: "abstract_only",
            evidence_passage: "We observed an improvement in accuracy.",
          },
        ],
      }),
    });
    await nextTick();

    const marker = wrapper.get('[data-test="citation-marker"]');
    expect(marker.attributes("data-cite")).toBe("1");
    expect(document.body.querySelector('[data-test="claim-drawer"]')).toBeNull();

    await marker.trigger("click");
    await nextTick();

    const drawer = document.body.querySelector('[data-test="claim-drawer"]');
    expect(drawer).not.toBeNull();
    expect(drawer?.textContent).toContain("该文指出方法显著优于基线");
    expect(drawer?.textContent).toContain("原文只说观察到改善");
    expect(drawer?.textContent).toContain("夸大");
    wrapper.unmount();
  });

  it("keeps the claim drawer closed for reports without claim checks", async () => {
    const wrapper = mountResult();
    await nextTick();

    await wrapper.get('[data-test="citation-marker"]').trigger("click");
    await nextTick();

    const drawer = document.body.querySelector('[data-test="claim-drawer"]');
    expect(drawer?.textContent).toContain("还没有论断级核验结论");
    wrapper.unmount();
  });

  it("renders the evidence matrix tab, opens claim drawer on cell click, and triggers fill-gap", async () => {
    fillMatrixGap.mockReset().mockResolvedValue({
      task_id: "t-1",
      question_index: 0,
      query: "RAG gap query",
      added_count: 2,
      duplicate_count: 1,
      summary_markdown: "### 补检索",
      evidence_matrix: null,
      bibliography: [],
    });
    const wrapper = mountResult({
      report: report({
        claim_checks: [
          {
            claim_id: "n2:c1",
            sentence_text: "方法显著提升了召回率 [E1]。",
            cited_ref_ids: ["E1"],
            verdict: "supported",
            confidence: 0.9,
            rationale: "原文支持",
            evidence_scope: "full_text",
            evidence_passage: "Recall increased significantly.",
          },
        ],
        evidence_matrix: {
          questions: ["RAG 召回率提升是否稳定？"],
          cells: [
            {
              question_index: 0,
              bucket: "strong",
              claim_ids: ["n2:c1"],
              ref_indices: [1],
              evidence_kind: "excerpt",
              summary: "命中 1 条强支持论断。",
              suggested_query: null,
            },
            {
              question_index: 0,
              bucket: "gap",
              claim_ids: [],
              ref_indices: [],
              evidence_kind: "none",
              summary: "暂无更多空白证据",
              suggested_query: "RAG gap query",
            },
          ],
        },
      }),
    });

    const matrixTab = wrapper.find('[data-test="result-tab-matrix"]');
    expect(matrixTab.exists()).toBe(true);
    await matrixTab.trigger("click");
    await nextTick();

    // 点击非空格子打开抽屉
    await wrapper.find('[data-test="matrix-cell-open-0-strong"]').trigger("click");
    await nextTick();
    const drawer = document.body.querySelector('[data-test="claim-drawer"]');
    expect(drawer?.textContent).toContain("证据矩阵 · Q1 · 强支持");
    expect(drawer?.textContent).toContain("方法显著提升了召回率");

    // 点击空格子一键补检索
    await wrapper.find('[data-test="matrix-fill-gap-0-gap"]').trigger("click");
    await nextTick();
    await nextTick();
    expect(fillMatrixGap).toHaveBeenCalledWith("task-1", {
      question_index: 0,
      query: "RAG gap query",
    });
    expect(wrapper.emitted("refresh")).toHaveLength(1);
    wrapper.unmount();
  });

  it("切换证据矩阵后通过 v-show 保活正文容器并使用中文文案与 scaleX 评分条", async () => {
    const wrapper = mountResult({
      report: report({
        evidence_matrix: {
          questions: ["Q1"],
          cells: [
            {
              question_index: 0,
              bucket: "strong",
              claim_ids: [],
              ref_indices: [1],
              evidence_kind: "excerpt",
              summary: "ok",
              suggested_query: null,
            },
          ],
        },
      }),
    });

    expect(wrapper.text()).toContain("终稿复盘");
    expect(wrapper.text()).toContain("百分制评分");
    expect(wrapper.text()).toContain("共 3 条");
    expect(wrapper.text()).not.toContain("Final review");
    expect(wrapper.text()).not.toContain("SCORE / 100");

    // 评分条使用 scaleX
    expect(wrapper.find('[data-test="result-scores"] [style*="scaleX"]').exists()).toBe(true);

    // 切到矩阵 Tab 后正文容器仍留在 DOM（v-show 保活），只是 display: none
    await wrapper.find('[data-test="result-tab-matrix"]').trigger("click");
    await nextTick();
    const md = wrapper.find('[data-test="result-markdown"]');
    expect(md.exists()).toBe(true);
    expect((md.element as HTMLElement).style.display).toBe("none");
  });

  it("支持打开高校标准母版导出弹窗、记忆封面信息并调用 renderTemplate（Phase 1）", async () => {
    localStorage.clear();
    renderTemplateMock.mockResolvedValueOnce(
      new Blob(["docx"], {
        type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      }),
    );
    const createObjectUrl = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue("blob:thesis-mock");
    const revokeObjectUrl = vi
      .spyOn(URL, "revokeObjectURL")
      .mockImplementation(() => undefined);

    const wrapper = mountResult({
      report: report({
        final_markdown: "# 大语言模型综述\n\n## 研究现状\n\n正文字段。",
      }),
    });

    await wrapper.get('[data-test="export-thesis-template-btn"]').trigger("click");
    await nextTick();

    const dialog = document.body.querySelector('[data-test="thesis-template-dialog"]');
    expect(dialog).not.toBeNull();

    const authorInput = document.body.querySelector(
      '[data-test="thesis-cover-author"]',
    ) as HTMLInputElement;
    authorInput.value = "王小明";
    authorInput.dispatchEvent(new Event("input"));
    await nextTick();

    const confirmBtn = document.body.querySelector(
      '[data-test="thesis-template-confirm"]',
    ) as HTMLButtonElement;
    confirmBtn.click();
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));

    expect(renderTemplateMock).toHaveBeenCalledWith(
      "builtin:literature_review_cn_standard",
      expect.objectContaining({
        sections: expect.objectContaining({
          title: "大语言模型综述",
          author: "王小明",
          body: "# 大语言模型综述\n\n## 研究现状\n\n正文字段。",
        }),
      }),
    );
    expect(JSON.parse(localStorage.getItem("vp:thesis-cover-meta")!)).toMatchObject({
      author: "王小明",
    });

    createObjectUrl.mockRestore();
    revokeObjectUrl.mockRestore();
    wrapper.unmount();
  });
});
