import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { inflateRawSync } from "node:zlib";
import { apiBase, demoCreds } from "./support/env";
import { fetchAuthToken, seedAuthByApi } from "./support/auth";
import { measureContrastTargets } from "./support/contrast";

// Word 模板页 E2E（2026-10-26 重设计）。
const API = apiBase();
const SHOTS = "e2e/screenshots";

/**
 * 仓库里的真实测试模板（后端测试夹具）。
 */
const TEMPLATE_FIXTURE = "../backend/tests/fixtures/templates/school_template.docx";

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
  // ===== 准备：复用 API 登录，确保有模板 =====
  const demo = demoCreds();
  const accessToken = (await fetchAuthToken(request, demo.account, demo.password)).access_token;
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

  // ===== 进入模板页 =====
  await seedAuthByApi(page, request, demo.account, demo.password, "/templates");
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

  const contrastResults = await measureContrastTargets(page, [
    { label: "template-sample-note", selector: '[data-test="template-sample-note"]' },
  ]);
  const contrast = contrastResults[0]!;
  console.log("NOTE_CONTRAST=" + JSON.stringify(contrast));
  expect(contrast.ratio).toBeGreaterThanOrEqual(contrast.threshold);

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
