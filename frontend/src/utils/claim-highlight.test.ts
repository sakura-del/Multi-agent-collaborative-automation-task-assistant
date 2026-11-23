import { describe, expect, it } from "vitest";

import { escapeHtml, highlightQuote } from "@/utils/claim-highlight";

describe("escapeHtml", () => {
  it("escapes HTML control characters", () => {
    expect(escapeHtml('<a href="x">&\'</a>')).toBe(
      "&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;",
    );
  });
});

describe("highlightQuote", () => {
  it("wraps an exact substring with a mark", () => {
    const result = highlightQuote("Graph neural networks improve recall.", "improve recall");
    expect(result.matched).toBe(true);
    expect(result.html).toContain('<mark class="rounded-[2px] bg-amber-200/70');
    expect(result.html).toContain("improve recall</mark>");
  });

  it("ignores whitespace differences (newlines collapsed by the model)", () => {
    const passage = "第一句结论。\n第二句证据：模型在长文本上\n仍然保持稳定。";
    const result = highlightQuote(passage, "模型在长文本上 仍然保持稳定");
    expect(result.matched).toBe(true);
    expect(result.html).toContain("<mark");
  });

  it("matches case-insensitively", () => {
    const result = highlightQuote("Retrieval Augmented Generation works.", "retrieval augmented generation");
    expect(result.matched).toBe(true);
  });

  it("does not mark a quote that is missing from the passage", () => {
    const result = highlightQuote("The paper studies graphs.", "The paper proves transformers are better");
    expect(result.matched).toBe(false);
    expect(result.html).not.toContain("<mark");
    expect(result.html).toBe("The paper studies graphs.");
  });

  it("escapes HTML in both matched and unmatched output", () => {
    const passage = "风险 <script>alert(1)</script> 与结论";
    const result = highlightQuote(passage, "alert(1)");
    expect(result.html).not.toContain("<script>");
    expect(result.html).toContain("&lt;script&gt;");
    expect(result.matched).toBe(true);
  });

  it("returns the escaped passage when quote is null/empty", () => {
    expect(highlightQuote("a < b", null)).toEqual({ html: "a &lt; b", matched: false });
    expect(highlightQuote("", "x")).toEqual({ html: "", matched: false });
  });
});
