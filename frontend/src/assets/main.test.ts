import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const mainCssPath = path.resolve(__dirname, "./main.css");
const mainCss = fs.readFileSync(mainCssPath, "utf-8");

describe("assets/main.css 全局 Token 与 GPU 动画规则", () => {
  it("浅色 :root、深色 .dark 与 @theme inline 完整包含 --chart-1..6 及语义色", () => {
    const rootBlock = mainCss.match(/:root\s*\{[\s\S]*?\n\}/)?.[0] ?? "";
    const darkBlock = mainCss.match(/\.dark\s*\{[\s\S]*?\n\}/)?.[0] ?? "";
    const themeBlock = mainCss.match(/@theme inline\s*\{[\s\S]*?\n\}/)?.[0] ?? "";

    for (const n of [1, 2, 3, 4, 5, 6]) {
      expect(rootBlock).toMatch(new RegExp(`--chart-${n}:\\s*oklch\\(`));
      expect(darkBlock).toMatch(new RegExp(`--chart-${n}:\\s*oklch\\(`));
      expect(themeBlock).toContain(`--color-chart-${n}: var(--chart-${n});`);
    }

    expect(rootBlock).toContain("--success:");
    expect(rootBlock).toContain("--warning:");
    expect(darkBlock).toContain("--success:");
    expect(darkBlock).toContain("--warning:");
  });

  it("在 @layer components 中收拢 .chat-markdown 并移除 :deep() 与无效 hsl(var(...))", () => {
    expect(mainCss).toContain(".chat-markdown");
    expect(mainCss).toContain(".chat-markdown--user");
    expect(mainCss).not.toContain(":deep(");
    expect(mainCss).not.toContain("hsl(var(");
  });

  it("在 @layer components 中包含 GPU 合成层 .funnel-particles 动画且无布局重排属性", () => {
    expect(mainCss).toContain(".funnel-particles");
    expect(mainCss).toContain("container-type: size;");
    expect(mainCss).toContain(".funnel-particle");
    expect(mainCss).toContain(".funnel-particle.exit-0::after");
    expect(mainCss).toContain("translate3d(");

    const keyframesMatches = mainCss.match(/@keyframes\s+funnel-[\s\S]*?\n\s*\}/g) ?? [];
    expect(keyframesMatches.length).toBeGreaterThanOrEqual(4);
    for (const block of keyframesMatches) {
      expect(block).not.toMatch(/\b(top|left|right|bottom|width|height|background):/);
    }
  });

  it("保留 prefers-reduced-motion 全局与粒子兜底降级", () => {
    const reduceIdx = mainCss.indexOf("@media (prefers-reduced-motion: reduce)");
    expect(reduceIdx).toBeGreaterThan(-1);
    expect(mainCss).toMatch(/\.funnel-particles\s*\{\s*display:\s*none;/);
  });
});
