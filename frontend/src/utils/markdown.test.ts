import { describe, expect, it } from "vitest";

import { markdownCapabilities, renderMarkdown } from "@/utils/markdown";

/**
 * 综述正文的 Markdown 渲染（2026-10-23 任务详情页重设计）。
 *
 * ## 为什么需要
 *
 * 结果页原先用 `<pre>{{ final_markdown }}</pre>` 纯文本渲染 —— 注释写的原因是
 * 「不用 v-html」（安全考虑）。但用户看到的是一篇**带井号和星号的综述**：
 *
 * ```
 * # 检索增强生成评估方法综述
 * ## 摘要
 * **[E1] Ragas 提出...**
 * ```
 *
 * 这是核心产出页，可读性影响最大。
 *
 * ## 安全底线（最重要）
 *
 * 综述正文 = LLM 输出 + 用户主题 + 文献元数据 → **必须视为不可信输入**。
 * 只测「标题变成 `<h1>`」而不测 XSS，等于给注入留后门。
 *
 * ## 环境差异（实测踩到，必须显式处理）
 *
 * `dompurify` 需要真实 DOM：
 *
 * | 环境 | `isSupported` | 行为 |
 * | --- | --- | --- |
 * | 浏览器 | true | 正常消毒 |
 * | 纯 Node | false | 实例不可用 |
 * | happy-dom（vitest 默认） | **true 但剥标签** | 静默把 `<h1>` 变纯文本 |
 *
 * 第三种最危险（看起来成功、其实渲染坏了），所以实现里做了能力探测，
 * 探测不过就走「转义纯文本」的保守降级。本文件同时验证**两条路径**。
 */

const sanitizerReady = markdownCapabilities.sanitizerUsable;

describe("renderMarkdown：输入边界（与环境无关）", () => {
  it("空值安全返回空串", () => {
    expect(renderMarkdown("")).toBe("");
    expect(renderMarkdown("   ")).toBe("");
    expect(renderMarkdown(null)).toBe("");
    expect(renderMarkdown(undefined)).toBe("");
  });

  it("纯文本内容不丢失", () => {
    expect(renderMarkdown("这是一段没有标记的纯文本。")).toContain(
      "这是一段没有标记的纯文本。",
    );
  });

  it("引用标记 [E1] 是业务语义，不能被吃掉", () => {
    const html = renderMarkdown("该结论有证据支持 [E1][E3]。");
    expect(html).toContain("[E1]");
    expect(html).toContain("[E3]");
  });
});

/**
 * XSS 消毒：**无论走哪条路径都必须通过**。
 *
 * - 消毒器可用 → 走 DOMPurify，危险标签被剥但正文保留；
 * - 消毒器不可用 → 走转义纯文本，危险内容变成字面量（不可执行）。
 *
 * 两条路径的共同断言：**输出里不能有可执行的注入**。
 */
describe("renderMarkdown：XSS 消毒（核心安全底线）", () => {
  it("清除 <script>（不能出现可执行 script 标签）", () => {
    const html = renderMarkdown('正常文字<script>alert("xss")</script>');
    expect(html).not.toMatch(/<script[\s>]/i);
    expect(html).toContain("正常文字");
  });

  it("onerror 不能成为可执行的属性", () => {
    // 注意断言写法：降级路径会把整段转义成文本，`onerror=` 仍作为**字面量**
    // 出现在 &quot; 之间 —— 那是无害的。真正的判据是「它不能出现在真标签里」：
    // 要么没有 <img，要么 <img 上不带 onerror= 属性。
    const html = renderMarkdown('<img src="x" onerror="alert(1)">');
    const hasLiveImg = /<img[^>]*onerror\s*=/i.test(html);
    expect(hasLiveImg).toBe(false);
  });

  it("onclick 不能成为可执行的属性", () => {
    const html = renderMarkdown('<div onclick="steal()">点我</div>');
    expect(/<div[^>]*onclick\s*=/i.test(html)).toBe(false);
  });

  it("javascript: 协议不能出现在 href 里", () => {
    const html = renderMarkdown("[点我](javascript:alert(1))");
    expect(/href\s*=\s*["']?\s*javascript:/i.test(html)).toBe(false);
  });

  it("清除 <iframe>", () => {
    const html = renderMarkdown('<iframe src="https://evil.example"></iframe>');
    expect(html).not.toMatch(/<iframe[\s>]/i);
  });

  it("清除 <object> / <embed>", () => {
    expect(renderMarkdown('<object data="x"></object>')).not.toMatch(/<object[\s>]/i);
    expect(renderMarkdown('<embed src="x">')).not.toMatch(/<embed[\s>]/i);
  });

  it("清除 <style>（可用于覆盖 UI 钓鱼）", () => {
    const html = renderMarkdown("<style>body{display:none}</style>正文");
    expect(html).not.toMatch(/<style[\s>]/i);
  });

  it("注释型注入不能逃逸出 script", () => {
    const html = renderMarkdown("<!-- <script>alert(1)</script> -->正文");
    expect(html).not.toMatch(/<script[\s>]/i);
  });
});

/**
 * 排版渲染：仅在消毒器**真正可用**时断言（happy-dom 下会走降级路径）。
 */
describe.skipIf(!sanitizerReady)("renderMarkdown：排版渲染（需可用消毒器）", () => {
  it("标题渲染成 h 标签", () => {
    const html = renderMarkdown("# 综述标题");
    expect(html).toContain("<h1");
    expect(html).toContain("综述标题");
  });

  it("二三级标题", () => {
    expect(renderMarkdown("## 摘要")).toContain("<h2");
    expect(renderMarkdown("### 小节")).toContain("<h3");
  });

  it("粗体渲染成 strong", () => {
    const html = renderMarkdown("**Ragas: Automated Evaluation**");
    expect(html).toContain("<strong");
  });

  it("列表渲染成 ul/ol", () => {
    expect(renderMarkdown("- 甲\n- 乙")).toContain("<ul");
    expect(renderMarkdown("1. 甲\n2. 乙")).toContain("<ol");
  });

  it("段落分段", () => {
    expect(renderMarkdown("第一段\n\n第二段")).toContain("<p");
  });

  it("正文尖括号被转义而不是当标签", () => {
    expect(renderMarkdown("证明：a < b 且 b > c")).not.toContain("<b 且");
  });
});

/**
 * 降级路径：消毒器不可用时，必须输出**转义纯文本**而不是未消毒 HTML。
 */
describe.skipIf(sanitizerReady)("renderMarkdown：保守降级（消毒器不可用）", () => {
  it("降级输出仍然保留原文内容", () => {
    expect(renderMarkdown("# 综述标题")).toContain("综述标题");
  });

  it("降级输出做了 HTML 转义（尖括号不成为标签）", () => {
    const html = renderMarkdown("<script>alert(1)</script>");
    expect(html).not.toMatch(/<script[\s>]/i);
    expect(html).toContain("&lt;script");
  });
});
