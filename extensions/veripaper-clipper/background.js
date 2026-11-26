/**
 * VeriPaper Clipper —— Service Worker：把提取到的题录保存到 VeriPaper 引用库。
 *
 * ## 为什么用 `X-API-Key`
 *
 * VeriPaper 的双通道鉴权（`backend/app/api/deps.py`）对开放 API 使用
 * **`X-API-Key` 头**，不是 `Authorization: Bearer` —— 把 Key 塞进 Bearer
 * 会被 JWT 解析器拒绝（401）。这是设计方案的笔误，落地以代码为准。
 *
 * ## 权限说明
 *
 * fetch 目标域名在 `manifest.json` 的 `host_permissions` 里，
 * 扩展 Service Worker 才能跨域读取响应（MV3 下不是网页 CORS 语义）。
 */

const DEFAULT_API_BASE = "https://veripaper.me";

async function saveItem({ item, apiBase, apiKey }) {
  const base = (apiBase || DEFAULT_API_BASE).replace(/\/+$/, "");
  const response = await fetch(`${base}/api/v1/reference-library`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": apiKey,
    },
    body: JSON.stringify({ items: [item] }),
  });

  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  return { ok: response.ok, status: response.status, body };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "VERIPAPER_SAVE") {
    saveItem(message)
      .then((result) => sendResponse(result))
      .catch((error) => sendResponse({ ok: false, status: 0, error: String(error?.message || error) }));
    return true; // 异步 sendResponse
  }
  return false;
});
