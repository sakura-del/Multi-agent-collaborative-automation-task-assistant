import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { apiBase, demoCreds } from "./support/env";
import { clearAuthSession, fetchAuthToken, seedAuthByApi } from "./support/auth";
import {
  measureAllElementsContrast,
  resolveElementColors,
  type ContrastMeasurement,
  type ContrastTarget,
} from "./support/contrast";

const API = apiBase();
const STREAM_RE = /\/api\/v1\/conversations\/[^/]+\/messages\/[^/]+\/stream(?:\?|$)/;

interface ViewportSpec {
  name: "mobile" | "tablet" | "desktop";
  width: number;
  height: number;
}

const VIEWPORTS: ViewportSpec[] = [
  { name: "mobile", width: 393, height: 851 },
  { name: "tablet", width: 768, height: 900 },
  { name: "desktop", width: 1440, height: 900 },
];

const THEMES = ["light", "dark"] as const;
type ThemeMode = (typeof THEMES)[number];

interface SeedRefs {
  completedTaskId: string;
  workspaceId: string;
  extractionTableId: string;
}

async function resolveSeedRefs(request: APIRequestContext): Promise<SeedRefs> {
  const { account, password } = demoCreds();
  const token = (await fetchAuthToken(request, account, password)).access_token;
  const headers = { Authorization: `Bearer ${token}` };

  const tasksResp = await request.get(`${API}/tasks?limit=50`, { headers });
  expect(tasksResp.ok()).toBeTruthy();
  const tasks = (await tasksResp.json()) as { task_id: string; status: string; title: string }[];
  const completed =
    tasks.find((t) => t.title.includes("[E2E-Seed]") && t.status === "completed") ??
    tasks.find((t) => t.status === "completed");
  expect(completed, "种子库应包含已完成综述任务").toBeTruthy();

  const wsResp = await request.get(`${API}/workspaces`, { headers });
  expect(wsResp.ok()).toBeTruthy();
  const workspaces = (await wsResp.json()) as { workspace_id: string; slug: string }[];
  const ws =
    workspaces.find((w) => w.slug === "e2e-demo-workspace") ?? workspaces[0];
  expect(ws, "种子库应包含演示工作区").toBeTruthy();

  const extResp = await request.get(`${API}/extractions`, { headers });
  expect(extResp.ok()).toBeTruthy();
  const extData = (await extResp.json()) as { items?: { id: string; title: string }[] };
  const extList = extData.items ?? [];
  const ext = extList.find((e) => e.title.includes("E2E")) ?? extList[0];
  expect(ext, "种子库应包含抽取表").toBeTruthy();

  return {
    completedTaskId: completed!.task_id,
    workspaceId: ws!.workspace_id,
    extractionTableId: ext!.id,
  };
}

async function primeTheme(page: Page, theme: ThemeMode): Promise<void> {
  const url = page.url();
  if (!url || url === "about:blank") return;
  await page.evaluate((mode) => {
    localStorage.setItem("vp:theme", mode);
    document.documentElement.classList.toggle("dark", mode === "dark");
    document.documentElement.style.colorScheme = mode;
  }, theme);
}

async function applyTheme(page: Page, theme: ThemeMode): Promise<void> {
  const changed = await page.evaluate((mode) => {
    const prev = document.documentElement.classList.contains("dark");
    const next = mode === "dark";
    localStorage.setItem("vp:theme", mode);
    document.documentElement.classList.toggle("dark", next);
    document.documentElement.style.colorScheme = mode;
    return prev !== next;
  }, theme);
  await expect
    .poll(() => page.evaluate(() => document.documentElement.classList.contains("dark")))
    .toBe(theme === "dark");
  // 若主题类确实发生了切换，等待 CSS 颜色过渡（transition-colors 150ms）收敛，避免在渐变中间帧采样对比度
  await page.evaluate(async (hadToggle) => {
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
    const transitions = document
      .getAnimations()
      .filter((anim) => anim instanceof CSSTransition);
    await Promise.all(transitions.map((t) => t.finished.catch(() => undefined)));
    if (hadToggle) {
      await new Promise<void>((resolve) => setTimeout(resolve, 180));
    }
  }, changed);
}

async function assertNoHorizontalOverflow(page: Page, contextLabel: string): Promise<void> {
  const result = await page.evaluate(() => {
    window.scrollTo(0, 0);
    document.documentElement.scrollLeft = 0;
    document.body.scrollLeft = 0;
    document.querySelectorAll<HTMLElement>(".app-nav__links").forEach((el) => {
      el.scrollLeft = 0;
    });

    const vw = window.innerWidth;
    const sw = document.documentElement.scrollWidth;
    const offenders = Array.from(document.querySelectorAll("body *"))
      .map((el) => {
        const style = getComputedStyle(el);
        if (style.display === "none" || style.visibility === "hidden") return null;
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return null;
        if (r.right <= vw + 1) return null;
        return {
          tag: el.tagName,
          test: el.getAttribute("data-test"),
          cls: typeof (el as HTMLElement).className === "string" ? (el as HTMLElement).className.slice(0, 80) : "",
          right: Math.round(r.right),
          width: Math.round(r.width),
        };
      })
      .filter((x): x is NonNullable<typeof x> => x !== null)
      .slice(0, 5);

    return { vw, sw, overflow: sw - vw, offenders };
  });

  expect(
    result.overflow,
    `${contextLabel} 横向溢出 ${result.overflow}px (scrollWidth=${result.sw}, innerWidth=${result.vw}), 溢出元素=${JSON.stringify(result.offenders)}`,
  ).toBeLessThanOrEqual(1);
}

async function assertElementVisibleAndUnoccluded(
  page: Page,
  selector: string,
  label: string,
  minSize = 24,
): Promise<void> {
  const loc = page.locator(selector).first();
  await expect(loc, `${label} 应可见`).toBeVisible({ timeout: 15_000 });
  const diag = await loc.evaluate(
    (el, min) => {
      const r = el.getBoundingClientRect();
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const cx = Math.min(vw - 1, Math.max(1, r.left + r.width / 2));
      const cy = Math.min(vh - 1, Math.max(1, r.top + r.height / 2));
      const hit = document.elementFromPoint(cx, cy);
      const hitInside = hit === el || (hit !== null && el.contains(hit)) || (hit !== null && hit.contains(el));
      return {
        x: r.x,
        y: r.y,
        width: r.width,
        height: r.height,
        vw,
        vh,
        hitInside,
        hitTag: hit?.tagName ?? null,
        hitTest: hit?.getAttribute("data-test") ?? null,
        min,
      };
    },
    minSize,
  );
  expect(diag.width, `${label} 宽度过小`).toBeGreaterThanOrEqual(minSize);
  expect(diag.height, `${label} 高度过小`).toBeGreaterThanOrEqual(minSize);
  expect(diag.x, `${label} 左越界`).toBeGreaterThanOrEqual(-1);
  expect(diag.y, `${label} 上越界`).toBeGreaterThanOrEqual(-1);
  expect(diag.x + diag.width, `${label} 右越界`).toBeLessThanOrEqual(diag.vw + 1);
  expect(diag.y + diag.height, `${label} 下越界`).toBeLessThanOrEqual(diag.vh + 1);
  expect(
    diag.hitInside,
    `${label} 中心点被遮挡 (命中 ${diag.hitTag} data-test=${diag.hitTest})`,
  ).toBe(true);
}

const COMMON_CONTRAST_TARGETS: ContrastTarget[] = [
  { label: "body", selector: "body" },
  { label: "h1", selector: "main h1, section h1, h1" },
  { label: "muted", selector: "main .text-muted-foreground, section .text-muted-foreground" },
  { label: "status-badge", selector: '[data-test="status-badge"]' },
  { label: "text-success", selector: ".text-success" },
  { label: "text-warning", selector: ".text-warning" },
  { label: "text-destructive", selector: ".text-destructive" },
  { label: "topbar-kbd", selector: '[data-test="open-command-palette"] kbd' },
  { label: "sidebar-kbd", selector: "aside kbd" },
];

test.describe("多视口 × 双主题全站自动化走查 Matrix", () => {
  test.setTimeout(360_000);

  test("公开页与工作台/后台全站 27 具名路由在 3 视口 × 2 主题下零溢出/零遮挡/对比度达标", async ({
    page,
    request,
  }) => {
    const refs = await resolveSeedRefs(request);
    const isMobileProject = test.info().project.name === "mobile-chrome";
    // mobile-chrome 已有 hasTouch:true，专注真实触控 393×851 双主题；chromium 遍历完整 3 档视口
    const viewportsToRun = isMobileProject ? [VIEWPORTS[0]!] : VIEWPORTS;

    const publicRoutes = [
      { path: "/", label: "landing-root", ready: "h1" },
      { path: "/landing", label: "landing-preview", ready: "h1" },
      { path: "/login", label: "login", ready: '[data-test="auth-title"]' },
      { path: "/register", label: "register", ready: '[data-test="auth-title"]' },
      { path: "/verify-email?token=probe", label: "verify-email", ready: "h1" },
      { path: "/password-reset", label: "password-reset", ready: "h1" },
      { path: "/auth/callback", label: "oauth-callback", ready: '[role="alert"]' },
    ];

    const workspaceRoutes = [
      { path: "/tasks", label: "tasks", ready: '[data-test="new-task"]' },
      { path: "/chat", label: "chat", ready: '[data-test="chat-input"]' },
      {
        path: `/tasks/${refs.completedTaskId}?tab=plan`,
        label: "task-detail-plan",
        ready: '[data-test="tab-plan"]',
      },
      {
        path: `/tasks/${refs.completedTaskId}?tab=execution`,
        label: "task-detail-execution",
        ready: '[data-test="trace-waterfall"]',
      },
      {
        path: `/tasks/${refs.completedTaskId}?tab=logs`,
        label: "task-detail-logs",
        ready: '[data-test="task-log"]',
      },
      {
        path: `/tasks/${refs.completedTaskId}?tab=result`,
        label: "task-detail-result",
        ready: '[data-test="result-markdown"]',
      },
      {
        path: `/tasks/${refs.completedTaskId}/screening`,
        label: "task-screening",
        ready: '[data-test="screening-prisma"]',
      },
      { path: "/knowledge", label: "knowledge", ready: "h1" },
      { path: "/extractions", label: "extractions-list", ready: "h1" },
      {
        path: `/extractions/${refs.extractionTableId}`,
        label: "extractions-detail",
        ready: "h1",
      },
      { path: "/styles", label: "styles", ready: '[data-test^="style-row-"]' },
      { path: "/templates", label: "templates", ready: '[data-test^="template-row-"]' },
      { path: "/references/import", label: "reference-import", ready: '[data-test="import-form"]' },
      {
        path: "/references/library",
        label: "reference-library",
        ready: '[data-test="library-summary"]',
      },
      { path: "/observability", label: "observability", ready: '[data-test="observability-total"]' },
      { path: "/workspaces", label: "workspaces", ready: "h1" },
      {
        path: `/workspaces/${refs.workspaceId}`,
        label: "workspace-detail",
        ready: '[data-test="workspace-detail-name"]',
      },
      { path: "/settings/models", label: "settings-models", ready: '[data-test="model-create-open"]' },
      { path: "/settings/api-keys", label: "settings-api-keys", ready: "h1" },
      { path: "/settings/plan", label: "settings-plan", ready: '[data-test="plan-current"]' },
      { path: "/account/verify", label: "account-verify", ready: "h1" },
      { path: "/admin", label: "admin-console", ready: "h1" },
      { path: "/admin/byok", label: "admin-byok", ready: "h1" },
      { path: "/design/timeline", label: "design-timeline", ready: "h1" },
    ];

    const allContrastRecords: Array<ContrastMeasurement & { route: string; theme: string; viewport: string }> = [];

    // 先检查公开页与落地页真实主题按钮切换（三态 + system）
    await clearAuthSession(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/landing", { waitUntil: "domcontentloaded" });
    await expect(page.locator('[data-test="landing-theme-toggle"]')).toBeVisible({ timeout: 20_000 });
    await page.locator('[data-test="landing-theme-toggle"]').click();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.classList.contains("dark")))
      .toBe(true);
    await page.locator('[data-test="landing-theme-mode-select"]').click();
    await page.locator('[data-test="landing-theme-mode-light"]').click();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.classList.contains("dark")))
      .toBe(false);
    await page.emulateMedia({ colorScheme: "dark" });
    await page.locator('[data-test="landing-theme-mode-select"]').click();
    await page.locator('[data-test="landing-theme-mode-system"]').click();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.classList.contains("dark")))
      .toBe(true);
    await page.emulateMedia({ colorScheme: "light" });

    for (const vp of viewportsToRun) {
      await page.setViewportSize({ width: vp.width, height: vp.height });

      for (const theme of THEMES) {
        // ===== A. 公开页全路由（未登录态） =====
        await clearAuthSession(page);
        await primeTheme(page, theme);
        for (const r of publicRoutes) {
          await page.goto(r.path, { waitUntil: "domcontentloaded" });
          await expect(page.locator(r.ready).first()).toBeVisible({
            timeout: 20_000,
          });
          await applyTheme(page, theme);
          if (r.label === "login") {
            await assertNoHorizontalOverflow(page, `${vp.name}/${theme}/${r.label}-otp`);
            await page.locator('[data-test="toggle-password-login"]').click();
            await expect(page.locator("#account")).toBeVisible();
            await assertNoHorizontalOverflow(page, `${vp.name}/${theme}/${r.label}-password`);
          } else {
            await assertNoHorizontalOverflow(page, `${vp.name}/${theme}/${r.label}`);
          }

          const publicTargets: ContrastTarget[] = [...COMMON_CONTRAST_TARGETS];
          if (r.label === "landing-root" || r.label === "landing-preview") {
            publicTargets.push(
              {
                label: "landing-exclude-btn",
                selector: '[data-test^="demo-verdict-"][data-test$="-exclude"]',
              },
              {
                label: "landing-trust-item",
                selector: '[data-test="landing-trust-bar"] li',
              },
              {
                label: "landing-trust-label",
                selector: '[data-test="landing-trust-bar"] p',
              },
            );
          }

          const measurements = await measureAllElementsContrast(page, publicTargets);
          for (const m of measurements) {
            if (m.found && m.visible) {
              allContrastRecords.push({ ...m, route: r.label, theme, viewport: vp.name });
              expect(
                m.passed,
                `${vp.name}/${theme}/${r.label} [${m.label}] 对比度 ${m.ratio} < ${m.threshold} (fg=${m.resolvedFgHex}, bg=${m.resolvedBgHex}, text="${m.textSample}")`,
              ).toBe(true);
            }
          }
        }

        // ===== B. 工作台与管理后台全路由（admin 登录态） =====
        const { account, password } = demoCreds();
        await primeTheme(page, theme);
        await seedAuthByApi(page, request, account, password, "/tasks");

        for (const r of workspaceRoutes) {
          await page.goto(r.path, { waitUntil: "domcontentloaded" });
          await expect(page.locator(r.ready).first()).toBeVisible({ timeout: 30_000 });
          await applyTheme(page, theme);
          await page.waitForTimeout(120);

          await assertNoHorizontalOverflow(page, `${vp.name}/${theme}/${r.label}`);

          // 非 /chat 与非 /design/timeline 页面必须可见且无遮挡 chat-fab
          if (r.label !== "chat" && r.label !== "design-timeline") {
            await assertElementVisibleAndUnoccluded(
              page,
              '[data-test="chat-fab"]',
              `${vp.name}/${theme}/${r.label} chat-fab`,
              32,
            );
          }

          // 针对特定路由的几何、遮挡与对比度深查
          if (r.label === "tasks") {
            if (vp.width < 768) {
              await assertElementVisibleAndUnoccluded(
                page,
                '[data-test="nav-mobile-trigger"]',
                `${vp.name}/${theme} nav-mobile-trigger`,
                28,
              );
              await page.locator('[data-test="nav-mobile-trigger"]').click();
              await expect(page.locator('[data-test="mobile-open-command"]')).toBeVisible();
              await expect(page.locator('[role="dialog"] [data-test="global-prompt"]')).toBeVisible();
              await page.keyboard.press("Escape");
            } else {
              await assertElementVisibleAndUnoccluded(
                page,
                '[data-test="open-command-palette"]',
                `${vp.name}/${theme} open-command-palette`,
                24,
              );
              await assertElementVisibleAndUnoccluded(
                page,
                '[data-test="theme-toggle"]',
                `${vp.name}/${theme} theme-toggle`,
                24,
              );
            }

            // 展开新建任务表单不溢出
            await page.locator('[data-test="new-task"]').click();
            await expect(page.locator('[data-test="topic"]')).toBeVisible();
            await assertNoHorizontalOverflow(page, `${vp.name}/${theme}/tasks-create-form`);
            await page.locator('[data-test="new-task"]').click();

            // 仅在 1440/393 检查一次 CommandPalette 边界与真实 theme-toggle 切换
            if (vp.name !== "tablet" && theme === "light") {
              await page.locator('[data-test="open-command-palette"]').click();
              const palette = page.locator('[data-test="command-palette"]');
              await expect(palette).toBeVisible();
              const pBox = await palette.boundingBox();
              expect(pBox!.x).toBeGreaterThanOrEqual(0);
              expect(pBox!.x + pBox!.width).toBeLessThanOrEqual(vp.width + 1);
              expect(pBox!.y + pBox!.height).toBeLessThanOrEqual(vp.height + 1);
              await page.keyboard.press("Escape");
              await expect(palette).toHaveCount(0);
            }
          }

          if (r.label === "task-detail-result") {
            // 打开批注抽屉检查不超出视口并可 Esc 关闭
            await page.locator('[data-test="toggle-annotation"]').click();
            const drawer = page.locator('[data-test="annotation-drawer"]');
            await expect(drawer).toBeVisible({ timeout: 15_000 });
            // 等待 MotionDrawer 滑入动画落位后再检视口外边界
            await expect
              .poll(async () => {
                const b = await drawer.boundingBox();
                return b ? b.x + b.width : 9999;
              })
              .toBeLessThanOrEqual(vp.width + 1);
            const dBox = await drawer.boundingBox();
            expect(dBox!.x).toBeGreaterThanOrEqual(-1);
            expect(dBox!.x + dBox!.width).toBeLessThanOrEqual(vp.width + 1);
            expect(dBox!.y + dBox!.height).toBeLessThanOrEqual(vp.height + 1);

            // W2 回归：打开抽屉时 chat-fab 坐标点不得穿透置于抽屉/遮罩之上
            const fabEl = page.locator('[data-test="chat-fab"]');
            if ((await fabEl.count()) > 0) {
              const fabBox = await fabEl.boundingBox();
              if (fabBox && fabBox.width > 0 && fabBox.height > 0) {
                const hitChatFab = await page.evaluate(
                  ({ x, y }) => {
                    const hit = document.elementFromPoint(x, y);
                    const fab = document.querySelector('[data-test="chat-fab"]');
                    return Boolean(hit && fab && (hit === fab || fab.contains(hit)));
                  },
                  { x: fabBox.x + fabBox.width / 2, y: fabBox.y + fabBox.height / 2 },
                );
                expect(hitChatFab).toBe(false);
              }
            }

            await page.keyboard.press("Escape");
            await expect(drawer).toHaveCount(0);
          }

          if (r.label === "reference-library" && vp.width < 768) {
            // 移动端触控热区不得 opacity === 0 隐身
            const editBtn = page.locator('[data-test^="library-edit-"]').first();
            if ((await editBtn.count()) > 0) {
              const btnDiag = await editBtn.evaluate((el) => {
                const s = getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return { opacity: s.opacity, w: r.width, h: r.height };
              });
              expect(Number(btnDiag.opacity)).toBeGreaterThan(0);
              expect(Math.max(btnDiag.w, btnDiag.h)).toBeGreaterThanOrEqual(28);
            }
          }

          // 汇总页面专用对比度目标
          const extraTargets: ContrastTarget[] = [...COMMON_CONTRAST_TARGETS];
          if (r.label === "task-detail-plan") {
            extraTargets.push(
              { label: "agent-plan-kind-pill", selector: '[data-test="agent-plan-kind-pill"]' },
              { label: "agent-plan-status", selector: '[data-test="agent-plan-status"]' },
            );
          } else if (r.label === "extractions-detail") {
            extraTargets.push(
              { label: "cell-not-reported", selector: '[data-test="cell-not-reported"]' },
              { label: "cell-not-reported-scope", selector: '[data-test="cell-not-reported"] span' },
            );
          } else if (r.label === "observability") {
            extraTargets.push(
              { label: "obs-total", selector: '[data-test="observability-total"]' },
              { label: "obs-denom", selector: '[data-test="observability-success-denominator"]' },
            );
          } else if (r.label === "settings-plan") {
            extraTargets.push(
              { label: "plan-period-end", selector: '[data-test="plan-period-end"]' },
              { label: "plan-period-key", selector: '[data-test="plan-period-key"]' },
            );
          } else if (r.label === "styles") {
            extraTargets.push({
              label: "style-preview-p",
              selector: '[data-test="style-preview-gbt7714-2025-numeric"] p',
            });
          } else if (r.label === "settings-models") {
            extraTargets.push({
              label: "context-meter",
              selector: '[data-test="context-meter"] span',
            });
          }

          const measurements = await measureAllElementsContrast(page, extraTargets);
          for (const m of measurements) {
            if (m.found && m.visible) {
              allContrastRecords.push({ ...m, route: r.label, theme, viewport: vp.name });
              expect(
                m.passed,
                `${vp.name}/${theme}/${r.label} [${m.label}] 对比度 ${m.ratio} < ${m.threshold} (fg=${m.resolvedFgHex}, bg=${m.resolvedBgHex}, text="${m.textSample}")`,
              ).toBe(true);
            }
          }
        }
      }
    }

    const minRatio = allContrastRecords.reduce((min, r) => Math.min(min, r.ratio), 99);
    console.log(
      "UX_MATRIX_CONTRAST_SUMMARY=" +
        JSON.stringify({
          totalChecks: allContrastRecords.length,
          passedChecks: allContrastRecords.filter((r) => r.passed).length,
          minRatio,
          minSample: allContrastRecords.find((r) => r.ratio === minRatio),
        }),
    );
  });

  test("双主题下 ChatShell 几何、触控热区与 ChatMessageItem 代码块/表格非透明验证", async ({
    page,
    request,
  }) => {
    const ssePayload = [
      `event: start\ndata: ${JSON.stringify({ message_id: "stub" })}\n\n`,
      `event: delta\ndata: ${JSON.stringify({
        text:
          "## 代码与表格验证\n\n" +
          "```ts\nconst ok: boolean = true;\nconsole.log(ok);\n```\n\n" +
          "| 字段 | 含义 |\n| --- | --- |\n| status | 就绪 |\n\n" +
          "参考 [文档链接](https://example.com) 。",
      })}\n\n`,
      `event: done\ndata: ${JSON.stringify({ message_id: "stub" })}\n\n`,
    ].join("");

    await page.route(STREAM_RE, async (route) => {
      await route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
        body: ssePayload,
      });
    });

    const { account, password } = demoCreds();
    await seedAuthByApi(page, request, account, password, "/tasks");

    // 验证主题按钮真实切换后进 /tasks 测 ChatShell floating & dock 几何
    for (const vp of VIEWPORTS) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      for (const theme of THEMES) {
        await applyTheme(page, theme);
        await page.evaluate(() => {
          localStorage.setItem("vp:chat:variant", "floating");
          localStorage.setItem("vp:chat:open", "1");
        });
        await page.reload({ waitUntil: "domcontentloaded" });

        const shell = page.locator('[data-test="chat-shell"]');
        await expect(shell).toBeVisible({ timeout: 20_000 });
        const floatGeom = await shell.evaluate((el) => {
          const s = getComputedStyle(el);
          const r = el.getBoundingClientRect();
          return {
            position: s.position,
            x: r.x,
            y: r.y,
            width: r.width,
            height: r.height,
            vw: window.innerWidth,
            vh: window.innerHeight,
          };
        });
        expect(floatGeom.position).toBe("fixed");
        expect(floatGeom.width).toBeLessThanOrEqual(Math.min(380, vp.width - 24) + 1);
        expect(floatGeom.x).toBeGreaterThanOrEqual(-1);
        expect(floatGeom.x + floatGeom.width).toBeLessThanOrEqual(vp.width + 1);
        expect(floatGeom.y).toBeGreaterThanOrEqual(-1);
        expect(floatGeom.height).toBeLessThanOrEqual(vp.height + 1);

        // 切到 dock 态
        await page.locator('[data-test="chat-variant-toggle"]').click();
        await page.waitForTimeout(150);
        const dockGeom = await shell.evaluate((el) => {
          const s = getComputedStyle(el);
          const r = el.getBoundingClientRect();
          const mainR = document.querySelector("main")?.getBoundingClientRect();
          return {
            position: s.position,
            width: r.width,
            mainWidth: mainR?.width ?? 0,
          };
        });
        expect(dockGeom.position).toBe("fixed");
        if (vp.width < 768) {
          expect(dockGeom.width).toBeGreaterThanOrEqual(vp.width - 2);
        } else {
          expect(dockGeom.width).toBeGreaterThanOrEqual(438);
          expect(dockGeom.width).toBeLessThanOrEqual(442);
          expect(dockGeom.mainWidth).toBeGreaterThan(300);
        }
      }
    }

    // 在桌面视口发送一条含行内代码/链接的用户消息，接收含代码块与表格的助手响应
    await page.setViewportSize({ width: 1280, height: 860 });
    await page.goto("/chat", { waitUntil: "domcontentloaded" });
    await page.locator('[data-test="chat-toggle-list"]').click();
    await page.locator('[data-test="chat-new-conversation"]').click();
    const input = page.locator('[data-test="chat-input"]');
    await input.fill("测试 `inline_code` 与 [链接](https://example.com)");
    await input.press("Enter");

    await expect(page.locator('[data-test="chat-stop"]')).toHaveCount(0, { timeout: 30_000 });
    const assistantPre = page.locator('[data-test="chat-body-assistant"] pre').first();
    await expect(assistantPre).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('[data-test="chat-body-assistant"] td').first()).toBeVisible();

    for (const theme of THEMES) {
      await applyTheme(page, theme);
      await page.waitForTimeout(80);

      const preColors = await resolveElementColors(page, '[data-test="chat-body-assistant"] pre');
      const bubbleColors = await resolveElementColors(page, '[data-test="chat-body-assistant"]');
      expect(preColors.bgRgba[3], `${theme} 代码块背景不应透明`).toBeGreaterThan(0);
      expect(preColors.compositeBgHex, `${theme} 代码块底色应与气泡可区分`).not.toBe(
        bubbleColors.compositeBgHex,
      );

      const codeColors = await resolveElementColors(
        page,
        '[data-test="chat-body-assistant"] pre code',
      );
      expect(codeColors.ratio, `${theme} 代码块文字对比度应 >= 4.5`).toBeGreaterThanOrEqual(4.5);

      const tdColors = await resolveElementColors(page, '[data-test="chat-body-assistant"] td');
      expect(tdColors.borderTopWidth, `${theme} 表格边框宽度应 >= 1`).toBeGreaterThanOrEqual(1);
      expect(tdColors.borderTopRgba[3], `${theme} 表格边框不应透明`).toBeGreaterThan(0);

      // 用户消息内代码/链接在主色气泡上对比度 >= 4.5
      const userCodeColors = await resolveElementColors(
        page,
        '[data-test="chat-body-user"] code',
      );
      expect(userCodeColors.ratio, `${theme} 用户气泡行内代码对比度应 >= 4.5`).toBeGreaterThanOrEqual(
        4.5,
      );
      const userLinkColors = await resolveElementColors(page, '[data-test="chat-body-user"] a');
      expect(userLinkColors.ratio, `${theme} 用户气泡链接对比度应 >= 4.5`).toBeGreaterThanOrEqual(
        4.5,
      );
    }
  });
});
