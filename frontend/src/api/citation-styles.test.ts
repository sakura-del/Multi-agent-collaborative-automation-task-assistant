import { describe, expect, it, vi } from "vitest";

import {
  deleteCitationStyle,
  listCitationStyles,
  uploadCitationStyle,
} from "@/api/citation-styles";
import type { CitationStyleOut } from "@/api/types";

vi.mock("@/api/client", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

import api from "@/api/client";

// 引用样式契约（Issue #4 M2 §1.3）：与后端 CitationStyleOut 字段一一对应
function styleRow(overrides: Partial<CitationStyleOut> = {}): CitationStyleOut {
  return {
    style_id: "gbt7714-2025-numeric",
    title: "GB/T 7714—2025（顺序编码制）",
    numbering: "citation-number",
    ordering: "citation-order",
    default_locale: "zh-CN",
    source: "preset",
    is_default: true,
    ...overrides,
  };
}

describe("citation style api", () => {
  it("lists styles together with the default style id", async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: { styles: [styleRow()], default_style_id: "gbt7714-2025-numeric" },
    });

    const result = await listCitationStyles();

    expect(api.get).toHaveBeenCalledWith("/citation-styles");
    expect(result.styles).toHaveLength(1);
    expect(result.styles[0].source).toBe("preset");
    expect(result.default_style_id).toBe("gbt7714-2025-numeric");
  });

  it("uploads a .csl as multipart without setting the content type", async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 201,
      data: styleRow({ style_id: "my-style", source: "custom", is_default: false }),
    });
    const file = new File(["<style/>"], "my-style.csl", { type: "application/xml" });

    const created = await uploadCitationStyle(file);

    const [url, body, config] = vi.mocked(api.post).mock.calls[0];
    expect(url).toBe("/citation-styles");
    expect(body).toBeInstanceOf(FormData);
    // multipart 字段名固定 `file`（后端契约）
    expect(((body as FormData).get("file") as File).name).toBe("my-style.csl");
    // 边界由浏览器生成：客户端不得写死 content-type
    expect(config?.headers).toBeUndefined();
    expect(created.source).toBe("custom");
    expect(created.style_id).toBe("my-style");
  });

  it("surfaces the backend detail when the upload is rejected with 400", async () => {
    const detail = "样式准入失败：缺少 <info><id>；检测到 CSL-M 方言，暂不支持";
    vi.mocked(api.post).mockResolvedValue({ status: 400, data: { detail } });

    await expect(uploadCitationStyle(new File(["x"], "bad.csl"))).rejects.toThrow(detail);

    // client.ts 的错误归一化只保留 message，会吞掉 FastAPI 的 detail：
    // 因此 400 必须被解析为正常响应后手工抛出，才能把原因展示给用户
    const [, , config] = vi.mocked(api.post).mock.calls[0];
    expect(typeof config?.validateStatus).toBe("function");
    expect(config?.validateStatus?.(400)).toBe(true);
    expect(config?.validateStatus?.(201)).toBe(true);
    expect(config?.validateStatus?.(500)).toBe(false);
  });

  it("joins FastAPI validation details when detail is a list", async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 400,
      data: { detail: [{ msg: "field required" }, { msg: "bad xml" }] },
    });

    await expect(uploadCitationStyle(new File(["x"], "bad.csl"))).rejects.toThrow(
      "field required；bad xml"
    );
  });

  it("falls back to a readable message when the 400 body has no detail", async () => {
    vi.mocked(api.post).mockResolvedValue({ status: 400, data: {} });

    await expect(uploadCitationStyle(new File(["x"], "bad.csl"))).rejects.toThrow("上传失败");
  });

  it("deletes a custom style by id", async () => {
    vi.mocked(api.delete).mockResolvedValue({ status: 204 });

    await deleteCitationStyle("my-style");

    expect(api.delete).toHaveBeenCalledWith("/citation-styles/my-style");
  });
});
