import DOMPurify from "dompurify";
import { marked } from "marked";

/**
 * 综述正文的 Markdown → 安全 HTML（2026-10-23 任务详情页重设计）。
 *
 * ## 为什么需要它
 *
 * 结果页原先用 `<pre>{{ final_markdown }}</pre>` 纯文本渲染 —— 那条注释写的
 * 原因是「不用 v-html」（安全考虑）。但用户看到的是一篇**带井号和星号的综述**：
 *
 * ```
 * # 检索增强生成评估方法综述
 * ## 摘要
 * **[E1] Ragas 提出...**
 * ```
 *
 * 这是核心产出页，可读性影响最大。
 *
 * ## 安全模型（**这段最重要**）
 *
 * 综述正文 = LLM 输出 + 用户主题 + 文献元数据 → **必须视为不可信输入**。
 * 直接 `v-html` 会有 XSS 风险（LLM 可能被诱导输出 `<img onerror=...>`，
 * 文献标题也可能带 HTML）。
 *
 * 因此走两段式：`marked` 解析 → `DOMPurify` 消毒（白名单）。
 * **没有第二步就不能用 v-html**。测试里锁了 8 类注入向量。
 *
 * ## 为什么不用自研渲染器
 *
 * AGENTS §6.5.1 要求「优先复用成熟库」。Markdown 语法边界极多
 * （嵌套列表、引用块、表格、代码围栏），自研解析器的长期维护成本远高于
 * 引两个零运行时依赖的成熟库。
 *
 * ## DOMPurify 在无 DOM 环境下会退化（实测踩到）
 *
 * `dompurify` 需要真实 DOM。实测：
 *
 * | 环境 | `isSupported` | 行为 |
 * | --- | --- | --- |
 * | 浏览器 | `true` | 正常消毒 |
 * | 纯 Node（无 DOM） | `false` | 实例不可用 |
 * | happy-dom（vitest 默认） | `true` 但 DOM 实现有差异 | **静默剥掉 `<h1>`/`<ul>` 等标签** |
 *
 * 第三种最危险：`isSupported` 为 true，但输出被剥成纯文本 —— 看起来「消毒成功」
 * 了，其实是渲染坏了。所以这里显式探测能力，探测不过就走**保守降级**：
 * 输出转义后的纯文本（宁可少渲染，不可不安全、也不可静默丢格式）。
 */

//: 消毒白名单：只允许综述里可能出现的排版标签。
//: 不用 DOMPurify 默认白名单 —— 默认允许 `<form>` / `<input>` 等交互元素，
//: 在只读正文区没有意义，反而扩大攻击面。
const ALLOWED_TAGS = [
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
];

//: 只允许链接的必要属性（`target` 与 `rel` 由下面统一补齐）。
const ALLOWED_ATTR = ["href", "title"];

marked.setOptions({
  gfm: true,
  breaks: false,
});

/**
 * 探测 DOMPurify 在当前环境是否**真正可用**。
 *
 * 不能只看 `isSupported` —— happy-dom 下它是 `true`，但会剥掉合法标签。
 * 所以用一个探针：`<h1>a</h1>` 消毒后必须仍然包含 `<h1`。
 */
function domPurifyUsable(): boolean {
  if (!DOMPurify.isSupported) {
    return false;
  }
  try {
    const probe = DOMPurify.sanitize("<h1>probe</h1>", { ALLOWED_TAGS });
    return typeof probe === "string" && probe.includes("<h1");
  } catch {
    return false;
  }
}

/** HTML 转义（降级通道与文本兜底共用）。 */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * 把 Markdown 渲染成可安全 `v-html` 的 HTML。
 *
 * @param source Markdown 原文（可为 null / undefined / 空串）
 * @returns 消毒后的 HTML；输入为空时返回空串
 *
 * @example
 * renderMarkdown("# 综述")            // "<h1>综述</h1>"
 * renderMarkdown("<script>x</script>") // ""（脚本被清除）
 */
export function renderMarkdown(source: string | null | undefined): string {
  const text = (source ?? "").trim();
  if (!text) {
    return "";
  }

  const rawHtml = marked.parse(text, { async: false }) as string;

  // 环境不支持可靠消毒 → 降级成转义纯文本。
  // 为什么不用 marked 的输出直接返回：那是**未消毒的 HTML**，会开 XSS 口子。
  if (!domPurifyUsable()) {
    return `<pre class="md-fallback">${escapeHtml(text)}</pre>`;
  }

  const clean = DOMPurify.sanitize(rawHtml, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
    FORBID_TAGS: ["script", "style", "iframe", "object", "embed", "form", "input"],
    FORBID_ATTR: ["onerror", "onclick", "onload", "onmouseover", "style"],
  });

  // 外链统一新窗口 + 断开 opener（防止 window.opener 劫持）
  return clean.replace(
    /<a\s+([^>]*?)href="(https?:\/\/[^"]+)"/g,
    '<a $1href="$2" target="_blank" rel="noopener noreferrer"',
  );
}

/** 供测试断言环境能力（避免测试依赖具体 DOM 实现）。 */
export const markdownCapabilities = {
  get sanitizerUsable(): boolean {
    return domPurifyUsable();
  },
};
