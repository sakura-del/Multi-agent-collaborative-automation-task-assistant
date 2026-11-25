import { expect, test, type APIRequestContext } from "@playwright/test";
import { apiBase, demoCreds, secondaryCreds } from "./support/env";
import { clearAuthSession, fetchAuthToken, seedAuthByApi } from "./support/auth";

const API = apiBase();

async function getCompletedTaskId(request: APIRequestContext): Promise<string> {
  const { account, password } = demoCreds();
  const token = (await fetchAuthToken(request, account, password)).access_token;
  const resp = await request.get(`${API}/tasks?limit=50`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(resp.ok()).toBeTruthy();
  const list = (await resp.json()) as { task_id: string; status: string; title: string }[];
  const hit =
    list.find((t) => t.title.includes("[E2E-Seed]") && t.status === "completed") ??
    list.find((t) => t.status === "completed");
  expect(hit, "种子库应存在已完成任务").toBeTruthy();
  return hit!.task_id;
}

test.describe("prefers-reduced-motion: reduce 专项验收", () => {
  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    const matches = await page.evaluate(
      () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    );
    expect(matches).toBe(true);
  });

  test("RM-1：CSS 全局兜底与骨架屏/加载图标瞬时化", async ({ page, request }) => {
    const sec = secondaryCreds();
    await seedAuthByApi(page, request, sec.account, sec.password, "/tasks");

    await page.route("**/api/v1/billing/usage", async (route) => {
      await new Promise((r) => setTimeout(r, 900));
      await route.continue();
    });

    await page.goto("/settings/plan", { waitUntil: "domcontentloaded" });
    const loading = page.locator('[data-test="plan-loading"]');
    await expect(loading).toBeVisible({ timeout: 15_000 });

    const durations = await page.evaluate(() => {
      const toMs = (raw: string): number => {
        const first = raw.split(",")[0]?.trim() ?? "0s";
        if (first.endsWith("ms")) return parseFloat(first) || 0;
        if (first.endsWith("s")) return (parseFloat(first) || 0) * 1000;
        return 0;
      };
      const results: Array<{ selector: string; animMs: number; transMs: number; animName: string }> =
        [];
      const candidates = [
        ...Array.from(document.querySelectorAll(".animate-pulse")).map((el) => ({
          sel: ".animate-pulse",
          el,
        })),
        ...Array.from(document.querySelectorAll(".animate-spin")).map((el) => ({
          sel: ".animate-spin",
          el,
        })),
        ...Array.from(document.querySelectorAll("button")).slice(0, 3).map((el) => ({
          sel: "button",
          el,
        })),
      ];
      for (const { sel, el } of candidates) {
        const s = getComputedStyle(el);
        results.push({
          selector: sel,
          animMs: toMs(s.animationDuration),
          transMs: toMs(s.transitionDuration),
          animName: s.animationName,
        });
      }
      return results;
    });

    expect(durations.length).toBeGreaterThan(0);
    for (const item of durations) {
      const animSuppressed = item.animName === "none" || item.animMs <= 1;
      expect(animSuppressed, `${item.selector} 动画时长未归零: ${JSON.stringify(item)}`).toBe(
        true,
      );
      expect(item.transMs, `${item.selector} 过渡时长未归零: ${JSON.stringify(item)}`).toBeLessThanOrEqual(
        1,
      );
    }

    await expect(page.locator('[data-test="plan-current"]')).toBeVisible({ timeout: 30_000 });
  });

  test("RM-2：BeTraceWaterfall 回放 <=100ms 直达末帧且 span bar scaleX===1", async ({
    page,
    request,
  }) => {
    const taskId = await getCompletedTaskId(request);
    const { account, password } = demoCreds();
    await seedAuthByApi(page, request, account, password, `/tasks/${taskId}?tab=execution`);

    const waterfall = page.locator('[data-test="trace-waterfall"]');
    await expect(waterfall).toBeVisible({ timeout: 30_000 });

    const replayBtn = page.locator('[data-test="trace-replay"]');
    await replayBtn.click();
    // 严格 <= 100ms 立即检查，绝不等 2s
    await page.waitForTimeout(50);

    const state = await page.evaluate(() => {
      const wf = document.querySelector('[data-test="trace-waterfall"]')!;
      const timeLabel =
        Array.from(wf.querySelectorAll("span")).find((el) =>
          /[\d.]+(?:ms|s)\s*\/\s*[\d.]+(?:ms|s)/.test(el.textContent ?? ""),
        )?.textContent ?? "";
      const parts = timeLabel.split("/").map((p) => p.trim());

      const bars = Array.from(
        wf.querySelectorAll<HTMLElement>('[style*="transform-origin: left center"]'),
      ).map((el) => {
        const tf = getComputedStyle(el).transform;
        if (!tf || tf === "none") return 1;
        const m = new DOMMatrixReadOnly(tf);
        return Math.round(m.a * 1000) / 1000;
      });

      const playheadLine = wf.querySelector<HTMLElement>('[data-test="trace-playhead-line"]');
      return {
        timeLabel,
        leftTime: parts[0] ?? "",
        rightTime: parts[1] ?? "",
        barCount: bars.length,
        bars,
        playheadRatio: playheadLine?.style.getPropertyValue("--playhead-ratio") ?? null,
      };
    });

    expect(state.barCount).toBeGreaterThanOrEqual(1);
    expect(state.leftTime).toBeTruthy();
    expect(state.leftTime).toBe(state.rightTime);
    for (const sx of state.bars) {
      expect(sx, "reduced-motion 下所有 span bar 应直接处于 scaleX=1").toBe(1);
    }

    // 验证 scrub 拖拽仍可正常调整位置
    const range = waterfall.locator('input[type="range"]');
    await range.evaluate((el) => {
      const input = el as HTMLInputElement;
      const max = Number(input.max || "1000");
      input.value = String(Math.round(max / 2));
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const scrubbedLabel = await waterfall
      .locator("span")
      .filter({ hasText: /\// })
      .first()
      .innerText();
    const [cur, total] = scrubbedLabel.split("/").map((s) => s.trim());
    expect(cur).not.toBe(total);
  });

  test("RM-3：FunnelChart 在 reduced-motion 下隐藏粒子且数据正常展示", async ({
    page,
    request,
  }) => {
    const taskId = await getCompletedTaskId(request);
    const { account, password } = demoCreds();
    await seedAuthByApi(page, request, account, password, `/tasks/${taskId}/screening`);

    const funnel = page.locator('[data-test="screening-prisma"]');
    await expect(funnel).toBeVisible({ timeout: 30_000 });
    await expect(funnel.locator('svg[role="img"]')).toBeVisible();
    await expect(funnel).toContainText("总体转化");

    expect(await funnel.locator(".funnel-particles").count()).toBe(0);
    expect(await funnel.locator(".funnel-particle").count()).toBe(0);
  });

  test("RM-4：AnimatedCollapse / MotionDialog / MotionDrawer 偏移归零且 Esc 还焦", async ({
    page,
    request,
  }) => {
    const taskId = await getCompletedTaskId(request);
    const { account, password } = demoCreds();
    await seedAuthByApi(page, request, account, password, "/tasks");

    // 1. AnimatedCollapse（TaskCreateForm 高级设置）
    await page.locator('[data-test="new-task"]').click();
    const toggleAdv = page.locator('[data-test="toggle-advanced-options"]');
    await expect(toggleAdv).toBeVisible();
    await toggleAdv.click();
    await page.waitForTimeout(50);

    const collapseState = await page.evaluate(() => {
      const dateFrom = document.querySelector('[data-test="date-from"]') as HTMLElement | null;
      const inner = dateFrom?.closest(".overflow-visible, .overflow-hidden") as HTMLElement | null;
      return {
        innerFound: !!inner,
        overflow: inner ? getComputedStyle(inner).overflow : "",
        opacity: inner ? getComputedStyle(inner).opacity : "",
      };
    });
    expect(collapseState.innerFound).toBe(true);
    expect(collapseState.overflow).toBe("visible");
    expect(collapseState.opacity).toBe("1");

    // 2. MotionDialog（CommandPalette）
    const cmdTrigger = page.locator('[data-test="open-command-palette"]');
    await cmdTrigger.focus();
    await cmdTrigger.click();

    const dialog = page.locator('[data-test="command-palette"]');
    await expect(dialog).toBeVisible();
    // MotionDialog reduced-motion 使用 DURATION.micro (80ms)，等待极短过渡落定后再读取精确几何属性
    await expect
      .poll(() => dialog.evaluate((el) => getComputedStyle(el).opacity), { timeout: 200 })
      .toBe("1");
    const dialogGeom = await dialog.evaluate((el) => {
      const s = getComputedStyle(el);
      const m = s.transform && s.transform !== "none" ? new DOMMatrixReadOnly(s.transform) : null;
      return {
        opacity: s.opacity,
        tx: m ? m.m41 : 0,
        ty: m ? m.m42 : 0,
        sx: m ? m.a : 1,
        sy: m ? m.d : 1,
      };
    });
    expect(dialogGeom.opacity).toBe("1");
    expect(dialogGeom.tx).toBe(0);
    expect(dialogGeom.ty).toBe(0);
    expect(dialogGeom.sx).toBe(1);
    expect(dialogGeom.sy).toBe(1);

    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    const focusedAfterDialog = await page.evaluate(
      () => document.activeElement?.getAttribute("data-test") ?? "",
    );
    expect(focusedAfterDialog).toBe("open-command-palette");

    // 3. MotionDrawer（annotation-drawer）
    await page.goto(`/tasks/${taskId}?tab=result`, { waitUntil: "domcontentloaded" });
    const drawerTrigger = page.locator('[data-test="toggle-annotation"]');
    await expect(drawerTrigger).toBeVisible({ timeout: 30_000 });
    await drawerTrigger.focus();
    await drawerTrigger.click();
    await page.waitForTimeout(120);

    const drawer = page.locator('[data-test="annotation-drawer"]');
    await expect(drawer).toBeVisible();
    const drawerGeom = await drawer.evaluate((el) => {
      const s = getComputedStyle(el);
      const m = s.transform && s.transform !== "none" ? new DOMMatrixReadOnly(s.transform) : null;
      return {
        opacity: s.opacity,
        tx: m ? m.m41 : 0,
        ty: m ? m.m42 : 0,
        sx: m ? m.a : 1,
        sy: m ? m.d : 1,
      };
    });
    expect(drawerGeom.opacity).toBe("1");
    expect(drawerGeom.tx).toBe(0);
    expect(drawerGeom.ty).toBe(0);
    expect(drawerGeom.sx).toBe(1);
    expect(drawerGeom.sy).toBe(1);

    await page.keyboard.press("Escape");
    await expect(drawer).toHaveCount(0);
    const focusedAfterDrawer = await page.evaluate(
      () => document.activeElement?.getAttribute("data-test") ?? "",
    );
    expect(focusedAfterDrawer).toBe("toggle-annotation");
  });

  test("RM-5：JS 平滑滚动 <150ms 到位 & 数值变更下一帧直达", async ({ page, request }) => {
    // 1. PlanView 免费额度用尽点击升级滚动
    const sec = secondaryCreds();
    await seedAuthByApi(page, request, sec.account, sec.password, "/settings/plan");
    const upgradeBtn = page.locator('[data-test="plan-upgrade-action"]');
    await expect(upgradeBtn).toBeVisible({ timeout: 30_000 });

    const yBefore = await page.evaluate(() => window.scrollY);
    await upgradeBtn.click();
    await page.waitForTimeout(80);
    const yAt80 = await page.evaluate(() => window.scrollY);
    await page.waitForTimeout(80);
    const yAt160 = await page.evaluate(() => window.scrollY);
    expect(yAt80).toBeGreaterThanOrEqual(yBefore);
    expect(Math.abs(yAt160 - yAt80), "reduced-motion 下滚动应在 <150ms 内停止").toBeLessThanOrEqual(
      1,
    );

    // 2. LandingShowcase 计数器下一帧直达最终整数
    await clearAuthSession(page);
    await page.goto("/landing", { waitUntil: "domcontentloaded" });
    const counter = page.locator('[data-test="prisma-included-counter"]');
    await expect(counter).toBeVisible({ timeout: 20_000 });
    const beforeNum = Number(((await counter.innerText()).match(/\d+/) ?? ["0"])[0]);

    const unpressedInclude = page
      .locator('[data-test^="demo-verdict-"][data-test$="-include"][aria-pressed="false"]')
      .first();
    await expect(unpressedInclude).toBeVisible();
    await unpressedInclude.click();

    // 下一帧立即验证已等于目标值（无中间插值）
    const nextText = await page.evaluate(
      () =>
        new Promise<string>((resolve) =>
          requestAnimationFrame(() => {
            const el = document.querySelector('[data-test="prisma-included-counter"]');
            resolve(el?.textContent ?? "");
          }),
        ),
    );
    const afterNum = Number((nextText.match(/\d+/) ?? ["0"])[0]);
    expect(afterNum).toBe(beforeNum + 1);
  });

  test("RM-6：reduced-motion 下核心业务功能（表单校验/PRISMA/ConfirmBar）100% 正常", async ({
    page,
    request,
  }) => {
    const taskId = await getCompletedTaskId(request);
    const { account, password } = demoCreds();
    await seedAuthByApi(page, request, account, password, "/tasks");

    // 1. TaskCreateForm 空提交校验报错聚焦
    await page.locator('[data-test="new-task"]').click();
    await page.locator('[data-test="submit"]').click();
    await expect(page.locator('[data-test="validation-error"]')).toBeVisible();

    // 2. PRISMA 筛选决策成功提示
    await page.goto(`/tasks/${taskId}/screening`, { waitUntil: "domcontentloaded" });
    const maybeBtn = page.locator('button[data-test^="screening-maybe-"]').first();
    await expect(maybeBtn).toBeVisible({ timeout: 30_000 });
    await maybeBtn.click();
    await expect(page.locator('[data-test="screening-success"]')).toBeVisible({ timeout: 15_000 });

    // 3. ConfirmBar 取消保留 + 确认删除（任务详情批注）
    await page.goto(`/tasks/${taskId}?tab=result`, { waitUntil: "domcontentloaded" });
    await page.locator('[data-test="toggle-annotation"]').click();
    const drawer = page.locator('[data-test="annotation-drawer"]');
    await expect(drawer).toBeVisible();

    const uniqueNote = `[RM-6] 减动效批注-${Date.now()}`;
    await page.locator('[data-test="annotation-input"]').fill(uniqueNote);
    await page.locator('[data-test="annotation-submit"]').click();

    const list = page.locator('[data-test="annotation-list"]');
    const row = list.locator("li").filter({ hasText: uniqueNote });
    await expect(row).toHaveCount(1, { timeout: 20_000 });

    await row.getByRole("button", { name: "删除" }).click();
    const cancelBtn = row.locator('[data-test^="annotation-delete-cancel-"]');
    const confirmBtn = row.locator('[data-test^="annotation-delete-confirm-"]');
    await expect(confirmBtn).toBeVisible();
    await cancelBtn.click();
    await expect(row).toHaveCount(1);

    await row.getByRole("button", { name: "删除" }).click();
    await expect(confirmBtn).toBeVisible();
    await confirmBtn.click();
    await expect(list.locator("li").filter({ hasText: uniqueNote })).toHaveCount(0, {
      timeout: 20_000,
    });
  });
});
