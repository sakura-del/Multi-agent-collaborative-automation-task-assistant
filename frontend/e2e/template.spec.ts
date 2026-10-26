import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { inflateRawSync } from "node:zlib";

// Word 模板页 E2E（2026-10-26 重设计）。
//
// ## 为什么整个文件只有一条 test
//
// `auth_login` 限流是 **5 次 / 3600s**（AGENTS §2）。此前拆成 4 条 test，
// 每条各做一次 API 登录 + 一次页面登录 = 8 次 → 必然 429，
// 表现为「登录页超时」，**看起来像页面缺陷但完全无关**（已实测踩到）。
//
// 合并成一条：1 次 API 登录 + 1 次页面登录，一次页面访问跑完所有断言。
// 这也与 `style.spec.ts` / `plan-measure.spec.ts` 的取证模式一致。
const API = "http://10.0.10.2:48080/api/v1";
const SHOTS = "e2e/screenshots";

/**
 * 仓库里的真实测试模板（后端测试夹具）。
 *
 * 路径相对 `frontend/`（Playwright 的 cwd），所以要先 `../backend/`。
 * 用真实模板而不是现造一个：占位符与样式映射都是真的，
 * 这样「校验」走的路径与用户上传的完全一致。
 */
const TEMPLATE_FIXTURE = "../backend/tests/fixtures/templates/school_template.docx";

async function loginAs(page: Page, account: string, password: string) {
  await page.goto("/login");
  await page.locator("#account").fill(account);
  await page.locator("#password").fill(password);
  await page.locator('form button[type="submit"]').click();
  await page.getByRole("link", { name: /任务/ }).waitFor({ timeout: 30_000 });
}

/**
 * 从 zip（docx）里取出指定条目的内容。
 *
 * 只解析到够用的程度：扫描本地文件头（PK\x03\x04），按 `method` 判断是否
 * deflate，再解压。够读 `word/document.xml` 就行。
 */
function extractZipEntry(zip: Buffer, entryName: string): Buffer {
  let offset = 0;
  while (offset < zip.length - 4) {
    if (zip.readUInt32LE(offset) !== 0x04034b50) {
      offset += 1;
      continue;
    }
    const method = zip.readUInt16LE(offset + 8);
    const compressedSize = zip.readUInt32LE(offset + 18);
    const nameLength = zip.readUInt16LE(offset + 26);
    const extraLength = zip.readUInt16LE(offset + 28);
    const nameStart = offset + 30;
    const name = zip.subarray(nameStart, nameStart + nameLength).toString("utf8");
    const dataStart = nameStart + nameLength + extraLength;
    if (name === entryName) {
      const data = zip.subarray(dataStart, dataStart + compressedSize);
      return method === 0 ? data : inflateRawSync(data);
    }
    offset = dataStart + compressedSize;
  }
  throw new Error(`zip 里没有 ${entryName}`);
}

test("模板页：校验语义如实、占位符可读、删除需确认、移动端无溢出", async ({
  page,
  request,
}) => {
  // ===== 准备：一次 API 登录，确保有模板 =====
  const loginResp = await request.post(`${API}/auth/login`, {
    data: { account: "demo", password: "demo12345" },
  });
  expect(loginResp.ok(), "auth_login 限流 5 次/3600s，超了要等窗口").toBeTruthy();
  const accessToken = (await loginResp.json()).access_token as string;
  const auth = { Authorization: `Bearer ${accessToken}` };

  const listTemplates = async () =>
    (await request.get(`${API}/templates`, { headers: auth })).json();

  let existing = await listTemplates();
  if (existing.items.length === 0) {
    const upload = await request.post(`${API}/templates`, {
      headers: auth,
      multipart: {
        file: {
          name: "school.docx",
          mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          buffer: readFileSync(TEMPLATE_FIXTURE),
        },
        name: "E2E 学院模板",
      },
    });
    expect(upload.ok()).toBeTruthy();
    existing = await listTemplates();
  }
  const templateId = existing.items[0].template_id as string;
  console.log("TEMPLATE_ID=" + templateId);

  // ===== 一次页面登录，跑完所有页面断言 =====
  await loginAs(page, "demo", "demo12345");
  await page.goto("/templates");
  await expect(page.locator(`[data-test="template-row-${templateId}"]`)).toBeVisible({
    timeout: 30_000,
  });

  // ---------- ① 按钮与说明不得暗示产出真实成稿 ----------
  const button = page.locator(`[data-test="template-render-${templateId}"]`);
  const buttonLabel = (await button.innerText()).trim();
  console.log("BUTTON_LABEL=" + buttonLabel);
  expect(buttonLabel).not.toBe("渲染");
  expect(buttonLabel).toMatch(/校验|预览/);

  const note = page.locator('[data-test="template-sample-note"]');
  await expect(note).toBeVisible();
  const noteText = (await note.innerText()).replace(/\s+/g, " ").trim();
  console.log("NOTE_TEXT=" + noteText.slice(0, 160));
  expect(noteText).toMatch(/示例/);

  const pageText = await page.evaluate(
    () => (document.querySelector("main") ?? document.body).textContent ?? ""
  );
  console.log("HAS_DEMO_WORD=" + /示例/.test(pageText));
  console.log("HAS_GENERATE_CLAIM=" + /生成成稿/.test(pageText));
  expect(/示例/.test(pageText)).toBe(true);
  // 修复前页面写着「已用模板生成成稿并开始下载」——这类承诺必须消失
  expect(/生成成稿/.test(pageText)).toBe(false);

  // ---------- ② 占位符可读（优先 label，无 label 退回 key） ----------
  const chips = await page
    .locator(`[data-test="template-row-${templateId}"] code`)
    .evaluateAll((nodes) => nodes.map((n) => (n.textContent ?? "").trim()));
  console.log("PLACEHOLDER_CHIPS=" + JSON.stringify(chips.slice(0, 10)));
  expect(chips.length).toBeGreaterThan(0);

  // ---------- ③ 几何与对比度 ----------
  const noteGeometry = await note.evaluate((el) => {
    const r = (el as HTMLElement).getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height) };
  });
  console.log("NOTE_GEOMETRY=" + JSON.stringify(noteGeometry));
  expect(noteGeometry.h).toBeGreaterThan(20);

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
    const el = document.querySelector('[data-test="template-sample-note"]') as HTMLElement;
    const s = getComputedStyle(el);
    const l1 = lum(toRgb(s.color));
    const l2 = lum(toRgb(s.backgroundColor));
    const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
    return {
      color: s.color,
      bg: s.backgroundColor,
      fontSize: s.fontSize,
      ratio: Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100,
    };
  });
  console.log("NOTE_CONTRAST=" + JSON.stringify(contrast));
  expect(contrast.ratio).toBeGreaterThanOrEqual(4.5);

  const deleteAria = await page
    .locator(`[data-test="template-delete-${templateId}"]`)
    .getAttribute("aria-label");
  console.log("DELETE_ARIA=" + deleteAria);
  expect(deleteAria).toBeTruthy();

  await page.screenshot({ path: `${SHOTS}/template-after.png`, fullPage: true });

  // ---------- ④ 校验下载：解包 docx 确认正文是示例文本 ----------
  const downloadPromise = page.waitForEvent("download", { timeout: 60_000 });
  await button.click();
  const download = await downloadPromise;
  console.log("DOWNLOAD_NAME=" + download.suggestedFilename());
  const path = await download.path();
  expect(path).toBeTruthy();

  const success = page.locator('[data-test="template-render-success"]');
  await expect(success).toBeVisible({ timeout: 30_000 });
  const successText = (await success.innerText()).replace(/\s+/g, " ");
  console.log("SUCCESS_TEXT=" + successText.slice(0, 200));
  expect(successText).toMatch(/示例/);
  expect(successText).toMatch(/不是.*成稿|仅用于/);

  const buffer = readFileSync(path!);
  const documentXml = extractZipEntry(buffer, "word/document.xml").toString("utf8");
  const texts = [...documentXml.matchAll(/<w:t[^>]*>([^<]*)<\/w:t>/g)]
    .map((m) => m[1])
    .filter((t) => t.trim());
  const body = texts.join(" | ");
  console.log("DOCX_BYTES=" + buffer.length);
  console.log("DOCX_BODY_LEN=" + texts.join("").length);
  console.log("DOCX_BODY=" + body.slice(0, 260));
  // 成稿里是**示例文本**（这是设计），且占位符已被真实替换
  expect(body).toContain("示例内容");
  expect(body).not.toContain("{{");

  // ---------- ⑤ 删除二次确认 ----------
  await page.locator(`[data-test="template-delete-${templateId}"]`).click();
  const confirm = page.locator(`[data-test="template-delete-confirm-${templateId}"]`);
  await expect(confirm).toBeVisible();
  await expect(page.locator(`[data-test="template-row-${templateId}"]`)).toBeVisible();

  await page.locator(`[data-test="template-delete-cancel-${templateId}"]`).click();
  await expect(confirm).toBeHidden();
  await expect(page.locator(`[data-test="template-row-${templateId}"]`)).toBeVisible();

  // 接口回查：取消后模板确实还在
  const after = await listTemplates();
  const stillThere = (after.items as Array<{ template_id: string }>).some(
    (item) => item.template_id === templateId
  );
  console.log("TEMPLATE_STILL_THERE_AFTER_CANCEL=" + stillThere);
  expect(stillThere).toBe(true);

  // ---------- ⑥ 移动端无横向溢出 ----------
  const desktopOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  const mobileOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  console.log("DESKTOP_OVERFLOW=" + desktopOverflow);
  console.log("MOBILE_OVERFLOW=" + mobileOverflow);
  expect(desktopOverflow).toBeLessThanOrEqual(1);
  expect(mobileOverflow).toBeLessThanOrEqual(1);

  await page.screenshot({ path: `${SHOTS}/template-mobile.png`, fullPage: true });
});
