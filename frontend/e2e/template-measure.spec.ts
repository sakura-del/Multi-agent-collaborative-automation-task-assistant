import { expect, test, type Page } from "@playwright/test";

// Word 模板页「修复后量数值」验收（AGENTS §6.5.7）：与修复前取证逐项对照。
const API = "http://10.0.10.2:48080/api/v1";

async function loginAs(page: Page, account: string, password: string) {
  await page.goto("/login");
  await page.locator("#account").fill(account);
  await page.locator("#password").fill(password);
  await page.locator('form button[type="submit"]').click();
  await page.getByRole("link", { name: /任务/ }).waitFor({ timeout: 30_000 });
}

test("模板页修复后：文案/说明/几何/对比度", async ({ page, request }) => {
  const loginResp = await request.post(`${API}/auth/login`, {
    data: { account: "demo", password: "demo12345" },
  });
  const token = (await loginResp.json()).access_token as string;
  const templates = await (
    await request.get(`${API}/templates`, { headers: { Authorization: `Bearer ${token}` } })
  ).json();
  const templateId = templates.items[0].template_id as string;

  await loginAs(page, "demo", "demo12345");
  await page.goto("/templates");
  await expect(page.locator(`[data-test="template-row-${templateId}"]`)).toBeVisible({
    timeout: 30_000,
  });

  // ① 全部可见文本（与修复前对照）
  const text = await page.evaluate(() => {
    const out: string[] = [];
    const walk = (node: Node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        const t = (node.textContent ?? "").trim();
        if (t) out.push(t);
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      const el = node as HTMLElement;
      const s = getComputedStyle(el);
      if (s.display === "none" || s.visibility === "hidden") return;
      node.childNodes.forEach(walk);
    };
    walk(document.querySelector("main") ?? document.body);
    return out;
  });
  console.log("AFTER_VISIBLE_TEXT=" + JSON.stringify(text));

  // ② 关键文案
  const buttonLabel = (await page.locator(`[data-test="template-render-${templateId}"]`).innerText()).trim();
  console.log("AFTER_BUTTON_LABEL=" + buttonLabel);

  const noteText = (await page.locator('[data-test="template-sample-note"]').innerText())
    .replace(/\s+/g, " ")
    .trim();
  console.log("AFTER_NOTE_LEN=" + noteText.length);
  console.log("AFTER_NOTE=" + noteText.slice(0, 200));

  const bodyText = text.join(" ");
  console.log("AFTER_HAS_DEMO_WORD=" + /示例/.test(bodyText));
  console.log("AFTER_HAS_GENERATE_CLAIM=" + /生成成稿/.test(bodyText));

  // ③ 几何：说明条是否真的占版面
  const noteGeometry = await page.locator('[data-test="template-sample-note"]').evaluate((el) => {
    const r = (el as HTMLElement).getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height) };
  });
  console.log("AFTER_NOTE_GEOMETRY=" + JSON.stringify(noteGeometry));

  // ④ 对比度（说明条文字 vs 底色）
  const contrast = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 1;
    canvas.height = 1;
    const ctx = canvas.getContext("2d")!;
    const toRgb = (c: string): [number, number, number] => {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = "#000";
      ctx.fillStyle = c;
      ctx.fillRect(0, 0, 1, 1);
      const d = ctx.getImageData(0, 0, 1, 1).data;
      return [d[0], d[1], d[2]];
    };
    const lum = (rgb: [number, number, number]) => {
      const f = (x: number) => {
        const s = x / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
    };
    const ratio = (fg: string, bg: string) => {
      const l1 = lum(toRgb(fg));
      const l2 = lum(toRgb(bg));
      const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
      return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100;
    };
    const note = document.querySelector('[data-test="template-sample-note"]') as HTMLElement;
    const noteStyle = getComputedStyle(note);
    return {
      color: noteStyle.color,
      bg: noteStyle.backgroundColor,
      fontSize: noteStyle.fontSize,
      ratio: ratio(noteStyle.color, noteStyle.backgroundColor),
    };
  });
  console.log("AFTER_CONTRAST=" + JSON.stringify(contrast));

  // ⑤ 删除确认是否可键盘触达（图标按钮必须有 aria-label）
  const deleteBtn = page.locator(`[data-test="template-delete-${templateId}"]`);
  console.log("AFTER_DELETE_ARIA=" + (await deleteBtn.getAttribute("aria-label")));

  // ⑥ 溢出
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  console.log("AFTER_DESKTOP_OVERFLOW=" + overflow);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  const mobileOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  console.log("AFTER_MOBILE_OVERFLOW=" + mobileOverflow);

  await page.screenshot({ path: "e2e/screenshots/template-after-mobile.png", fullPage: true });

  // 硬断言
  expect(/示例/.test(bodyText)).toBe(true);
  expect(/生成成稿/.test(bodyText)).toBe(false);
  expect(overflow).toBeLessThanOrEqual(1);
  expect(mobileOverflow).toBeLessThanOrEqual(1);
  expect(contrast.ratio).toBeGreaterThanOrEqual(4.5);
  expect(noteGeometry.h).toBeGreaterThan(20);
});
