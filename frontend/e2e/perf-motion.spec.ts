import { expect, test, type APIRequestContext } from "@playwright/test";
import { apiBase, demoCreds } from "./support/env";
import { fetchAuthToken, seedAuthByApi } from "./support/auth";
import { measureActionPerf, type PerfActionResult } from "./support/perf";

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
  expect(hit, "种子库应存在已完成长综述任务").toBeTruthy();
  return hit!.task_id;
}

test.describe("浏览器真实性能与零 Layout Thrashing 硬门禁", () => {
  test.setTimeout(180_000);

  test("PERF-1 ~ PERF-5：长文/长列表滚动、瀑布图回放拖拽、PRISMA 决策与漏斗粒子、Tab 与聊天壳切换", async ({
    page,
    request,
  }) => {
    const taskId = await getCompletedTaskId(request);
    const { account, password } = demoCreds();
    await seedAuthByApi(page, request, account, password, `/tasks/${taskId}?tab=result`);

    const report: Record<string, PerfActionResult | Record<string, unknown>> = {};

    // ===== PERF-1：长综述结果页滚动（TaskResult >= 8,000 字） =====
    const mdBody = page.locator('[data-test="result-markdown"]');
    await expect(mdBody).toBeVisible({ timeout: 30_000 });
    const textLen = await mdBody.evaluate((el) => (el.textContent ?? "").trim().length);
    expect(textLen, "TaskResult 长综述字数应 >= 8000").toBeGreaterThanOrEqual(8000);

    const perf1 = await measureActionPerf(page, async () => {
      const maxScroll = await page.evaluate(
        () => Math.max(400, document.documentElement.scrollHeight - window.innerHeight),
      );
      for (let i = 1; i <= 10; i += 1) {
        await page.evaluate((y) => window.scrollTo(0, y), Math.round((maxScroll * i) / 10));
        await page.waitForTimeout(30);
      }
      for (let i = 9; i >= 0; i -= 1) {
        await page.evaluate((y) => window.scrollTo(0, y), Math.round((maxScroll * i) / 10));
        await page.waitForTimeout(30);
      }
    });
    report.PERF_1_TaskResultScroll = perf1;
    expect(perf1.fps).toBeGreaterThanOrEqual(50);
    expect(perf1.jankFramesCount).toBeLessThanOrEqual(2);
    expect(perf1.cls).toBeLessThanOrEqual(0.01);
    expect(perf1.deltaLayoutCount).toBeLessThanOrEqual(10);

    // ===== PERF-5 (part A)：TaskResult Tab 切换 3 轮（paper <-> matrix） =====
    const tabPaper = page.locator('[data-test="result-tab-paper"]');
    const tabMatrix = page.locator('[data-test="result-tab-matrix"]');
    await expect(tabMatrix).toBeVisible();
    const switchDurations: number[] = [];
    const perf5a = await measureActionPerf(page, async () => {
      for (let round = 0; round < 3; round += 1) {
        const t0 = Date.now();
        await tabMatrix.click();
        await expect(page.locator('[data-test="evidence-matrix"]')).toBeVisible();
        const t1 = Date.now();
        await tabPaper.click();
        await expect(mdBody).toBeVisible();
        const t2 = Date.now();
        if (round >= 1) {
          switchDurations.push(t1 - t0, t2 - t1);
        }
      }
    });
    report.PERF_5A_ResultTabSwitch = { ...perf5a, switchDurationsMs: switchDurations };
    expect(perf5a.cls).toBeLessThanOrEqual(0.01);

    // ===== PERF-2 (part A)：TaskLog >= 200 条分页与滚动 =====
    await page.goto(`/tasks/${taskId}?tab=logs`, { waitUntil: "domcontentloaded" });
    await expect(page.locator('[data-test="task-log"]')).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('[data-test="log-count"]')).toContainText("210 条", {
      timeout: 30_000,
    });
    await expect(page.locator('[data-test="log-page"]')).toBeVisible();
    const logCount = await page.locator('[data-test^="log-row-"]').count();
    expect(logCount, "TaskLog 首页渲染条数应 >= 100").toBeGreaterThanOrEqual(100);

    const perf2a = await measureActionPerf(page, async () => {
      const maxScroll = await page.evaluate(
        () => Math.max(400, document.documentElement.scrollHeight - window.innerHeight),
      );
      for (let i = 1; i <= 10; i += 1) {
        await page.evaluate((y) => window.scrollTo(0, y), Math.round((maxScroll * i) / 10));
        await page.waitForTimeout(30);
      }
    });
    report.PERF_2A_TaskLogScroll = perf2a;
    expect(perf2a.fps).toBeGreaterThanOrEqual(50);
    expect(perf2a.droppedFramesPct).toBeLessThanOrEqual(10);
    expect(perf2a.cls).toBeLessThanOrEqual(0.02);

    // ===== PERF-3：BeTraceWaterfall 回放与 20 次拖拽 Scrub =====
    await page.goto(`/tasks/${taskId}?tab=execution`, { waitUntil: "domcontentloaded" });
    const waterfall = page.locator('[data-test="trace-waterfall"]');
    await expect(waterfall).toBeVisible({ timeout: 30_000 });
    const spanCount = await waterfall.locator('[style*="transform-origin: left center"]').count();
    expect(spanCount, "BeTraceWaterfall Span 数应 >= 15").toBeGreaterThanOrEqual(15);
    // 等待路由转场与按钮滚入视口稳定后再开始采样，避免把入场/滚动计入回放窗口
    await page.locator('[data-test="trace-replay"]').scrollIntoViewIfNeeded();
    await page.waitForTimeout(250);

    const perf3Replay = await measureActionPerf(page, async () => {
      await page.locator('[data-test="trace-replay"]').click();
      await page.waitForTimeout(2150);
    });
    report.PERF_3A_WaterfallReplay = perf3Replay;
    expect(perf3Replay.deltaLayoutCount).toBeLessThanOrEqual(3);
    expect(perf3Replay.fps).toBeGreaterThanOrEqual(50);
    expect(perf3Replay.cls).toBeLessThanOrEqual(0.001);

    const perf3Scrub = await measureActionPerf(page, async () => {
      const range = waterfall.locator('input[type="range"]');
      const max = Number((await range.getAttribute("max")) ?? "60000");
      for (let i = 1; i <= 20; i += 1) {
        const val = Math.round((max * i) / 20);
        await range.evaluate((el, v) => {
          const input = el as HTMLInputElement;
          input.value = String(v);
          input.dispatchEvent(new Event("input", { bubbles: true }));
        }, val);
        await page.waitForTimeout(16);
      }
    });
    report.PERF_3B_WaterfallScrub = perf3Scrub;
    expect(perf3Scrub.deltaLayoutCount).toBeLessThanOrEqual(5);
    expect(perf3Scrub.deltaLayoutDurationMs).toBeLessThanOrEqual(15);

    // ===== PERF-2 (part B) & PERF-4：PRISMA 80+ 列表滚动、连续决策 & FunnelChart 粒子 =====
    await page.goto(`/tasks/${taskId}/screening`, { waitUntil: "domcontentloaded" });
    const rows = page.locator('[data-test^="screening-row-"]');
    await expect(rows.first()).toBeVisible({ timeout: 30_000 });
    expect(await rows.count(), "ScreeningView 种子文献条数应 >= 80").toBeGreaterThanOrEqual(80);
    // 等待异步 Mermaid 流程图初始化落定，避免首屏 chunk 编译混入稳态滚动帧率
    await expect(page.locator('[data-test="screening-mermaid-diagram"] svg')).toBeVisible({
      timeout: 20_000,
    });
    await page.waitForTimeout(350);

    const perf2b = await measureActionPerf(page, async () => {
      const maxScroll = await page.evaluate(
        () => Math.max(400, document.documentElement.scrollHeight - window.innerHeight),
      );
      for (let i = 1; i <= 10; i += 1) {
        await page.evaluate((y) => window.scrollTo(0, y), Math.round((maxScroll * i) / 10));
        await page.waitForTimeout(30);
      }
      for (let i = 9; i >= 0; i -= 1) {
        await page.evaluate((y) => window.scrollTo(0, y), Math.round((maxScroll * i) / 10));
        await page.waitForTimeout(30);
      }
    });
    report.PERF_2B_ScreeningScroll = perf2b;
    expect(perf2b.fps).toBeGreaterThanOrEqual(50);
    expect(perf2b.droppedFramesPct).toBeLessThanOrEqual(10);
    expect(perf2b.cls).toBeLessThanOrEqual(0.02);

    // 定位到候选列表首行后再连续点击 5 次视口内卡片「待定」，测试 Pin-in-Place 零跳动与零 CLS
    // 预留 >= 140px 顶部缓冲避开 TopBar，并确保目标按钮位于视口中部安全区
    await rows.first().evaluate((el) => el.scrollIntoView({ block: "start" }));
    await page.evaluate(() => window.scrollBy(0, -140));
    await page.waitForTimeout(250);
    const inViewportIds = await page.evaluate(() => {
      const btns = Array.from(
        document.querySelectorAll<HTMLButtonElement>('button[data-test^="screening-maybe-"]'),
      );
      return btns
        .filter((el) => {
          const r = el.getBoundingClientRect();
          return r.width > 0 && r.height > 0 && r.top >= 140 && r.bottom <= window.innerHeight - 160;
        })
        .map((el) => el.getAttribute("data-test"))
        .filter((id): id is string => Boolean(id));
    });
    expect(inViewportIds.length, "当前视口内应存在至少 1 个待定按钮").toBeGreaterThanOrEqual(1);
    const targetIds = Array.from({ length: 5 }, (_, i) => inViewportIds[i % inViewportIds.length]!);
    const scrollBeforeDecision = await page.evaluate(() => window.scrollY);

    const perf4Decision = await measureActionPerf(page, async () => {
      for (const testid of targetIds) {
        await page.locator(`[data-test="${testid}"]`).click();
        await page.waitForTimeout(150);
      }
    });
    const scrollAfterDecision = await page.evaluate(() => window.scrollY);
    report.PERF_4A_PrismaPinInPlace = {
      ...perf4Decision,
      scrollDelta: Math.abs(scrollAfterDecision - scrollBeforeDecision),
    };
    expect(perf4Decision.cls).toBeLessThanOrEqual(0.005);
    expect(Math.abs(scrollAfterDecision - scrollBeforeDecision)).toBe(0);
    expect(perf4Decision.maxLongTaskMs).toBeLessThan(80);

    // 静止观察 FunnelChart 2s 粒子动画周期：重新进入干净页面（不含上面 5 条 toast 的退场计时），零 Layout Thrashing
    // 注：此处传入 { sampleRaf: false } 是为了避免 JS rAF 探针每帧唤醒 BeginMainFrame 并将正常运行的 CSS 动画同步至
    // CSSOM 产生人工 RecalcStyleCount 增量，从而精确隔离捕获 top/left 等触发布局重排的退化；
    // 同时也明确意识到 sampleRaf: false 不会在静止期测量纯 CSSOM 计算退化（如 var(--x)/cqw），
    // 且 Chromium 的 effect.getKeyframes() 返回已求值的 px 字符串、无法暴露原始 var(--x)/cqw/cqh；
    // 因此下文除用 document.getAnimations() 校验关键帧属性白名单（仅限 transform/opacity）外，
    // 直接递归校验 document.styleSheets 中的 funnel-* CSSKeyframesRule 原始文本及粒子 DOM 的 --x 变量。
    await page.goto(`/tasks/${taskId}/screening`, { waitUntil: "domcontentloaded" });
    await expect(page.locator('[data-test="screening-prisma"] .funnel-particle').first()).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.locator('[data-test="screening-mermaid-diagram"] svg')).toBeVisible({
      timeout: 20_000,
    });
    await page.waitForTimeout(250);

    const particleAnimCheck = await page.evaluate(() => {
      const anims = document.getAnimations().filter((anim) => {
        const effect = anim.effect as KeyframeEffect | null;
        const target = effect?.target;
        return target instanceof HTMLElement && target.classList.contains("funnel-particle");
      });
      const ALLOWED_KEYS = new Set([
        "opacity",
        "transform",
        "offset",
        "computedOffset",
        "easing",
        "composite",
      ]);
      const disallowedProps: string[] = [];
      for (const anim of anims) {
        const effect = anim.effect as KeyframeEffect;
        for (const kf of effect.getKeyframes()) {
          for (const key of Object.keys(kf)) {
            if (!ALLOWED_KEYS.has(key)) {
              disallowedProps.push(key);
            }
          }
        }
      }

      // ① 递归遍历 document.styleSheets（含 @layer / CSSGroupingRule / CSSMediaRule 等嵌套规则与顶层 KEYFRAMES_RULE）
      const funnelKeyframeRules: CSSKeyframesRule[] = [];
      const visitRuleList = (rules: CSSRuleList | undefined) => {
        if (!rules) return;
        for (const rule of Array.from(rules)) {
          if (rule.type === CSSRule.KEYFRAMES_RULE) {
            const kfRule = rule as CSSKeyframesRule;
            if (kfRule.name.startsWith("funnel-")) {
              funnelKeyframeRules.push(kfRule);
            }
            continue;
          }
          if ("cssRules" in rule) {
            visitRuleList((rule as CSSGroupingRule).cssRules);
          }
        }
      };
      for (const sheet of Array.from(document.styleSheets)) {
        try {
          visitRuleList(sheet.cssRules);
        } catch {
          // 跨域样式表无法访问 cssRules，跳过
        }
      }

      const forbiddenCssPattern = /(var\(\s*--|cqw|cqh|\btop\b|\bleft\b)/i;
      const disallowedKeyframeCss: string[] = [];
      for (const kfRule of funnelKeyframeRules) {
        if (forbiddenCssPattern.test(kfRule.cssText)) {
          disallowedKeyframeCss.push(`${kfRule.name}: ${kfRule.cssText}`);
        }
        for (const subRule of Array.from(kfRule.cssRules)) {
          if (forbiddenCssPattern.test(subRule.cssText)) {
            disallowedKeyframeCss.push(`${kfRule.name} -> ${subRule.cssText}`);
          }
        }
      }

      // ② 遍历 .funnel-particle DOM 节点，禁止内联或计算样式挂载 --x 动态变量驱动位移
      const particleNodes = Array.from(
        document.querySelectorAll<HTMLElement>('[data-test="screening-prisma"] .funnel-particle'),
      );
      const disallowedParticleVars: string[] = [];
      particleNodes.forEach((el, idx) => {
        const inlineX = el.style.getPropertyValue("--x").trim();
        const computedX = getComputedStyle(el).getPropertyValue("--x").trim();
        if (inlineX !== "" || computedX !== "") {
          disallowedParticleVars.push(
            `particle#${idx}(inline="${inlineX}",computed="${computedX}")`,
          );
        }
      });

      return {
        count: anims.length,
        disallowedProps,
        funnelKeyframesCount: funnelKeyframeRules.length,
        disallowedKeyframeCss,
        particleNodeCount: particleNodes.length,
        disallowedParticleVars,
      };
    });
    expect(particleAnimCheck.count, "FunnelChart 应存在运行中的粒子 CSS 动画").toBeGreaterThan(0);
    expect(particleAnimCheck.disallowedProps, "粒子关键帧仅允许 transform 与 opacity").toEqual([]);
    expect(
      particleAnimCheck.funnelKeyframesCount,
      "document.styleSheets 中应命中 >= 1 条 funnel-* CSSKeyframesRule",
    ).toBeGreaterThanOrEqual(1);
    expect(
      particleAnimCheck.disallowedKeyframeCss,
      "funnel-* @keyframes 禁止使用 var(--)/cqw/cqh/top/left",
    ).toEqual([]);
    expect(
      particleAnimCheck.particleNodeCount,
      "页面应存在 >= 1 个 .funnel-particle DOM 节点",
    ).toBeGreaterThanOrEqual(1);
    expect(
      particleAnimCheck.disallowedParticleVars,
      "粒子 DOM 节点禁止挂载或继承 --x CSS 变量",
    ).toEqual([]);

    const perf4Particles = await measureActionPerf(
      page,
      async () => {
        await page.waitForTimeout(2000);
      },
      { sampleRaf: false },
    );
    report.PERF_4B_FunnelParticles = {
      ...perf4Particles,
      particleAnimations: particleAnimCheck.count,
      funnelKeyframesCount: particleAnimCheck.funnelKeyframesCount,
    };
    expect(perf4Particles.deltaLayoutCount).toBe(0);
    expect(perf4Particles.deltaRecalcStyleCount).toBeLessThanOrEqual(2);

    // ===== PERF-5 (part B)：ChatShell 形态切换 3 轮 =====
    await page.goto("/tasks", { waitUntil: "domcontentloaded" });
    await expect(page.locator('[data-test="chat-fab"]')).toBeVisible({ timeout: 20_000 });
    const mainWidthBefore = await page.evaluate(
      () => document.querySelector("main")?.getBoundingClientRect().width ?? 0,
    );
    const mainWidthSamples: number[] = [];

    const perf5b = await measureActionPerf(page, async () => {
      for (let round = 0; round < 3; round += 1) {
        await page.locator('[data-test="chat-fab"]').click();
        await expect(page.locator('[data-test="chat-shell"]')).toBeVisible();
        mainWidthSamples.push(
          await page.evaluate(
            () => document.querySelector("main")?.getBoundingClientRect().width ?? 0,
          ),
        );

        await page.locator('[data-test="chat-variant-toggle"]').click();
        await page.waitForTimeout(120);
        mainWidthSamples.push(
          await page.evaluate(
            () => document.querySelector("main")?.getBoundingClientRect().width ?? 0,
          ),
        );

        await page.locator('[data-test="chat-variant-toggle"]').click();
        await page.waitForTimeout(120);
        await page.locator('[data-test="chat-close"]').click();
        await expect(page.locator('[data-test="chat-shell"]')).toHaveCount(0);
        mainWidthSamples.push(
          await page.evaluate(
            () => document.querySelector("main")?.getBoundingClientRect().width ?? 0,
          ),
        );
      }
    });
    const maxMainWidthDrift = mainWidthSamples.reduce(
      (max, w) => Math.max(max, Math.abs(w - mainWidthBefore)),
      0,
    );
    report.PERF_5B_ChatShellToggle = { ...perf5b, maxMainWidthDrift };
    expect(perf5b.cls).toBeLessThanOrEqual(0.01);
    expect(maxMainWidthDrift).toBeLessThan(2);

    console.log("PERF_MOTION_REPORT=" + JSON.stringify(report));
  });
});
