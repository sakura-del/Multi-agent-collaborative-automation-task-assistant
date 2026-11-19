import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { demoPassword, E2E_DEMO_ACCOUNT } from "./credentials";

/**
 * 会话内核 E2E（Issue #8 P0 收尾，2026-11-19）。
 *
 * 覆盖两条线，职责分离：
 *
 * 1. **真实链路**（1 例）：FAB → 输入 → Enter → SSE 200 → 流式 → 完成 → 刷新恢复。
 *    这一例证明前后端契约真的通（平台模型、消息落库、重连加载）。
 * 2. **流式状态机**（3 例）：用 `page.route` 拦截 SSE，返回确定性的
 *    多 delta 帧，验证「流式中 / 完成 / 停止 / 分支」四种 UI 终态。
 *
 * 为什么状态机要打桩：平台模型有 429 与数十秒延迟，真实链路的时序无法断言
 * 「停止按钮何时出现」；桩响应让状态断言零抖动，且**不消耗 token**。
 * 真实链路单独跑一次，两者互补而不是互相替代。
 *
 * 稳定等待原则（针对 CDP 曾僵死）：
 * - 只用 web-first 断言（`toBeVisible` / `toHaveCount` / `expect.poll`），
 *   不用固定 sleep 等结果；
 * - 每条断言都有显式超时上限，失败信息能定位到具体 data-test；
 * - 登录单独做了限流重试（auth_login 默认 10 次/分钟）。
 */

const API = "/api/v1";
/** 会话标题取首条消息前 40 字（后端行为），用它做清理标记 */
const MARKER = "[E2E-chat]";
const STREAM_RE = /\/api\/v1\/conversations\/[^/]+\/messages\/[^/]+\/stream$/;
const FIXTURE = resolve(dirname(fileURLToPath(import.meta.url)), "fixtures/sample.txt");

/** 登录（含限流重试：命中 429 时等待窗口恢复再提交）。 */
async function login(page: Page): Promise<void> {
  await page.goto("/login");
  await page.locator("#account").fill(E2E_DEMO_ACCOUNT);
  await page.locator("#password").fill(demoPassword());
  await page.locator('form button[type="submit"]').click();

  const tasksLink = page.getByRole("link", { name: /任务/ });
  const expandNav = page.locator('[data-test="nav-expand"]');
  const mobileNav = page.locator('[data-test="nav-mobile-trigger"]');
  let expanded = false;
  for (let attempt = 0; attempt < 8; attempt += 1) {
    if (await tasksLink.isVisible().catch(() => false)) {
      return;
    }
    // 移动视口（Pixel 5）侧栏默认折叠：任务链接不在 DOM 可见区，
    // 必须先点「展开导航」。桌面端该按钮可能不存在，点击失败即忽略。
    if (!expanded) {
      const trigger = (await expandNav.isVisible().catch(() => false))
        ? expandNav
        : (await mobileNav.isVisible().catch(() => false))
          ? mobileNav
          : null;
      if (trigger) {
        await trigger.click().catch(() => undefined);
        expanded = true;
      }
    }
    const limited = await page
      .locator("text=请求过于频繁")
      .isVisible()
      .catch(() => false);
    await page.waitForTimeout(limited ? 8_000 : 1_000);
    if (limited) {
      await page
        .locator('form button[type="submit"]')
        .click()
        .catch(() => undefined);
    }
  }
  await expect(tasksLink).toBeVisible({ timeout: 30_000 });
}

/** 清理本 spec 残留会话（用例失败也不会污染目标库）。 */
async function cleanup(request: APIRequestContext): Promise<void> {
  const loginResp = await request.post(`${API}/auth/login`, {
    data: { account: E2E_DEMO_ACCOUNT, password: demoPassword() },
  });
  if (!loginResp.ok()) {
    // 清理失败不阻塞用例（登录限流时宁可留残留，也不能让清理把测试拖红）
    return;
  }
  const token = (await loginResp.json()).access_token as string;
  const headers = { Authorization: `Bearer ${token}` };
  const listResp = await request.get(`${API}/conversations`, { headers });
  if (!listResp.ok()) {
    return;
  }
  const conversations = (await listResp.json()) as { id: string; title: string }[];
  for (const conversation of conversations) {
    if (conversation.title.startsWith(MARKER)) {
      await request.delete(`${API}/conversations/${conversation.id}`, { headers });
    }
  }
}

async function openChat(page: Page): Promise<void> {
  await expect(page.locator('[data-test="chat-fab"]')).toBeVisible({ timeout: 30_000 });
  await page.locator('[data-test="chat-fab"]').click();
  await expect(page.locator('[data-test="chat-shell"]')).toBeVisible();
}

/** 新建会话（先展开会话列表，点「新会话」后列表自动收起）。 */
async function newConversation(page: Page): Promise<void> {
  await page.locator('[data-test="chat-toggle-list"]').click();
  await expect(page.locator('[data-test="chat-conversation-list"]')).toBeVisible();
  await page.locator('[data-test="chat-new-conversation"]').click();
  await expect(page.locator('[data-test="chat-conversation-list"]')).toBeHidden();
}

async function send(page: Page, text: string): Promise<void> {
  const input = page.locator('[data-test="chat-input"]');
  await expect(input).toBeVisible();
  await input.fill(text);
  // BePromptInput 的 submitOnEnter：Enter 直提交，Shift+Enter 换行
  await input.press("Enter");
}

/** 等待所有流式结束：停止按钮与流式光标都消失（比等某个文字更稳）。 */
async function waitStreamingSettled(page: Page): Promise<void> {
  await expect(page.locator('[data-test="chat-stop"]')).toHaveCount(0, { timeout: 180_000 });
  await expect(page.locator('[data-test="chat-streaming-cursor"]')).toHaveCount(0, {
    timeout: 60_000,
  });
}

function sseBody(chunks: string[]): string {
  const frame = (event: string, data: unknown) =>
    `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  return [
    frame("start", { message_id: "stub" }),
    ...chunks.map((text) => frame("delta", { text })),
    frame("done", { message_id: "stub" }),
  ].join("");
}

test.describe("会话内核：真实链路", () => {
  // 平台模型单轮 5~40s；加上刷新恢复，留足余量
  test.setTimeout(300_000);
  test.describe.configure({ retries: 1 });

  test.beforeAll(async ({ request }) => {
    await cleanup(request);
  });
  test.afterAll(async ({ request }) => {
    await cleanup(request);
  });

  test("FAB → 输入 → SSE → 完成 → 刷新后历史恢复", async ({ page }) => {
    // 真实链路要调平台模型（有 429/延迟）。没有可用模型的测试环境
    // （如本地隔离栈）默认跳过；需要时显式 E2E_REAL_LLM=1。
    test.skip(!process.env.E2E_REAL_LLM, "需平台模型可用：设 E2E_REAL_LLM=1 开启");

    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await login(page);
    await page.goto("/tasks");
    await openChat(page);
    await newConversation(page);

    const prompt = `${MARKER} 用两句话说明检索增强生成（RAG）的核心思想`;
    // 先挂响应监听再发送：SSE 响应头到达即 resolve（不等正文）
    const sseResponse = page.waitForResponse(
      (response) => STREAM_RE.test(response.url()) && response.request().method() === "GET",
      { timeout: 120_000 }
    );
    await send(page, prompt);

    // 契约 ①：SSE 真的建立（状态码 + 事件流类型）
    const response = await sseResponse;
    expect(response.status()).toBe(200);
    expect(response.headers()["content-type"] ?? "").toContain("text/event-stream");

    // 契约 ②：用户消息立即上屏，且走的是既有 data-test
    const userBubble = page.locator('[data-test="chat-message-user"]');
    await expect(userBubble).toHaveCount(1);
    await expect(page.locator('[data-test="chat-body-user"]')).toContainText("RAG");

    // 契约 ③：流式结束后进入完成态——停止按钮/光标消失，助手正文非空
    await waitStreamingSettled(page);
    const assistantBodies = page.locator('[data-test="chat-body-assistant"]');
    await expect(assistantBodies).toHaveCount(1, { timeout: 60_000 });
    await expect
      .poll(async () => (await assistantBodies.first().innerText()).trim().length, {
        timeout: 60_000,
      })
      .toBeGreaterThan(10);

    // 完成态不应残留错误条
    await expect(page.locator('[data-test="chat-error"]')).toHaveCount(0);
    await expect(page.locator('[data-test="chat-message-error"]')).toHaveCount(0);

    // 契约 ④：刷新后历史会话恢复（store 挂载 → 拉列表 → 打开最近会话）
    await page.reload();
    await expect(page.locator('[data-test="chat-shell"]')).toBeVisible({ timeout: 30_000 });
    await expect
      .poll(async () => {
        const body = page.locator('[data-test="chat-body-assistant"]').first();
        if ((await body.count()) === 0) return 0;
        return (await body.innerText()).trim().length;
      }, { timeout: 60_000 })
      .toBeGreaterThan(10);
    await expect(page.locator('[data-test="chat-body-user"]')).toContainText("RAG");

    expect(pageErrors).toEqual([]);
  });
});

test.describe("会话内核：流式状态机（SSE 桩）", () => {
  test.setTimeout(120_000);

  test.beforeAll(async ({ request }) => {
    await cleanup(request);
  });
  test.afterAll(async ({ request }) => {
    await cleanup(request);
  });

  /** 拦截 SSE：可延迟（模拟慢速生成）、可每次调用返回不同正文（分支区分）。 */
  async function stubStream(
    page: Page,
    options: { delayMs?: number; bodies: string[] }
  ): Promise<void> {
    let call = 0;
    await page.route(STREAM_RE, async (route) => {
      const body = options.bodies[Math.min(call, options.bodies.length - 1)] ?? "";
      call += 1;
      if (options.delayMs) {
        await new Promise((resolve) => setTimeout(resolve, options.delayMs));
      }
      try {
        await route.fulfill({
          status: 200,
          headers: {
            "content-type": "text/event-stream",
            "cache-control": "no-cache",
          },
          body,
        });
      } catch {
        // 客户端已 abort（「停止」用例的正常路径），fulfill 会抛错
      }
    });
  }

  test("持久化 full 形态不影响普通页布局（回归）", async ({ page }) => {
    // 复现线上事故：访问过 /chat 后 localStorage 留下 full，回 /tasks 时
    // 聊天壳变成 w-full 的 flex 子项，把任务页挤成竖排。
    await page.addInitScript(() => {
      localStorage.setItem("vp:chat:variant", "full");
      localStorage.setItem("vp:chat:open", "1");
    });

    await login(page);
    await page.goto("/tasks");

    const shell = page.locator('[data-test="chat-shell"]');
    await expect(shell).toBeVisible({ timeout: 30_000 });
    // full 是 static/flex；floating/dock 必须是 fixed 浮层
    await expect
      .poll(async () => shell.evaluate((element) => getComputedStyle(element).position))
      .toBe("fixed");
    const box = await shell.boundingBox();
    expect(box?.width ?? 0).toBeLessThan(500);

    // 主内容区仍有正常宽度（没有被挤成一条竖线）
    const mainBox = await page.locator("main").boundingBox();
    expect(mainBox?.width ?? 0).toBeGreaterThan(300);

    // 用户实际路径：点「停靠到右侧」放大 → 仍必须是 fixed 浮层，不挤占主内容
    await page.locator('[data-test="chat-variant-toggle"]').click();
    await expect
      .poll(async () => shell.evaluate((element) => getComputedStyle(element).position))
      .toBe("fixed");
    const dockBox = await shell.boundingBox();
    expect(dockBox?.width ?? 0).toBeLessThan(500);
    const mainAfterDock = await page.locator("main").boundingBox();
    expect(mainAfterDock?.width ?? 0).toBeGreaterThan(300);
  });

  test("多 delta 流式 → 完成态；Markdown/KaTeX/Mermaid 均渲染", async ({ page }) => {
    await stubStream(page, {
      delayMs: 1_500,
      bodies: [
        sseBody([
          "## 桩响应\n\n第一段结论。\n\n",
          "行内公式 $E=mc^2$ 应渲染为 KaTeX。\n\n",
          "```mermaid\ngraph TD\n  A[检索] --> B[生成]\n```\n",
        ]),
      ],
    });

    await login(page);
    await page.goto("/tasks");
    await openChat(page);
    await newConversation(page);
    await send(page, `${MARKER} 渲染检查`);

    // 流式中：停止按钮与光标都在（桩延迟 1.5s，足够稳定观察）
    await expect(page.locator('[data-test="chat-stop"]')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('[data-test="chat-streaming-cursor"]')).toBeVisible();

    await waitStreamingSettled(page);

    const assistant = page.locator('[data-test="chat-body-assistant"]');
    await expect(assistant).toHaveCount(1);
    await expect(assistant).toContainText("第一段结论");
    // Markdown 标题 / KaTeX 公式 / Mermaid SVG 三个渲染器都要真的产出节点
    await expect(assistant.locator("h2")).toBeVisible();
    await expect(assistant.locator(".katex")).toBeVisible();
    await expect(assistant.locator(".mermaid-figure svg")).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('[data-test="chat-message-user"]')).toHaveCount(1);
  });

  test("停止生成 → interrupted 徽章 + 停止按钮消失", async ({ page }) => {
    await stubStream(page, {
      // 延迟足够长，确保「停止」在流式期间被点击
      delayMs: 15_000,
      bodies: [sseBody(["这段内容不应该完整出现"])],
    });

    await login(page);
    await page.goto("/tasks");
    await openChat(page);
    await newConversation(page);
    await send(page, `${MARKER} 长回答，用来测试停止`);

    await expect(page.locator('[data-test="chat-stop"]')).toBeVisible({ timeout: 15_000 });
    await page.locator('[data-test="chat-stop"]').click();

    // 流式立即收尾：徽章 + 停止按钮消失 + 光标消失
    await expect(page.locator('[data-test="chat-message-assistant"]')).toContainText("已中断", {
      timeout: 20_000,
    });
    await expect(page.locator('[data-test="chat-stop"]')).toHaveCount(0);
    await expect(page.locator('[data-test="chat-streaming-cursor"]')).toHaveCount(0);
    await expect(page.locator('[data-test="chat-message-error"]')).toHaveCount(0);
  });

  test("文档附件：上传知识库 → 随消息发送并落库", async ({ page }) => {
    await stubStream(page, { bodies: [sseBody(["收到附件，已准备好用它建综述。"]) ] });

    await login(page);
    await page.goto("/tasks");
    await openChat(page);
    await newConversation(page);

    // 上传：走知识库管线（解析/分块/索引），完成后出现附件 chip
    await page.locator('[data-test="chat-file-input"]').setInputFiles(FIXTURE);
    const chip = page.locator('[data-test="chat-attachment-chip"]');
    await expect(chip).toBeVisible({ timeout: 90_000 });
    await expect(chip).toContainText("sample");

    // 发送请求必须带上 document_id（后端据此校验归属并落 attachments JSON）
    const requestPromise = page.waitForRequest(
      (request) =>
        request.method() === "POST" && request.url().includes("/messages"),
      { timeout: 30_000 }
    );
    await send(page, `${MARKER} 用这篇文档建综述`);
    const request = await requestPromise;
    const body = request.postDataJSON() as {
      attachments?: { document_id?: string; title?: string }[];
    };
    expect(body.attachments?.length).toBe(1);
    expect(body.attachments?.[0]?.document_id).toMatch(/[0-9a-f-]{36}/);

    await waitStreamingSettled(page);
    // 用户气泡展示附件标题；助手正常完成
    await expect(page.locator('[data-test="chat-attachments"]')).toContainText("sample");
    await expect(page.locator('[data-test="chat-body-assistant"]')).toContainText("收到附件");
  });

  test("编辑重发产生分支 → 切换回上一分支看到原回答", async ({ page }) => {
    await stubStream(page, {
      bodies: [sseBody(["第一版回答：保留旧分支。"]), sseBody(["第二版回答：新分支。"]),],
    });

    await login(page);
    await page.goto("/tasks");
    await openChat(page);
    await newConversation(page);

    const firstPrompt = `${MARKER} 原始问题`;
    await send(page, firstPrompt);
    await waitStreamingSettled(page);
    await expect(page.locator('[data-test="chat-body-assistant"]')).toContainText("第一版回答");

    // 编辑重发：悬停显示工具条 → 编辑 → 改内容 → 重发
    const userMessage = page.locator('[data-test="chat-message-user"]');
    await userMessage.hover();
    await page.locator('[data-test="chat-edit"]').first().click();
    await page.locator('[data-test="chat-edit-input"]').fill(`${MARKER} 修改后的问题`);
    await page.locator('[data-test="chat-edit-submit"]').click();

    await waitStreamingSettled(page);
    await expect(page.locator('[data-test="chat-body-assistant"]')).toContainText("第二版回答");
    await expect(page.locator('[data-test="chat-body-user"]')).toContainText("修改后的问题");

    // 两个分支：2/2（新分支）→ 切到 1/2 必须能看到旧分支的**助手回答**
    const branchCount = page.locator('[data-test="chat-branch-count"]');
    await expect(branchCount).toHaveText("2/2", { timeout: 15_000 });
    await page.locator('[data-test="chat-branch-prev"]').first().click();

    await expect(page.locator('[data-test="chat-body-user"]')).toContainText("原始问题");
    await expect(page.locator('[data-test="chat-body-assistant"]')).toContainText("第一版回答");
    await expect(branchCount).toHaveText("1/2");
  });
});
