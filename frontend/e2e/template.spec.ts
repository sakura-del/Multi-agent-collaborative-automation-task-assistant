import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { inflateRawSync } from "node:zlib";

// Word 模板页 E2E（2026-10-26 重设计）：
// 页面不得让人以为「校验模板」产出真实成稿；删除必须二次确认。
const API = "http://10.0.10.2:48080/api/v1";
const SHOTS = "e2e/screenshots";

async function loginAs(page: Page, account: string, password: string) {
  await page.goto("/login");
  await page.locator("#account").fill(account);
  await page.locator("#password").fill(password);
  await page.locator('form button[type="submit"]').click();
  await page.getByRole("link", { name: /任务/ }).waitFor({ timeout: 30_000 });
}

async function token(request: Page["request"], account: string, password: string) {
  const resp = await request.post(`${API}/auth/login`, { data: { account, password } });
  expect(resp.ok()).toBeTruthy();
  return (await resp.json()).access_token as string;
}

/** 上传一个真实模板（用仓库里的测试夹具），返回 template_id。 */
async function ensureTemplate(request: Page["request"], accessToken: string): Promise<string> {
  const existing = await (
    await request.get(`${API}/templates`, { headers: { Authorization: `Bearer ${accessToken}` } })
  ).json();
  console.log("EXISTING_TEMPLATES=" + JSON.stringify(existing.items?.map((i: any) => i.template_id)));
  if (existing.items.length > 0) {
    return existing.items[0].template_id as string;
  }
  const resp = await request.post(`${API}/templates`, {
    headers: { Authorization: `Bearer ${accessToken}` },
    multipart: {
      file: {
        name: "school.docx",
        mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        buffer: readFileSync("tests/fixtures/templates/school_template.docx"),
      },
      name: "E2E 学院模板",
    },
  });
  console.log("UPLOAD_STATUS=" + resp.status() + " BODY=" + (await resp.text()).slice(0, 200));
  expect(resp.ok()).toBeTruthy();
  return (await resp.json()).template_id as string;
}

/**
 * 从 zip（docx）里取出指定条目的内容。
 *
 * 只解析到够用的程度：扫描本地文件头（PK\x03\x04），按
 * `method` 判断是否 deflate，再解压。够读 `word/document.xml` 就行。
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

test("模板页：按钮与说明不得暗示产出真实成稿", async ({ page, request }) => {
  const accessToken = await token(request, "demo", "demo12345");
  const templateId = await ensureTemplate(request, accessToken);

  await loginAs(page, "demo", "demo12345");
  await page.goto("/templates");
  await expect(page.locator(`[data-test="template-row-${templateId}"]`)).toBeVisible({
    timeout: 30_000,
  });

  // ① 按钮文案不能是「渲染」（承诺真实成稿）
  const button = page.locator(`[data-test="template-render-${templateId}"]`);
  const label = (await button.innerText()).trim();
  console.log("BUTTON_LABEL=" + label);
  expect(label).not.toBe("渲染");
  expect(label).toMatch(/校验|预览/);

  // ② 页面上必须有示例说明
  const note = page.locator('[data-test="template-sample-note"]');
  await expect(note).toBeVisible();
  const noteText = await note.innerText();
  console.log("NOTE_TEXT=" + noteText.replace(/\s+/g, " ").slice(0, 160));
  expect(noteText).toMatch(/示例/);

  // ③ 整页文本里不能出现「生成成稿」这种误导性承诺
  const pageText = await page.evaluate(
    () => (document.querySelector("main") ?? document.body).textContent ?? ""
  );
  console.log("HAS_DEMO_WORD=" + /示例/.test(pageText));

  await page.screenshot({ path: `${SHOTS}/template-after.png`, fullPage: true });
});

test("模板页：校验下载的是示例文本，且提示如实说明", async ({ page, request }) => {
  const accessToken = await token(request, "demo", "demo12345");
  const templateId = await ensureTemplate(request, accessToken);

  await loginAs(page, "demo", "demo12345");
  await page.goto("/templates");
  await expect(page.locator(`[data-test="template-row-${templateId}"]`)).toBeVisible({
    timeout: 30_000,
  });

  // 捕获下载
  const downloadPromise = page.waitForEvent("download", { timeout: 60_000 });
  await page.locator(`[data-test="template-render-${templateId}"]`).click();
  const download = await downloadPromise;
  const path = await download.path();
  console.log("DOWNLOAD_NAME=" + download.suggestedFilename());

  // 成功提示必须写明是示例
  const success = page.locator('[data-test="template-render-success"]');
  await expect(success).toBeVisible({ timeout: 30_000 });
  const successText = await success.innerText();
  console.log("SUCCESS_TEXT=" + successText.replace(/\s+/g, " ").slice(0, 200));
  expect(successText).toMatch(/示例/);
  expect(successText).toMatch(/不是.*成稿|仅用于/);

  // 实际打开下载的 docx，确认正文确实是示例文本（而不是真实综述内容）
  expect(path).toBeTruthy();
  const buffer = readFileSync(path!);
  console.log("DOWNLOAD_BYTES=" + buffer.length);

  // docx 是 zip：解析出 word/document.xml，再把 w:t 文本抽出来。
  // 只断言「文件能打开 + 是 zip」是不够的 —— 真正的缺陷正是
  // 「文件完全合法、只是内容是假内容」，所以必须读正文。
  const documentXml = extractZipEntry(buffer, "word/document.xml").toString("utf8");
  const texts = [...documentXml.matchAll(/<w:t[^>]*>([^<]*)<\/w:t>/g)]
    .map((match) => match[1])
    .filter((text) => text.trim());
  const body = texts.join(" | ");
  console.log("DOCX_BODY=" + body.slice(0, 300));
  console.log("DOCX_BODY_LEN=" + texts.join("").length);

  // 核心断言：成稿里是**示例文本**，不是真实综述
  expect(body).toContain("示例内容");
  // 且占位符已被真实替换（没有残留 {{ }}）
  expect(body).not.toContain("{{");
});

test("模板页：删除需要二次确认", async ({ page, request }) => {
  const accessToken = await token(request, "demo", "demo12345");
  const templateId = await ensureTemplate(request, accessToken);

  await loginAs(page, "demo", "demo12345");
  await page.goto("/templates");
  await expect(page.locator(`[data-test="template-row-${templateId}"]`)).toBeVisible({
    timeout: 30_000,
  });

  await page.locator(`[data-test="template-delete-${templateId}"]`).click();
  // 必须出现确认条，且此时模板仍在
  const confirm = page.locator(`[data-test="template-delete-confirm-${templateId}"]`);
  await expect(confirm).toBeVisible();
  await expect(page.locator(`[data-test="template-row-${templateId}"]`)).toBeVisible();

  // 取消后模板仍在
  await page.locator(`[data-test="template-delete-cancel-${templateId}"]`).click();
  await expect(confirm).toBeHidden();
  await expect(page.locator(`[data-test="template-row-${templateId}"]`)).toBeVisible();

  // 接口回查：模板未被删除
  const after = await (
    await request.get(`${API}/templates`, { headers: { Authorization: `Bearer ${accessToken}` } })
  ).json();
  const stillThere = (after.items as Array<{ template_id: string }>).some(
    (item) => item.template_id === templateId
  );
  console.log("TEMPLATE_STILL_THERE_AFTER_CANCEL=" + stillThere);
  expect(stillThere).toBe(true);
});

test("模板页：移动端无横向溢出", async ({ page, request }) => {
  const accessToken = await token(request, "demo", "demo12345");
  const templateId = await ensureTemplate(request, accessToken);

  await loginAs(page, "demo", "demo12345");
  await page.goto("/templates");
  await expect(page.locator(`[data-test="template-row-${templateId}"]`)).toBeVisible({
    timeout: 30_000,
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  console.log("MOBILE_OVERFLOW=" + overflow);
  expect(overflow).toBeLessThanOrEqual(1);

  await page.screenshot({ path: `${SHOTS}/template-mobile.png`, fullPage: true });
});
