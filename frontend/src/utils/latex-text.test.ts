import { describe, expect, it } from "vitest";

import { cleanAbstractLatex } from "@/utils/latex-text";

describe("cleanAbstractLatex（Issue #57）", () => {
  it("解包常见 LaTeX 文本修饰命令并保留内容", () => {
    expect(
      cleanAbstractLatex(
        "We propose \\textit{VeriPaper} for \\textbf{\\emph{reliable}} RAG with 95\\% accuracy.",
      ),
    ).toBe("We propose VeriPaper for reliable RAG with 95% accuracy.");
  });

  it("转换常见数学符号并剥离简单行内公式美元符", () => {
    expect(
      cleanAbstractLatex("Sample size $N \\leq 320 \\times 2$ with $\\alpha = 0.05$."),
    ).toBe("Sample size N ≤ 320 × 2 with α = 0.05.");
  });

  it("空值安全返回空字符串", () => {
    expect(cleanAbstractLatex(null)).toBe("");
    expect(cleanAbstractLatex(undefined)).toBe("");
    expect(cleanAbstractLatex("")).toBe("");
  });
});
