/**
 * VeriPaper Clipper —— Service Worker：一次提交「题录 + 附带 PDF」。
 *
 * ## 两段式保存（Task B4）
 *
 * 1. `POST /api/v1/reference-library`（JSON）把题录取进引用库；
 * 2. 用户选了附带 PDF 时，再 `POST /api/v1/knowledge/documents`（multipart）
 *    上传全文，并用题录的本地 `ref_id`（DOI 优先，否则题名派生）自动关联引用库条目。
 *
 * 两段都从 Service Worker 发出（popup 只负责读文件/展示结果），这样页面关闭、
 * popup 失焦都不会打断请求。popup 与 SW 之间只能传 JSON，所以 PDF 先由 popup
 * 读成 data URL 再随消息传过来，这里还原成 Blob 组 multipart。
 *
 * ## 为什么用 `X-API-Key`
 *
 * VeriPaper 的双通道鉴权（`backend/app/api/deps.py`）对开放 API 使用
 * **`X-API-Key` 头**，不是 `Authorization: Bearer` —— 把 Key 塞进 Bearer
 * 会被 JWT 解析器拒绝（401）。这是设计方案的笔误，落地以代码为准。
 * Key 只在请求头里出现，**不写日志、不持久化到 service worker**。
 *
 * ## 权限说明
 *
 * fetch 目标域名在 `manifest.json` 的 `host_permissions` 里，
 * 扩展 Service Worker 才能跨域读取响应（MV3 下不是网页 CORS 语义）。
 */

const DEFAULT_API_BASE = "https://veripaper.me";
const LIBRARY_PATH = "/api/v1/reference-library";
const DOCUMENTS_PATH = "/api/v1/knowledge/documents";
/** 与后端 `/knowledge/documents` 的上传上限保持一致（默认 30 MB）。 */
const MAX_ATTACHMENT_BYTES = 30 * 1024 * 1024;

function normalizeBase(apiBase) {
  return (apiBase || DEFAULT_API_BASE).replace(/\/+$/, "");
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

/** 题录 JSON POST（旧 `saveItem` 的行为保持兼容，额外返回 `ref_id` 供上传关联）。 */
async function postLibrary({ item, base, apiKey }) {
  const response = await fetch(`${base}${LIBRARY_PATH}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": apiKey,
    },
    body: JSON.stringify({ items: [item] }),
  });
  return { ok: response.ok, status: response.status, body: await readJson(response) };
}

/**
 * 本地题录 id 作为 `ref_id`：优先 `item.id`（content.js 生成 `doi:` / `title:` 前缀），
 * 再退回 DOI / 题名派生；后端 `ref_id` 表单字段上限 200 字符。
 */
function resolveRefId(item) {
  const localId = typeof item?.id === "string" ? item.id.trim() : "";
  if (localId) {
    return localId.slice(0, 200);
  }
  if (item?.doi) {
    return `doi:${String(item.doi).trim()}`.slice(0, 200);
  }
  const slug = String(item?.title || "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 120);
  return slug ? `title:${slug}`.slice(0, 200) : "";
}

/** `title` 表单字段：必填、1~256 字符；缺题名时用一个明确占位，不编造。 */
function documentTitle(item) {
  const title = String(item?.title || "").trim();
  return (title || "剪藏文献 PDF").slice(0, 256);
}

/**
 * multipart 的文件名：去掉引号/换行/反斜杠等会破坏 multipart 头部的字符，
 * 其余（含中文原名）保留，后端会再清洗落盘。
 */
function safeFilename(name) {
  const cleaned = String(name || "attachment.pdf")
    .replace(/["\r\n\\]/g, "_")
    .trim();
  return (cleaned || "attachment.pdf").slice(-120);
}

/** data URL → Blob（popup 用 FileReader 读 PDF，消息只能传 JSON）。 */
function dataUrlToBlob(dataUrl) {
  const text = String(dataUrl || "");
  const comma = text.indexOf(",");
  if (comma < 0) {
    throw new Error("附件数据不完整（缺少 data URL 头）");
  }
  const mimeMatch = /^data:([^;,]+)/.exec(text.slice(0, comma));
  const binary = atob(text.slice(comma + 1));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new Blob([bytes], { type: mimeMatch?.[1] || "application/pdf" });
}

/** 上传全文并关联引用库条目。`Content-Type` 交给浏览器生成（带 boundary），不手写。 */
async function uploadDocument({ item, attachment, refId, base, apiKey }) {
  const blob = dataUrlToBlob(attachment.dataUrl);
  if (blob.size > MAX_ATTACHMENT_BYTES) {
    return {
      ok: false,
      status: 0,
      body: null,
      error: `PDF 超过 ${MAX_ATTACHMENT_BYTES / 1024 / 1024} MB，后端会拒绝`,
    };
  }

  const form = new FormData();
  form.append("file", blob, safeFilename(attachment.name));
  form.append("title", documentTitle(item));
  if (refId) {
    form.append("ref_id", refId);
  }

  const response = await fetch(`${base}${DOCUMENTS_PATH}`, {
    method: "POST",
    headers: { "X-API-Key": apiKey },
    body: form,
  });
  return { ok: response.ok, status: response.status, body: await readJson(response) };
}

/**
 * 保存入口：先题录；题录成功且用户选了 PDF 才上传全文。
 *
 * 返回值保持旧契约（`ok` / `status` / `body` 为题录结果），新增：
 * - `refId`：实际用于关联的本地题录 id（未上传时为 null）；
 * - `document`：全文上传结果 `{ok,status,body}`；未附带 PDF 或题录失败时为 null。
 * 题录成功但 PDF 失败时 `ok` 仍为 true（题录确实已入库），由 popup 组合文案提示。
 */
async function saveAll({ item, apiBase, apiKey, attachment }) {
  const base = normalizeBase(apiBase);
  const library = await postLibrary({ item, base, apiKey });
  if (!library.ok || !attachment) {
    return { ...library, refId: null, document: null };
  }

  const refId = resolveRefId(item);
  let document;
  try {
    document = await uploadDocument({ item, attachment, refId, base, apiKey });
  } catch (error) {
    document = { ok: false, status: 0, body: null, error: String(error?.message || error) };
  }
  return { ...library, refId, document };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "VERIPAPER_SAVE") {
    saveAll(message)
      .then((result) => sendResponse(result))
      .catch((error) =>
        sendResponse({
          ok: false,
          status: 0,
          error: String(error?.message || error),
          document: null,
        })
      );
    return true; // 异步 sendResponse
  }
  return false;
});
