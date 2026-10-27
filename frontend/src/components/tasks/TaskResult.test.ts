import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";

import TaskResult from "@/components/tasks/TaskResult.vue";
import ReferenceVerificationPanel from "@/components/tasks/ReferenceVerificationPanel.vue";
import type { Artifact, ReferenceVerificationSummary, ReviewReport } from "@/api/types";

const downloadArtifact = vi.fn();
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
  },
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

  it.each([1.4, -0.1, "bad"])("marks an invalid score as unavailable instead of inventing a meter: %s", (value) => {
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
    expect(panel.props("summary")).toEqual(summary);
    expect(panel.props("entries")).toEqual([
      { index: 1, title: "已验证文献", url: null, verification_status: "verified" },
      { index: 2, title: "元数据不符文献", url: null, verification_status: "metadata_mismatch" },
      { index: 3, title: "未核验文献", url: null, verification_status: null },
    ]);
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
      },
      { index: 2, title: "危险链接", url: null, verification_status: null },
      { index: 3, title: "无链接", url: null, verification_status: null },
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
    ]);
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
});
