import { expect, test, type Page } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { demoPassword } from "./credentials";

// Phase 1 前端页面验收：Word 模板管理 + 参考文献导入
// 覆盖：登录 → 导航到新页面 → 上传模板（真实后端）→ 看到占位符 → 渲染下载
//       → 粘贴 BibTeX 解析预览 → 中文原样
//
// 运行（对着服务器）：
//   E2E_BASE_URL=http://10.0.10.2:48080 pnpm exec playwright test e2e/phase1-pages.spec.ts

const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "screenshots");
const CREDENTIALS = { account: "demo", password: demoPassword() };

async function login(page: Page) {
  await page.goto("/login");
  // 登录页的账号输入框有「用户名 / 邮箱」标签（id=account）
  await page.locator("#account").fill(CREDENTIALS.account);
  await page.locator("#password").fill(CREDENTIALS.password);
  // 提交按钮文案随模式变化（登录/注册），用 form 的 submit 按钮定位
  await page.locator('form button[type="submit"]').click();

  // ⚠️ 登录端点受限流保护（`rate_limit.rules.auth_login`，默认 10 次/分钟）。
  // 多文件 × 双视口连续跑会命中 429，此时页面停留在登录页、后续导航断言全部失败。
  // 这里显式识别并等待窗口恢复后重试，避免把「测试环境限流」误报成产品缺陷。
  const loggedIn = page.getByRole("link", { name: /任务/ });
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const settled = await Promise.race([
      loggedIn
        .waitFor({ state: "visible", timeout: 10_000 })
        .then(() => "ok" as const)
        .catch(() => null),
      page
        .locator("text=请求过于频繁")
        .waitFor({ state: "visible", timeout: 10_000 })
        .then(() => "limited" as const)
        .catch(() => null),
    ]);
    if (settled === "ok") {
      return;
    }
    // 被限流：等待窗口（默认 60s）后重试
    await page.waitForTimeout(8_000);
    await page.locator('form button[type="submit"]').click().catch(() => undefined);
  }
  await expect(loggedIn).toBeVisible({ timeout: 30_000 });
}

/**
 * 切页后把横向滚动归位。
 *
 * 移动端导航本身可横向滚动（`overflow-x: auto`），点击右侧的「导入文献」会把
 * 导航条滚到最右，导致整页 `scrollX > 0` → 后续「无横向溢出」断言误报。
 * 这是**测试**层面的清理，不是布局缺陷（布局已按窄屏纵向堆叠）。
 */
async function resetScroll(page: Page) {
  await page.evaluate(() => {
    window.scrollTo(0, 0);
    document.documentElement.scrollLeft = 0;
    document.body.scrollLeft = 0;
    document.querySelectorAll<HTMLElement>(".app-nav__links").forEach((element) => {
      element.scrollLeft = 0;
    });
  });
}

/**
 * 构造最小合法 `.docx`（zip 包）。
 *
 * 后端 inspector 要求同时存在 `word/document.xml` 与 `word/styles.xml`，
 * 否则会以「缺少部件」拒绝。这里用 Node 的 zlib 手写一个 STORED（不压缩）zip，
 * 避免引入额外依赖。
 */
function buildMinimalDocx(placeholders: string[]): Buffer {
  const body = placeholders
    .map((key) => `<w:p><w:r><w:t>{{ ${key} }}</w:t></w:r></w:p>`)
    .join("");
  const files: { name: string; content: string }[] = [
    {
      name: "[Content_Types].xml",
      content:
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
        '<Default Extension="xml" ContentType="application/xml"/>' +
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>' +
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>' +
        "</Types>",
    },
    {
      name: "_rels/.rels",
      content:
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>' +
        "</Relationships>",
    },
    {
      name: "word/document.xml",
      content:
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' +
        `<w:body>${body}</w:body></w:document>`,
    },
    {
      name: "word/styles.xml",
      content:
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' +
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>' +
        '<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>' +
        "</w:styles>",
    },
  ];
  return buildZip(files);
}

/** 极简 STORED zip 写入器（仅测试用，够 python-docx/zipfile 读取） */
function buildZip(files: { name: string; content: string }[]): Buffer {
  const chunks: Buffer[] = [];
  const central: Buffer[] = [];
  let offset = 0;

  for (const file of files) {
    const nameBytes = Buffer.from(file.name, "utf-8");
    const data = Buffer.from(file.content, "utf-8");
    const crc = crc32(data);
    const local = Buffer.alloc(30 + nameBytes.length);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4); // version needed
    local.writeUInt16LE(0x0800, 6); // flags: UTF-8
    local.writeUInt16LE(0, 8); // method: STORED
    local.writeUInt16LE(0, 10); // time
    local.writeUInt16LE(0, 12); // date
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBytes.length, 26);
    local.writeUInt16LE(0, 28);
    nameBytes.copy(local, 30);
    chunks.push(local, data);

    const cen = Buffer.alloc(46 + nameBytes.length);
    cen.writeUInt32LE(0x02014b50, 0);
    cen.writeUInt16LE(20, 4);
    cen.writeUInt16LE(20, 6);
    cen.writeUInt16LE(0x0800, 8);
    cen.writeUInt16LE(0, 10);
    cen.writeUInt16LE(0, 12);
    cen.writeUInt16LE(0, 14);
    cen.writeUInt32LE(crc, 16);
    cen.writeUInt32LE(data.length, 20);
    cen.writeUInt32LE(data.length, 24);
    cen.writeUInt16LE(nameBytes.length, 28);
    cen.writeUInt16LE(0, 30);
    cen.writeUInt16LE(0, 32);
    cen.writeUInt16LE(0, 34);
    cen.writeUInt16LE(0, 36);
    cen.writeUInt32LE(0, 38);
    cen.writeUInt32LE(offset, 42);
    nameBytes.copy(cen, 46);
    central.push(cen);
    offset += local.length + data.length;
  }

  const centralBuf = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(0, 4);
  end.writeUInt16LE(0, 6);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(centralBuf.length, 12);
  end.writeUInt32LE(offset, 16);
  end.writeUInt16LE(0, 20);

  return Buffer.concat([...chunks, centralBuf, end]);
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i += 1) {
    let value = i;
    for (let bit = 0; bit < 8; bit += 1) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }
    table[i] = value >>> 0;
  }
  return table;
})();

function crc32(data: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of data) {
    crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

test.describe("Phase 1 页面", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test("Word 模板页：上传 .docx 后展示占位符并可渲染下载", async ({ page }) => {
    await page.getByRole("link", { name: "Word 模板" }).click();
    await expect(page.getByRole("heading", { name: "Word 模板" })).toBeVisible();
    await resetScroll(page);

    await page.locator('[data-test="template-file"]').setInputFiles({
      name: "e2e-template.docx",
      mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      buffer: buildMinimalDocx(["title", "body", "references"]),
    });
    await page.locator('[data-test="template-name"]').fill(`E2E 模板 ${Date.now()}`);
    await page.locator('[data-test="template-upload-submit"]').click();

    // 上传成功 → 展示识别到的占位符；
    // 若被限流（`rate_limit.rules.upload`）则必须展示可读原因——多文件/多视口
    // 连跑会耗尽配额，这是测试环境依赖，不是产品缺陷。
    const outcome = page.locator(
      '[data-test="template-upload-success"], [data-test="template-upload-error"]'
    );
    await expect(outcome.first()).toBeVisible({ timeout: 30_000 });
    const outcomeText = (await outcome.first().textContent()) ?? "";
    if (outcomeText.includes("请求过于频繁")) {
      test.info().annotations.push({
        type: "rate-limited",
        description: "模板上传被限流（429），展示可读原因即为通过",
      });
      return;
    }
    await expect(page.locator('[data-test="template-upload-success"]')).toBeVisible();

    const row = page.locator('[data-test^="template-row-"]').first();
    await expect(row).toContainText("title");
    await expect(row).toContainText("references");

    // 渲染并下载（窄屏下按钮在行末，先滚动到可见再点）
    const renderButton = row.locator('[data-test^="template-render-"]');
    await renderButton.scrollIntoViewIfNeeded();
    const downloadPromise = page.waitForEvent("download", { timeout: 30_000 });
    // ⚠️ 用 `force`：移动端设备模拟下 Playwright 的「可操作性」检查以
    // `documentElement.clientWidth` 为基准，而它与 `window.innerWidth` 不一致
    // （实测 393 vs 554），会把实际可点的按钮判为不可见。
    await renderButton.click({ force: true });
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/\.docx$/);

    await page.screenshot({
      path: resolve(SHOTS, `phase1-templates-${test.info().project.name}.png`),
      fullPage: true,
    });

    // 清理：删除刚上传的模板，避免污染演示账号
    const deleteButton = row.locator('[data-test^="template-delete-"]');
    await deleteButton.scrollIntoViewIfNeeded();
    await deleteButton.click({ force: true });
    // 删除有二次确认（防止后端 unlink 磁盘文件）：先确认，再等待成功提示。
    const confirmButton = row.locator('[data-test^="template-delete-confirm-"]');
    await expect(confirmButton).toBeVisible();
    await confirmButton.click();
    await expect(page.locator('[data-test="template-upload-success"]')).toContainText("已删除", {
      timeout: 30_000,
    });
  });

  test("导入页：粘贴 BibTeX 解析预览（含中文原样）", async ({ page }) => {
    await page.getByRole("link", { name: "导入文献" }).click();
    await expect(page.getByRole("heading", { name: "导入参考文献" })).toBeVisible();
    await resetScroll(page);

    const bibtex = [
      "@article{es2023ragas,",
      "  author = {Es, Shahul and James, Jithin},",
      "  title = {Ragas: Automated Evaluation of Retrieval Augmented Generation},",
      "  year = {2023},",
      "  doi = {10.18653/v1/2023.evalnlp-1.16}",
      "}",
    ].join("\n");

    await page.locator('[data-test="import-content"]').fill(bibtex);
    await page.locator('[data-test="import-submit"]').click();

    // 成功 → 预览；被限流（`rate_limit.rules.verify` 覆盖 /references/import）
    // → 必须展示可读原因（多文件/多视口连跑会耗尽配额，属测试环境依赖）
    const importOutcome = page.locator(
      '[data-test="import-preview"], [data-test="import-error"]'
    );
    await expect(importOutcome.first()).toBeVisible({ timeout: 30_000 });
    const importText = (await importOutcome.first().textContent()) ?? "";
    if (importText.includes("请求过于频繁")) {
      test.info().annotations.push({
        type: "rate-limited",
        description: "导入端点被限流（429），展示可读原因即为通过",
      });
      return;
    }

    const preview = page.locator('[data-test="import-preview"]');
    await expect(preview).toBeVisible({ timeout: 30_000 });
    await expect(preview).toContainText("Ragas");
    await expect(page.locator('[data-test="import-summary"]')).toContainText("BibTeX");

    // 中文 RIS：标题与作者必须原样保留
    const ris = [
      "TY  - JOUR",
      "AU  - 牛耕田",
      "AU  - 李泽",
      "TI  - 面向中文文献综述的自动化方法",
      "PY  - 2024",
      "JO  - 中文信息学报",
      "SP  - 100",
      "EP  - 110",
      "ER  - ",
    ].join("\n");
    await page.locator('[data-test="import-content"]').fill(ris);
    await page.locator('[data-test="import-format"]').selectOption("ris");
    await page.locator('[data-test="import-submit"]').click();
    await expect(page.locator('[data-test="import-summary"]')).toContainText("RIS", {
      timeout: 30_000,
    });
    await expect(preview).toContainText("面向中文文献综述的自动化方法");
    await expect(preview).toContainText("牛耕田");

    await page.screenshot({
      path: resolve(SHOTS, `phase1-import-${test.info().project.name}.png`),
      fullPage: true,
    });
  });

  test("导入页：非法内容给出可读错误", async ({ page }) => {
    await page.getByRole("link", { name: "导入文献" }).click();
    await resetScroll(page);
    await page.locator('[data-test="import-content"]').fill("这既不是 BibTeX 也不是 RIS");
    await page.locator('[data-test="import-submit"]').click();

    await expect(page.locator('[data-test="import-error"]')).toBeVisible({ timeout: 30_000 });
    const errorText = (await page.locator('[data-test="import-error"]').textContent()) ?? "";
    if (errorText.includes("请求过于频繁")) {
      // 限流先于格式校验命中（同一端点共用配额），展示可读原因即为通过
      test.info().annotations.push({
        type: "rate-limited",
        description: "导入端点被限流（429），展示可读原因即为通过",
      });
      return;
    }
    expect(errorText).toContain("无法识别内容格式");
  });

  test("两个新页面均无横向溢出", async ({ page }) => {
    for (const [name, link] of [
      ["templates", "Word 模板"],
      ["import", "导入文献"],
    ] as const) {
      await page.getByRole("link", { name: link }).click();
      await expect(page.getByRole("heading").first()).toBeVisible();
      // 等待列表/横幅等异步内容落定，避免测量到中间态
      await page.waitForLoadState("networkidle").catch(() => undefined);
      await page.waitForTimeout(200);
      await resetScroll(page);

      const metrics = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
        // 找出实际超出视口的元素，便于失败时定位（不是断言依据，只用于诊断）
        offenders: Array.from(document.querySelectorAll("body *"))
          .map((el) => {
            const rect = el.getBoundingClientRect();
            return {
              tag: el.tagName,
              cls: typeof (el as HTMLElement).className === "string" ? (el as HTMLElement).className : "",
              right: Math.round(rect.right),
              width: Math.round(rect.width),
            };
          })
          .filter((item) => item.right > window.innerWidth + 1 && item.width > 0)
          .slice(0, 5),
      }));

      const overflow = metrics.scrollWidth - metrics.innerWidth;
      expect(
        overflow,
        `${name} 页面横向溢出 ${overflow}px；超出元素：${JSON.stringify(metrics.offenders)}`
      ).toBeLessThanOrEqual(1);
    }
  });
});

