// 截服务器真实页面（重设计后），验证侧栏分组与整体视觉。
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE = "http://10.0.10.2:48080";
const OUT = "C:/Users/Lenovo/AppData/Local/Temp/maa_after";
const ACCOUNT = "demo";
const PASSWORD = "demo12345";

const PAGES = [
  ["10-tasks", "/tasks"],
  ["11-knowledge", "/knowledge"],
  ["12-plan", "/settings/plan"],
];

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(OUT, "00-login.png"), fullPage: false });

  await page.fill("#account", ACCOUNT);
  await page.fill("#password", PASSWORD);
  await page.locator('button[type="submit"]').first().click();
  await page.waitForTimeout(5000);
  console.log(`after login: ${page.url()}`);

  for (const [name, route] of PAGES) {
    await page.goto(`${BASE}${route}`, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(900);
    await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: false });
    console.log(`shot ${name}: ${page.url()}`);
  }

  // 移动端侧栏（折成横向导航）
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const mp = await mobile.newPage();
  await mp.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 60000 });
  await mp.fill("#account", ACCOUNT);
  await mp.fill("#password", PASSWORD);
  await mp.locator('button[type="submit"]').first().click();
  await mp.waitForTimeout(4000);
  await mp.screenshot({ path: path.join(OUT, "20-mobile-tasks.png"), fullPage: true });
  console.log(`shot mobile: ${mp.url()}`);

  await browser.close();
  console.log(`\nDone → ${OUT}`);
  for (const f of fs.readdirSync(OUT).sort()) {
    console.log(`  ${f} (${Math.round(fs.statSync(path.join(OUT, f)).size / 1024)} KB)`);
  }
}

main().catch((e) => {
  console.error("ERROR:", e?.message || e);
  process.exit(1);
});
