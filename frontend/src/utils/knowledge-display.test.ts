import { describe, expect, it } from "vitest";

import type { KnowledgeDocument } from "@/api/types";
import {
  docTypeLabel,
  needsAttention,
  shouldShowOriginalName,
  shouldShowSourceColumn,
} from "@/utils/knowledge-display";

/**
 * 知识库展示逻辑（2026-10-23 重设计）。
 *
 * 这些判断有真实边界情况（扩展名大小写、`.markdown` 双后缀、句点文件名），
 * 放在纯函数里测比在组件里断言 DOM 更稳 —— 改版式不会误伤。
 */

function doc(overrides: Partial<KnowledgeDocument> = {}): KnowledgeDocument {
  return {
    id: "d1",
    title: "标题",
    doc_type: "pdf",
    index_status: "ready",
    chunk_count: 10,
    created_at: "2026-10-18T00:00:00Z",
    source: "upload",
    original_name: null,
    failure_reason: null,
    ...overrides,
  };
}

describe("docTypeLabel", () => {
  it("常见类型给可读标签", () => {
    expect(docTypeLabel("pdf")).toBe("PDF");
    expect(docTypeLabel("docx")).toBe("DOCX");
    expect(docTypeLabel("md")).toBe("Markdown");
    expect(docTypeLabel("markdown")).toBe("Markdown");
    expect(docTypeLabel("txt")).toBe("TXT");
  });

  it("大小写不敏感", () => {
    expect(docTypeLabel("PDF")).toBe("PDF");
    expect(docTypeLabel(" Md ")).toBe("Markdown");
  });

  it("未知类型原样大写（不吞掉信息）", () => {
    expect(docTypeLabel("epub")).toBe("EPUB");
  });

  it("空值安全", () => {
    expect(docTypeLabel("")).toBe("");
  });
});

describe("shouldShowOriginalName（标题去噪）", () => {
  it("文件名 = 标题 + 扩展名 → 不显示（实测 11 篇里 10 篇是这种）", () => {
    expect(
      shouldShowOriginalName(
        doc({
          title: "10_EVOR_Evolving_Retrieval_CodeGen_2402.12317v2",
          original_name: "10_EVOR_Evolving_Retrieval_CodeGen_2402.12317v2.pdf",
        }),
      ),
    ).toBe(false);
  });

  it("扩展名大小写不影响判定", () => {
    expect(
      shouldShowOriginalName(doc({ title: "某文档", original_name: "某文档.PDF" })),
    ).toBe(false);
  });

  it(".markdown 双后缀能正确剥离", () => {
    expect(
      shouldShowOriginalName(doc({ title: "笔记", original_name: "笔记.markdown" })),
    ).toBe(false);
  });

  it("标题与文件名不同 → 显示（信息不丢）", () => {
    expect(
      shouldShowOriginalName(
        doc({ title: "我的综述", original_name: "10_EVOR_2402.12317v2.pdf" }),
      ),
    ).toBe(true);
  });

  it("没有文件名 → 不显示", () => {
    expect(shouldShowOriginalName(doc({ original_name: null }))).toBe(false);
    expect(shouldShowOriginalName(doc({ original_name: "  " }))).toBe(false);
  });

  it("首尾空白差异不算不同", () => {
    expect(
      shouldShowOriginalName(doc({ title: " 标题 ", original_name: "标题.pdf " })),
    ).toBe(false);
  });
});

describe("shouldShowSourceColumn", () => {
  it("来源全相同 → 隐藏（常量列无信息量）", () => {
    expect(
      shouldShowSourceColumn([doc({ source: "upload" }), doc({ source: "upload" })]),
    ).toBe(false);
  });

  it("来源有差异 → 显示", () => {
    expect(
      shouldShowSourceColumn([doc({ source: "upload" }), doc({ source: "arxiv" })]),
    ).toBe(true);
  });

  it("大小写差异不算不同（避免仅因大小写显示整列）", () => {
    expect(
      shouldShowSourceColumn([doc({ source: "Upload" }), doc({ source: "upload" })]),
    ).toBe(false);
  });

  it("空列表 → 隐藏", () => {
    expect(shouldShowSourceColumn([])).toBe(false);
  });

  it("单条 → 隐藏（一行没有对比）", () => {
    expect(shouldShowSourceColumn([doc({ source: "upload" })])).toBe(false);
  });
});

describe("needsAttention", () => {
  it("索引失败需要用户处理（重新上传）", () => {
    expect(needsAttention(doc({ index_status: "failed" }))).toBe(true);
  });

  it("就绪 / 索引中都不需要用户动手", () => {
    expect(needsAttention(doc({ index_status: "ready" }))).toBe(false);
    expect(needsAttention(doc({ index_status: "indexing" }))).toBe(false);
  });
});
