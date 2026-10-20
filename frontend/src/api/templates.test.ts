import { describe, expect, it, vi } from "vitest";

import {
  deleteTemplate,
  listTemplates,
  renderTemplate,
  uploadTemplate,
  templateRejectionDetail,
} from "@/api/templates";
import type { TemplateOut } from "@/api/types";

vi.mock("@/api/client", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

import api from "@/api/client";

// Word 模板契约（Phase 1 P1-A 端点接线）：与后端 TemplateOut 字段一一对应
function templateRow(overrides: Partial<TemplateOut> = {}): TemplateOut {
  return {
    template_id: "tpl-9e961b86377bb8b1",
    name: "学院模板",
    checksum: "a".repeat(64),
    size: 4096,
    placeholders: [
      { key: "title", label: "", required: true, sample: "" },
      { key: "body", label: "", required: true, sample: "" },
    ],
    style_map: { Heading1: "h1", Normal: "body" },
    ...overrides,
  };
}

describe("templates api", () => {
  it("listTemplates 请求 /templates 并返回条目", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { items: [templateRow()] } });

    const result = await listTemplates();

    expect(api.get).toHaveBeenCalledWith("/templates");
    expect(result).toHaveLength(1);
    expect(result[0].template_id).toBe("tpl-9e961b86377bb8b1");
  });

  it("uploadTemplate 用 multipart 字段 file 与 name 提交", async () => {
    vi.mocked(api.post).mockResolvedValue({ status: 201, data: templateRow() });
    const file = new File(["fake"], "school.docx");

    const created = await uploadTemplate(file, "学院模板");

    expect(api.post).toHaveBeenCalledTimes(1);
    const [url, body] = vi.mocked(api.post).mock.calls[0];
    expect(url).toBe("/templates");
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).get("file")).toBeInstanceOf(File);
    expect((body as FormData).get("name")).toBe("学院模板");
    expect(created.name).toBe("学院模板");
  });

  it("uploadTemplate 把 400 的 detail 作为可展示错误抛出", async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 400,
      data: { detail: "模板包含宏（vbaProject.bin），已拒绝" },
    });

    await expect(uploadTemplate(new File(["x"], "evil.docx"), "带宏")).rejects.toThrow(
      "模板包含宏（vbaProject.bin），已拒绝"
    );
  });

  it("uploadTemplate 在 400 且无 detail 时给出兜底文案", async () => {
    vi.mocked(api.post).mockResolvedValue({ status: 400, data: {} });

    await expect(uploadTemplate(new File(["x"], "a.docx"), "x")).rejects.toThrow(
      "模板上传失败（400）"
    );
  });

  it("deleteTemplate 对 id 做 URL 编码", async () => {
    vi.mocked(api.delete).mockResolvedValue({ status: 204 });

    await deleteTemplate("tpl/with space");

    expect(api.delete).toHaveBeenCalledWith("/templates/tpl%2Fwith%20space");
  });

  it("renderTemplate 以 blob 请求并把结果包成 Blob", async () => {
    const blob = new Blob(["docx-bytes"]);
    vi.mocked(api.post).mockResolvedValue({ status: 200, data: blob });

    const result = await renderTemplate("tpl-1", {
      sections: { title: "标题" },
      bibliography: ["[1] 张三. 论文[J]. 期刊, 2024."],
    });

    expect(api.post).toHaveBeenCalledTimes(1);
    const [url, body, config] = vi.mocked(api.post).mock.calls[0];
    expect(url).toBe("/templates/tpl-1/render");
    expect(body).toEqual({
      sections: { title: "标题" },
      bibliography: ["[1] 张三. 论文[J]. 期刊, 2024."],
    });
    expect((config as { responseType?: string }).responseType).toBe("blob");
    expect(result).toBeInstanceOf(Blob);
  });

  it("renderTemplate 把 400 的 detail 作为可展示错误抛出", async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 400,
      data: { detail: "模板要求占位符 title，但未提供内容" },
    });

    await expect(
      renderTemplate("tpl-1", { sections: {}, bibliography: [] })
    ).rejects.toThrow("模板要求占位符 title，但未提供内容");
  });

  it("templateRejectionDetail 兼容字符串与数组 detail", () => {
    expect(templateRejectionDetail("原因A")).toBe("原因A");
    expect(templateRejectionDetail({ detail: "原因B" })).toBe("原因B");
    expect(templateRejectionDetail({ detail: [{ msg: "原因C" }, { msg: "原因D" }] })).toBe(
      "原因C；原因D"
    );
    expect(templateRejectionDetail({})).toBeNull();
    expect(templateRejectionDetail(null)).toBeNull();
  });
});
