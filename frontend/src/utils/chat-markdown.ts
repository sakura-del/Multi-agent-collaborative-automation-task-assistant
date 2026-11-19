import DOMPurify from "dompurify";
import katex from "katex";
import { Marked } from "marked";

import { renderMarkdown } from "@/utils/markdown";

/**
 * 会话消息渲染：Markdown + KaTeX 公式 + Mermaid 图（Issue #8 P0）。
 *
 * ## 与 `renderMarkdown`（结果页）的关系
 *
 * 结果页的渲染器**保持不动**（综述正文不需要公式/Mermaid，改它会牵动 8 类
 * XSS 注入向量的测试）。这里用独立 Marked 实例 + 扩展，只在会话里生效。
 *
 * ## 安全模型（与结果页同源，两条都不能省）
 *
 * 1. `$$...$$` / `$...$` 先由 **KaTeX** 转成数学 HTML（throwOnError=false，
 *    公式写错只显示红色原文，不抛异常）；
 * 2. marked 解析 → **DOMPurify** 消毒（在结果页白名单基础上追加 KaTeX 的
 *    MathML 标签与 Mermaid 代码块类名）。
 *
 * Mermaid **不在本函数里渲染**：`mermaid.render` 是异步且需要 DOM 量尺寸，
 * 由组件在挂载后用 `renderMermaidBlocks()` 把 ```mermaid 代码块替换成 SVG。
 */

//: KaTeX 输出用到的 MathML / HTML 标签（DOMPurify 追加白名单）
const KATEX_TAGS = [
  "math",
  "semantics",
  "annotation",
  "mrow",
  "mi",
  "mo",
  "mn",
  "ms",
  "mtext",
  "msup",
  "msub",
  "msubsup",
  "mfrac",
  "msqrt",
  "mroot",
  "mspace",
  "mtable",
  "mtr",
  "mtd",
  "munder",
  "mover",
  "munderover",
  "mstyle",
  "mpadded",
  "mphantom",
  "menclose",
  "merror",
  "mprescripts",
  "none",
];

const BASE_TAGS = [
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "p",
  "br",
  "hr",
  "strong",
  "em",
  "del",
  "code",
  "pre",
  "blockquote",
  "ul",
  "ol",
  "li",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "a",
  "sup",
  "sub",
  "span",
  "div",
  "svg",
  "path",
  "g",
  "text",
  "rect",
  "line",
  "circle",
];

interface MarkedToken {
  type: string;
  raw: string;
  text?: string;
  tokens?: MarkedToken[];
}

/** 独立 Marked 实例：不污染结果页使用的全局 marked 配置。 */
const chatMarked = new Marked({
  gfm: true,
  breaks: true,
});

chatMarked.use({
  extensions: [
    {
      name: "katexBlock",
      level: "block",
      start(src: string) {
        const index = src.indexOf("$$");
        return index < 0 ? undefined : index;
      },
      tokenizer(src: string) {
        const match = /^\$\$([\s\S]+?)\$\$/.exec(src);
        if (!match) return undefined;
        return { type: "katexBlock", raw: match[0], text: match[1].trim() } as never;
      },
      renderer(token: MarkedToken) {
        return renderKatex(token.text ?? "", true);
      },
    },
    {
      name: "katexInline",
      level: "inline",
      start(src: string) {
        const index = src.indexOf("$");
        return index < 0 ? undefined : index;
      },
      tokenizer(src: string) {
        const match = /^\$([^$\n]+?)\$/.exec(src);
        if (!match) return undefined;
        return { type: "katexInline", raw: match[0], text: match[1].trim() } as never;
      },
      renderer(token: MarkedToken) {
        return renderKatex(token.text ?? "", false);
      },
    },
  ],
});

function renderKatex(expression: string, displayMode: boolean): string {
  try {
    return katex.renderToString(expression, {
      displayMode,
      throwOnError: false,
      output: "htmlAndMathml",
      strict: "ignore",
    });
  } catch {
    // 极端输入（超长宏展开等）兜底：原文放进代码样式，不吞内容
    return `<code class="katex-error">${expression.replace(/[<>&]/g, "")}</code>`;
  }
}

function purifyUsable(): boolean {
  if (!DOMPurify.isSupported) return false;
  try {
    const probe = DOMPurify.sanitize("<h1>probe</h1>", { ALLOWED_TAGS: BASE_TAGS });
    return typeof probe === "string" && probe.includes("<h1");
  } catch {
    return false;
  }
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** 会话消息 → 可安全 `v-html` 的 HTML（KaTeX 已展开，Mermaid 仍是代码块）。 */
export function renderChatMarkdown(source: string | null | undefined): string {
  const text = (source ?? "").trim();
  if (!text) return "";

  // 无可靠消毒环境（纯 Node / 异常 DOM）：降级为转义纯文本，不冒险 v-html
  if (!purifyUsable()) {
    return `<pre class="md-fallback">${escapeHtml(text)}</pre>`;
  }

  const rawHtml = chatMarked.parse(text, { async: false }) as string;
  const clean = DOMPurify.sanitize(rawHtml, {
    ALLOWED_TAGS: [...BASE_TAGS, ...KATEX_TAGS],
    ALLOWED_ATTR: ["href", "title", "class", "style", "viewBox", "d", "display", "aria-hidden"],
    ADD_ATTR: ["target"],
  });

  // 外链统一新窗口 + noopener（与结果页一致）
  return clean.replace(
    /<a\s+href="(https?:[^"]+)"/g,
    '<a href="$1" target="_blank" rel="noopener noreferrer"'
  );
}

/**
 * 把已挂载 DOM 里的 ```mermaid 代码块替换为 SVG 图。
 *
 * 动态 import：mermaid 体积 ~1MB，只有出现图表时才加载。
 * `securityLevel: "strict"`：LLM 生成的图源码按不可信输入处理。
 */
export async function renderMermaidBlocks(root: HTMLElement): Promise<void> {
  const blocks = root.querySelectorAll<HTMLElement>("pre > code.language-mermaid");
  if (!blocks.length) return;

  const mermaid = (await import("mermaid")).default;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: "neutral",
    fontFamily: "inherit",
  });

  let index = 0;
  for (const block of Array.from(blocks)) {
    const source = block.textContent ?? "";
    if (!source.trim()) continue;
    const target = document.createElement("div");
    target.className = "mermaid-figure";
    try {
      const { svg } = await mermaid.render(`chat-mermaid-${Date.now()}-${index}`, source);
      target.innerHTML = svg;
      block.parentElement?.replaceWith(target);
    } catch {
      // 语法错误：保留原代码块并标注（不静默吞掉用户内容）
      const fallback = document.createElement("p");
      fallback.className = "text-xs text-destructive";
      fallback.textContent = "Mermaid 图渲染失败（源码见下方代码块）";
      block.parentElement?.before(fallback);
    }
    index += 1;
  }
}

/** 复用结果页渲染器（无公式场景的轻量路径，测试可用它对照行为）。 */
export { renderMarkdown };
