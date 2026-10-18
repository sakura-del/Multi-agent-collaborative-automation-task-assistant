import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";

import TaskResult from "@/components/tasks/TaskResult.vue";
import type { Artifact, ReviewReport } from "@/api/types";

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

  it("labels quality scores in human-readable form", () => {
    const wrapper = mountResult();
    const scores = wrapper.find('[data-test="result-scores"]');

    expect(scores.text()).toContain("引用可追溯性");
    expect(scores.text()).toContain("96");
    expect(scores.text()).toContain("质量");
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
