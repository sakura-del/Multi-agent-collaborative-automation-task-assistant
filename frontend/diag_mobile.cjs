
const { chromium, devices } = require("playwright");

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ ...devices["Pixel 5"] });
  const page = await context.newPage();
  await page.goto("http://10.0.10.2:48080/login");
  await page.locator("#account").fill("demo");
  await page.locator("#password").fill("demo12345");
  await page.locator('form button[type="submit"]').click();
  await page.waitForSelector('a[href="/tasks"]', { timeout: 30000 });

  await page.goto("http://10.0.10.2:48080/templates");
  await page.waitForTimeout(2500);

  const info = await page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll('[data-test^="template-row-"]'));
    const out = [];
    for (const row of rows) {
      const btn = row.querySelector('[data-test^="template-render-"]');
      const rowRect = row.getBoundingClientRect();
      const btnRect = btn ? btn.getBoundingClientRect() : null;
      const style = btn ? getComputedStyle(btn) : null;
      out.push({
        rowTestId: row.getAttribute("data-test"),
        rowRect: { x: rowRect.x, y: rowRect.y, w: rowRect.width, h: rowRect.height },
        btnRect: btnRect ? { x: btnRect.x, y: btnRect.y, w: btnRect.width, h: btnRect.height } : null,
        btnDisplay: style ? style.display : null,
        btnVisibility: style ? style.visibility : null,
        btnOpacity: style ? style.opacity : null,
        disabled: btn ? btn.disabled : null,
      });
    }
    return {
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
      scrollX: window.scrollX,
      rows: out,
    };
  });

  const outPath = process.argv[2];
  require("fs").writeFileSync(outPath, JSON.stringify(info, null, 2), "utf-8");
  await browser.close();
})();
