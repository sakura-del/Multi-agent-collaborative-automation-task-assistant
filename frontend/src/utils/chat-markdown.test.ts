import DOMPurify from "dompurify";
import { afterEach, describe, expect, it, vi } from "vitest";

const { mermaidInitializeMock, mermaidRenderMock } = vi.hoisted(() => ({
  mermaidInitializeMock: vi.fn(),
  mermaidRenderMock: vi.fn(),
}));

vi.mock("mermaid", () => ({
  default: {
    initialize: mermaidInitializeMock,
    render: mermaidRenderMock,
  },
}));

import { renderMermaidBlocks } from "@/utils/chat-markdown";

describe("chat-markdown Mermaid 消毒加固（W1）", () => {
  afterEach(() => {
    mermaidInitializeMock.mockReset();
    mermaidRenderMock.mockReset();
    vi.restoreAllMocks();
  });

  it("renderMermaidBlocks 通过 DOMPurify svg profile 净化 SVG 并保留 <text><tspan> 内容", async () => {
    const sanitizeSpy = vi.spyOn(DOMPurify, "sanitize");
    mermaidRenderMock.mockResolvedValueOnce({
      svg: [
        '<svg viewBox="0 0 200 100">',
        '<g><rect width="120" height="40"></rect><text x="10" y="20"><tspan>识别 n=50</tspan></text></g>',
        "</svg>",
      ].join(""),
    });

    const root = document.createElement("div");
    root.innerHTML = '<pre><code class="language-mermaid">flowchart TD\n  A--&gt;B</code></pre>';
    await renderMermaidBlocks(root);

    expect(mermaidInitializeMock).toHaveBeenCalledWith(
      expect.objectContaining({
        securityLevel: "strict",
        htmlLabels: false,
        flowchart: { htmlLabels: false },
      }),
    );
    expect(sanitizeSpy).toHaveBeenCalledWith(
      expect.stringContaining("<tspan>识别 n=50</tspan>"),
      { USE_PROFILES: { svg: true, svgFilters: true } },
    );
    const figure = root.querySelector(".mermaid-figure");
    expect(figure).not.toBeNull();
    expect(figure!.innerHTML).toContain("<text");
    expect(figure!.innerHTML).toContain("<tspan>");
    expect(figure!.textContent).toContain("识别 n=50");
  });

  it("renderMermaidBlocks 清除 Mermaid 输出中潜在的 script/img/事件属性 XSS 载荷", async () => {
    mermaidRenderMock.mockResolvedValueOnce({
      svg: [
        '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)" viewBox="0 0 100 100">',
        "<script>alert(2)</script>",
        '<foreignObject><img src="x" onerror="alert(3)" /></foreignObject>',
        '<text x="10" y="20">安全节点</text>',
        "</svg>",
      ].join(""),
    });

    const root = document.createElement("div");
    root.innerHTML = '<pre><code class="language-mermaid">flowchart TD\n  A--&gt;B</code></pre>';
    await renderMermaidBlocks(root);

    const figure = root.querySelector(".mermaid-figure");
    expect(figure).not.toBeNull();
    const html = figure!.innerHTML;
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("onload");
    expect(html).not.toContain("onerror");
    expect(html).toContain("安全节点");
  });
});
