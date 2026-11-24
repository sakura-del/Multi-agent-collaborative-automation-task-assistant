/**
 * 清洗学术元数据（如 arXiv / CrossRef）标题与摘要中残留的轻量 LaTeX 标记（Issue #57）。
 *
 * 目标：让筛选卡片中的摘要可直接阅读，不再出现 `\textit{...}`、`\emph{...}`、
 * `\%` 或裸 `$...$` 等原始标记，同时保持纯文本安全（不生成 HTML）。
 */

const TEXT_WRAPPER_RE =
  /\\(?:textit|textbf|emph|texttt|textrm|textsf|text|mathrm|mathbf|mathit|mathcal|mathsf|underline|textsc)\{([^{}]*)\}/g;

const SYMBOL_MAP: Record<string, string> = {
  "\\times": "×",
  "\\pm": "±",
  "\\leq": "≤",
  "\\le": "≤",
  "\\geq": "≥",
  "\\ge": "≥",
  "\\approx": "≈",
  "\\neq": "≠",
  "\\ne": "≠",
  "\\rightarrow": "→",
  "\\to": "→",
  "\\leftarrow": "←",
  "\\infty": "∞",
  "\\alpha": "α",
  "\\beta": "β",
  "\\gamma": "γ",
  "\\delta": "δ",
  "\\epsilon": "ε",
  "\\lambda": "λ",
  "\\mu": "μ",
  "\\sigma": "σ",
  "\\tau": "τ",
  "\\theta": "θ",
  "\\omega": "ω",
  "\\Delta": "Δ",
  "\\Sigma": "Σ",
  "\\Omega": "Ω",
};

const SYMBOL_RE = new RegExp(
  `${Object.keys(SYMBOL_MAP)
    .sort((a, b) => b.length - a.length)
    .map((key) => key.replace(/\\/g, "\\\\"))
    .join("|")}(?![a-zA-Z])`,
  "g",
);

export function cleanAbstractLatex(input: string | null | undefined): string {
  if (!input) {
    return "";
  }
  let text = input;

  // 多次解包支持轻度嵌套（如 \textbf{\textit{RAG}}）
  for (let pass = 0; pass < 3; pass += 1) {
    const next = text.replace(TEXT_WRAPPER_RE, "$1");
    if (next === text) break;
    text = next;
  }

  // 常见 LaTeX 数学符号与转义符
  text = text
    .replace(SYMBOL_RE, (match) => SYMBOL_MAP[match] ?? match)
    .replace(/\\([%&#$_])/g, "$1")
    .replace(/``/g, "“")
    .replace(/''/g, "”")
    .replace(/~/g, " ")
    // 剥离简单行内公式包裹的 $...$，保留内部可读表达式
    .replace(/\$([^$\n]{1,80})\$/g, "$1")
    .replace(/\s+/g, " ")
    .trim();

  return text;
}
