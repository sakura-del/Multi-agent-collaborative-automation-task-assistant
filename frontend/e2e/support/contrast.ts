import type { Page } from "@playwright/test";

export interface ContrastTarget {
  label: string;
  selector: string;
  /** 若为 UI 图标/边框等非普通文本元素，设为 true（阈值按 3.0 计） */
  isUiComponent?: boolean;
}

export interface ContrastMeasurement {
  label: string;
  selector: string;
  found: boolean;
  visible: boolean;
  fgColor: string;
  resolvedBgHex: string;
  resolvedFgHex: string;
  fontSizePx: number;
  fontWeight: number;
  isLargeText: boolean;
  threshold: number;
  ratio: number;
  passed: boolean;
  textSample: string;
}

async function evaluateContrast(
  page: Page,
  targets: ContrastTarget[],
  sampleAll: boolean,
): Promise<ContrastMeasurement[]> {
  return page.evaluate(
    ({ items, all }) => {
      const canvas = document.createElement("canvas");
      canvas.width = 1;
      canvas.height = 1;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) {
        throw new Error("无法创建 2D Canvas 上下文用于对比度解析");
      }

      const isDark = document.documentElement.classList.contains("dark");
      const fallbackRootHex = isDark ? "#09090b" : "#ffffff";

      function toRgba(cssColor: string): [number, number, number, number] {
        ctx!.clearRect(0, 0, 1, 1);
        ctx!.fillStyle = "rgba(0, 0, 0, 0)";
        ctx!.fillStyle = cssColor;
        ctx!.fillRect(0, 0, 1, 1);
        const data = ctx!.getImageData(0, 0, 1, 1).data;
        return [data[0], data[1], data[2], data[3]];
      }

      function rgbToHex(r: number, g: number, b: number): string {
        const h = (n: number) => n.toString(16).padStart(2, "0");
        return `#${h(r)}${h(g)}${h(b)}`;
      }

      function resolveCompositeBg(el: HTMLElement): [number, number, number] {
        const chain: HTMLElement[] = [];
        let cur: HTMLElement | null = el;
        while (cur) {
          chain.push(cur);
          cur = cur.parentElement;
        }
        chain.reverse(); // 从 html -> body -> ... -> el

        ctx!.clearRect(0, 0, 1, 1);
        ctx!.fillStyle = fallbackRootHex;
        ctx!.fillRect(0, 0, 1, 1);

        for (const node of chain) {
          const bg = getComputedStyle(node).backgroundColor;
          if (!bg || bg === "transparent" || bg === "rgba(0, 0, 0, 0)") {
            continue;
          }
          ctx!.fillStyle = bg;
          ctx!.fillRect(0, 0, 1, 1);
        }

        const data = ctx!.getImageData(0, 0, 1, 1).data;
        return [data[0], data[1], data[2]];
      }

      function resolveCompositeFg(
        fgColor: string,
        bgRgb: [number, number, number],
      ): [number, number, number] {
        ctx!.clearRect(0, 0, 1, 1);
        ctx!.fillStyle = `rgb(${bgRgb[0]}, ${bgRgb[1]}, ${bgRgb[2]})`;
        ctx!.fillRect(0, 0, 1, 1);
        ctx!.fillStyle = fgColor;
        ctx!.fillRect(0, 0, 1, 1);
        const data = ctx!.getImageData(0, 0, 1, 1).data;
        return [data[0], data[1], data[2]];
      }

      function luminance(rgb: [number, number, number]): number {
        const toLin = (c: number) => {
          const s = c / 255;
          return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
        };
        return 0.2126 * toLin(rgb[0]) + 0.7152 * toLin(rgb[1]) + 0.0722 * toLin(rgb[2]);
      }

      function calcRatio(fgRgb: [number, number, number], bgRgb: [number, number, number]): number {
        const l1 = luminance(fgRgb);
        const l2 = luminance(bgRgb);
        const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
        return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100;
      }

      function isElementVisible(el: HTMLElement): boolean {
        const style = getComputedStyle(el);
        if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
          return false;
        }
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      }

      function measureNode(el: HTMLElement, item: ContrastTarget, label: string): ContrastMeasurement {
        const visible = isElementVisible(el);
        const style = getComputedStyle(el);
        const fgColor = style.color;
        const bgRgb = resolveCompositeBg(el);
        const fgRgb = resolveCompositeFg(fgColor, bgRgb);
        const ratio = calcRatio(fgRgb, bgRgb);
        const fontSizePx = parseFloat(style.fontSize) || 14;
        const rawWeight = parseInt(style.fontWeight, 10);
        const fontWeight = Number.isFinite(rawWeight)
          ? rawWeight
          : style.fontWeight === "bold"
            ? 700
            : 400;
        const isLargeText = fontSizePx >= 18 || (fontSizePx >= 14 && fontWeight >= 700);
        const threshold = item.isUiComponent || isLargeText ? 3.0 : 4.5;

        void toRgba(fgColor);

        return {
          label,
          selector: item.selector,
          found: true,
          visible,
          fgColor,
          resolvedBgHex: rgbToHex(bgRgb[0], bgRgb[1], bgRgb[2]),
          resolvedFgHex: rgbToHex(fgRgb[0], fgRgb[1], fgRgb[2]),
          fontSizePx: Math.round(fontSizePx * 10) / 10,
          fontWeight,
          isLargeText,
          threshold,
          ratio,
          passed: !visible || ratio >= threshold,
          textSample: (el.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, 40),
        };
      }

      const results: ContrastMeasurement[] = [];
      for (const item of items) {
        if (all) {
          const nodes = Array.from(document.querySelectorAll(item.selector)) as HTMLElement[];
          if (nodes.length === 0) {
            results.push({
              label: item.label,
              selector: item.selector,
              found: false,
              visible: false,
              fgColor: "",
              resolvedBgHex: "",
              resolvedFgHex: "",
              fontSizePx: 0,
              fontWeight: 400,
              isLargeText: false,
              threshold: item.isUiComponent ? 3.0 : 4.5,
              ratio: 0,
              passed: true,
              textSample: "",
            });
            continue;
          }
          nodes.forEach((node, idx) => {
            const label = nodes.length > 1 ? `${item.label}#${idx + 1}` : item.label;
            results.push(measureNode(node, item, label));
          });
        } else {
          const el = document.querySelector(item.selector) as HTMLElement | null;
          if (!el) {
            results.push({
              label: item.label,
              selector: item.selector,
              found: false,
              visible: false,
              fgColor: "",
              resolvedBgHex: "",
              resolvedFgHex: "",
              fontSizePx: 0,
              fontWeight: 400,
              isLargeText: false,
              threshold: item.isUiComponent ? 3.0 : 4.5,
              ratio: 0,
              passed: true,
              textSample: "",
            });
            continue;
          }
          results.push(measureNode(el, item, item.label));
        }
      }
      return results;
    },
    { items: targets, all: sampleAll },
  );
}

/**
 * 在浏览器页面上下文中测量一组目标元素的 WCAG 2.1 AA 对比度（每个选择器测量首个匹配节点）。
 *
 * 特性：
 * - 通过 1×1 离屏 Canvas 精确解析 `oklch()` / `oklab()` / `color-mix()` 等 CSS Color 4 颜色；
 * - 沿 DOM 祖先链从外到内逐层 Canvas Alpha 合成真实背景色，正确处理 `bg-success/10`、`bg-warning/12` 等半透明底色；
 * - 前景色若带 Alpha 通道，同样叠合于合成背景色之上读取最终像素，避免透明度失真；
 * - 自动识别大字文本（>= 18px 或 >= 14px 且粗体 >= 700）对应 3.0 阈值，普通文本对应 4.5 阈值。
 */
export async function measureContrastTargets(
  page: Page,
  targets: ContrastTarget[],
): Promise<ContrastMeasurement[]> {
  return evaluateContrast(page, targets, false);
}

/**
 * 基于 `querySelectorAll` 全量抽样每个选择器匹配的所有 DOM 节点对比度，防止同构多实例漏测（如第 2 个之后的 StatusBadge、BeAgentPlan 胶囊等）。
 */
export async function measureAllElementsContrast(
  page: Page,
  targets: ContrastTarget[],
): Promise<ContrastMeasurement[]> {
  return evaluateContrast(page, targets, true);
}

/**
 * 在浏览器上下文解析单一 CSS 颜色为 RGBA 四元组（用于代码块背景非透明等校验）。
 */
export async function resolveElementColors(
  page: Page,
  selector: string,
): Promise<{
  found: boolean;
  fgRgba: [number, number, number, number];
  bgRgba: [number, number, number, number];
  compositeBgHex: string;
  compositeFgHex: string;
  ratio: number;
  borderTopWidth: number;
  borderTopRgba: [number, number, number, number];
}> {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel) as HTMLElement | null;
    if (!el) {
      return {
        found: false,
        fgRgba: [0, 0, 0, 0] as [number, number, number, number],
        bgRgba: [0, 0, 0, 0] as [number, number, number, number],
        compositeBgHex: "",
        compositeFgHex: "",
        ratio: 0,
        borderTopWidth: 0,
        borderTopRgba: [0, 0, 0, 0] as [number, number, number, number],
      };
    }

    const canvas = document.createElement("canvas");
    canvas.width = 1;
    canvas.height = 1;
    const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
    const isDark = document.documentElement.classList.contains("dark");
    const fallbackRootHex = isDark ? "#09090b" : "#ffffff";

    const toRgba = (c: string): [number, number, number, number] => {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = "rgba(0, 0, 0, 0)";
      ctx.fillStyle = c;
      ctx.fillRect(0, 0, 1, 1);
      const d = ctx.getImageData(0, 0, 1, 1).data;
      return [d[0], d[1], d[2], d[3]];
    };

    const rgbToHex = (r: number, g: number, b: number) => {
      const h = (n: number) => n.toString(16).padStart(2, "0");
      return `#${h(r)}${h(g)}${h(b)}`;
    };

    const chain: HTMLElement[] = [];
    let cur: HTMLElement | null = el;
    while (cur) {
      chain.push(cur);
      cur = cur.parentElement;
    }
    chain.reverse();

    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = fallbackRootHex;
    ctx.fillRect(0, 0, 1, 1);
    for (const node of chain) {
      const bg = getComputedStyle(node).backgroundColor;
      if (!bg || bg === "transparent" || bg === "rgba(0, 0, 0, 0)") continue;
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, 1, 1);
    }
    const bgData = ctx.getImageData(0, 0, 1, 1).data;
    const bgRgb: [number, number, number] = [bgData[0], bgData[1], bgData[2]];

    const style = getComputedStyle(el);
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = `rgb(${bgRgb[0]}, ${bgRgb[1]}, ${bgRgb[2]})`;
    ctx.fillRect(0, 0, 1, 1);
    ctx.fillStyle = style.color;
    ctx.fillRect(0, 0, 1, 1);
    const fgData = ctx.getImageData(0, 0, 1, 1).data;
    const fgRgb: [number, number, number] = [fgData[0], fgData[1], fgData[2]];

    const lum = (rgb: [number, number, number]) => {
      const f = (x: number) => {
        const s = x / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
    };
    const l1 = lum(fgRgb);
    const l2 = lum(bgRgb);
    const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
    const ratio = Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100;

    return {
      found: true,
      fgRgba: toRgba(style.color),
      bgRgba: toRgba(style.backgroundColor),
      compositeBgHex: rgbToHex(bgRgb[0], bgRgb[1], bgRgb[2]),
      compositeFgHex: rgbToHex(fgRgb[0], fgRgb[1], fgRgb[2]),
      ratio,
      borderTopWidth: parseFloat(style.borderTopWidth) || 0,
      borderTopRgba: toRgba(style.borderTopColor),
    };
  }, selector);
}
