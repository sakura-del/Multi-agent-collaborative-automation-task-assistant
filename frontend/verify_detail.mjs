import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE = "http://10.0.10.2:48080";
const OUT = "C:/Users/Lenovo/AppData/Local/Temp/maa_detail2";

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 60000 });
  await page.fill("#account", "demo");
  await page.fill("#password", "demo12345");
  await page.locator('form button[type="submit"]').click();
  await page.waitForTimeout(4000);
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await login(page);

  await page.goto(`${BASE}/tasks`, { waitUntil: "networkidle" });
  await page.waitForTimeout(700);
  const rows = page.locator('[data-test^="task-row-"]');
  let taskId = null;
  for (let i = 0; i < (await rows.count()); i += 1) {
    const t = await rows.nth(i).innerText();
    if (t.includes("已完成") && t.includes("检索增强")) {
      taskId = (await rows.nth(i).getAttribute("data-test"))?.replace("task-row-", "");
      break;
    }
  }
  console.log(`TASK_ID=${taskId}`);

  await page.goto(`${BASE}/tasks/${taskId}?tab=execution`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  const vp = page.locator('[data-test="dag-viewport"]');
  const orientation = await vp.getAttribute("data-orientation");
  const nodeCount = await page.locator('[data-test^="dag-node-"]').count();
  console.log(`DAG orientation=${orientation} nodes=${nodeCount}`);

  const vb = await vp.boundingBox();
  let visible = 0;
  for (let i = 0; i < nodeCount; i += 1) {
    const b = await page.locator('[data-test^="dag-node-"]').nth(i).boundingBox();
    if (!b || !vb) continue;
    if (b.y >= vb.y - 2 && b.y + b.height <= vb.y + vb.height + 2 &&
        b.x >= vb.x - 2 && b.x + b.width <= vb.x + vb.width + 2) visible += 1;
  }
  console.log(`nodes fully visible without scrolling = ${visible}/${nodeCount}`);
  await page.screenshot({ path: path.join(OUT, "execution.png") });

  await page.goto(`${BASE}/tasks/${taskId}?tab=result`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  const h1 = await page.locator('[data-test="result-markdown"] h1').count();
  const h2 = await page.locator('[data-test="result-markdown"] h2').count();
  const st = await page.locator('[data-test="result-markdown"] strong').count();
  const txt = await page.locator('[data-test="result-markdown"]').innerText();
  console.log(`markdown: h1=${h1} h2=${h2} strong=${st} rawHashLeft=${txt.includes("## ")}`);
  await page.screenshot({ path: path.join(OUT, "result.png") });

  const before = await page.locator('[data-test="annotation-drawer"]').count();
  await page.locator('[data-test="toggle-annotation"]').click();
  await page.waitForTimeout(600);
  const after = await page.locator('[data-test="annotation-drawer"]').count();
  console.log(`annotation drawer: closed=${before} open=${after}`);
  await page.screenshot({ path: path.join(OUT, "annotation-drawer.png") });

  const mctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const mp = await mctx.newPage();
  await login(mp);
  await mp.goto(`${BASE}/tasks/${taskId}?tab=execution`, { waitUntil: "networkidle" });
  await mp.waitForTimeout(1500);
  console.log(`mobile DAG orientation=${await mp.locator('[data-test="dag-viewport"]').getAttribute("data-orientation")}`);
  await mp.screenshot({ path: path.join(OUT, "mobile-execution.png") });

  await browser.close();
  console.log(`Done -> ${OUT}`);
}

main().catch((e) => { console.error("ERROR:", e?.message || e); process.exit(1); });
