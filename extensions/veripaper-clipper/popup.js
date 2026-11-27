/**
 * VeriPaper Clipper —— 弹窗逻辑。
 *
 * 流程：读取设置 → 向内容脚本要当前页题录 → 预览（含「摘要 / DOI / 正文」标记）
 * → 用户可选「附带刚下载的 PDF」→ 点保存 → 交给 Service Worker 统一发请求：
 *   1. `POST /api/v1/reference-library`（题录，JSON）；
 *   2. 选了 PDF 时再 `POST /api/v1/knowledge/documents`（multipart，`ref_id` 关联题录）。
 *
 * 所有网络请求都在 background.js 里；弹窗只负责 UI、chrome.storage 与读取本地 PDF
 * （pdf 读成 data URL 后才能放进扩展消息，Service Worker 再还原成 Blob 上传）。
 */

const DEFAULT_API_BASE = "https://veripaper.me";
const SETTINGS_KEY = "veripaper_clipper_settings";
/** 与后端 `/knowledge/documents` 的上传上限保持一致（默认 30 MB）。 */
const MAX_PDF_BYTES = 30 * 1024 * 1024;

const els = {
  settings: document.getElementById("settings"),
  apiBase: document.getElementById("api-base"),
  apiKey: document.getElementById("api-key"),
  saveSettings: document.getElementById("save-settings"),
  extract: document.getElementById("extract"),
  save: document.getElementById("save"),
  attachPdf: document.getElementById("attach-pdf"),
  attachInfo: document.getElementById("attach-info"),
  status: document.getElementById("status"),
  preview: document.getElementById("preview"),
  previewTitle: document.getElementById("preview-title"),
  previewMeta: document.getElementById("preview-meta"),
  badgeDoi: document.getElementById("badge-doi"),
  badgeAbstract: document.getElementById("badge-abstract"),
  badgeFulltext: document.getElementById("badge-fulltext"),
};

// `var`：题录状态挂到 window，便于自动化冒烟在「提取已单独覆盖」的前提下
// 直接注入等价状态、专测「附带 PDF 保存」链路（见 test/verify-extension.mjs）。
var current = null;
let currentSource = null;
let selectedFile = null;
let settings = { apiBase: DEFAULT_API_BASE, apiKey: "" };

function setStatus(message, kind) {
  els.status.textContent = message || "";
  els.status.className = `status${kind ? ` ${kind}` : ""}`;
}

function setBusy(busy) {
  els.extract.disabled = busy;
  els.save.disabled = busy || !current;
  els.saveSettings.disabled = busy;
  els.attachPdf.disabled = busy;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }
  if (bytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** 后端 4xx/5xx 的 detail 可能是字符串，也可能是 FastAPI 校验错误数组。 */
function describeDetail(payload, fallback) {
  const detail = payload?.detail ?? payload?.message;
  if (typeof detail === "string" && detail.trim()) {
    return detail.trim();
  }
  if (Array.isArray(detail)) {
    const parts = detail
      .map((entry) => entry?.msg || (typeof entry === "string" ? entry : JSON.stringify(entry)))
      .filter(Boolean);
    if (parts.length > 0) {
      return parts.join("；");
    }
  }
  return fallback;
}

async function loadSettings() {
  const stored = await chrome.storage.local.get(SETTINGS_KEY);
  const value = stored[SETTINGS_KEY] || {};
  settings = {
    apiBase: (value.apiBase || DEFAULT_API_BASE).replace(/\/+$/, ""),
    apiKey: value.apiKey || "",
  };
  els.apiBase.value = settings.apiBase;
  els.apiKey.value = settings.apiKey;
  if (!settings.apiKey) {
    els.settings.open = true;
    setStatus("请先填写 API Key（在 VeriPaper 设置 · 开放 API 生成）", "error");
  }
}

async function saveSettings() {
  const apiBase = (els.apiBase.value || DEFAULT_API_BASE).trim().replace(/\/+$/, "");
  const apiKey = els.apiKey.value.trim();
  if (!apiBase || !apiKey) {
    setStatus("请同时填写 VeriPaper 地址与 API Key", "error");
    return;
  }
  if (!/^maa_[A-Za-z0-9_-]+$/.test(apiKey)) {
    setStatus("API Key 格式看起来不对（应以 maa_ 开头），请重新复制", "error");
    return;
  }
  settings = { apiBase, apiKey };
  await chrome.storage.local.set({ [SETTINGS_KEY]: settings });
  els.settings.open = false;
  setStatus("设置已保存（仅存于本机浏览器）", "ok");
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    throw new Error("拿不到当前标签页");
  }
  return tab;
}

async function extract() {
  setBusy(true);
  setStatus("正在读取当前页题录…");
  current = null;
  currentSource = null;
  els.preview.classList.remove("visible");
  try {
    const tab = await activeTab();
    let response;
    try {
      response = await chrome.tabs.sendMessage(tab.id, { type: "VERIPAPER_EXTRACT" });
    } catch {
      throw new Error(
        "当前页面不支持提取：请打开知网 / 维普 / 万方的文献详情页后再试"
      );
    }
    if (!response?.ok) {
      throw new Error(response?.error || "提取失败");
    }
    current = response.item;
    currentSource = response.source || {};
    const extractedText =
      typeof currentSource.extracted_text === "string" ? currentSource.extracted_text : "";

    els.previewTitle.textContent = current.title || "（无题名）";
    const metaParts = [current.container_title, current.issued_year].filter(Boolean);
    if (extractedText) {
      metaParts.push(`正文 ${extractedText.length} 字`);
    }
    els.previewMeta.textContent = metaParts.join(" · ") || current.url || "";
    els.badgeDoi.style.display = currentSource.has_doi ? "inline-block" : "none";
    els.badgeAbstract.style.display = currentSource.has_abstract
      ? "inline-block"
      : "none";
    els.badgeFulltext.style.display = extractedText ? "inline-block" : "none";
    els.preview.classList.add("visible");
    els.save.disabled = false;
    setStatus(
      extractedText
        ? `已提取题录（含正文 ${extractedText.length} 字），确认无误后点「保存到引用库」`
        : "已提取题录，确认无误后点「保存到引用库」",
      "ok"
    );
  } catch (error) {
    setStatus(String(error?.message || error), "error");
  } finally {
    setBusy(false);
  }
}

/** 清空已选 PDF 与提示（文件不合法时用）。 */
function clearAttachment() {
  selectedFile = null;
  els.attachPdf.value = "";
  els.attachInfo.textContent = "";
  els.attachInfo.className = "attach-info";
}

function handleFileChange() {
  const file = els.attachPdf.files?.[0] || null;
  selectedFile = null;
  els.attachInfo.textContent = "";
  els.attachInfo.className = "attach-info";
  if (!file) {
    return;
  }

  const name = file.name || "未命名文件";
  const lower = name.toLowerCase();
  if (lower.endsWith(".caj")) {
    clearAttachment();
    els.attachInfo.textContent = "检测到 .caj 文件：请先在官网转成 PDF 再选择";
    els.attachInfo.className = "attach-info error";
    setStatus("检测到 .caj 文件：请先在官网转成 PDF 再上传（扩展只接收 PDF）", "error");
    return;
  }
  if (!lower.endsWith(".pdf")) {
    clearAttachment();
    els.attachInfo.textContent = "仅支持 PDF 文件（.caj 请先在官网转成 PDF）";
    els.attachInfo.className = "attach-info error";
    setStatus("仅支持 PDF 文件；.caj 请先在官网转成 PDF", "error");
    return;
  }
  if (file.size > MAX_PDF_BYTES) {
    clearAttachment();
    els.attachInfo.textContent = `PDF 超过 ${MAX_PDF_BYTES / 1024 / 1024} MB 上限`;
    els.attachInfo.className = "attach-info error";
    setStatus(`PDF 超过 ${MAX_PDF_BYTES / 1024 / 1024} MB（后端上限），请压缩后再上传`, "error");
    return;
  }

  selectedFile = file;
  els.attachInfo.textContent = `已选择：${name}（${formatBytes(file.size)}），保存时将一并上传并关联到这条题录`;
}

/** File → data URL（扩展消息只能传 JSON，PDF 二进制走 base64）。 */
async function fileToAttachment(file) {
  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("读取文件失败"));
    reader.readAsDataURL(file);
  });
  if (!dataUrl.startsWith("data:")) {
    throw new Error("读取文件失败");
  }
  return {
    name: file.name || "attachment.pdf",
    type: file.type || "application/pdf",
    dataUrl,
  };
}

async function save() {
  if (!current) {
    return;
  }
  if (!settings.apiKey) {
    els.settings.open = true;
    setStatus("保存前请先填写 API Key", "error");
    return;
  }

  let attachment = null;
  if (selectedFile) {
    try {
      attachment = await fileToAttachment(selectedFile);
    } catch (error) {
      setStatus(`读取 PDF 失败：${String(error?.message || error)}`, "error");
      return;
    }
  }

  setBusy(true);
  setStatus(attachment ? "正在保存题录并上传 PDF…" : "正在保存到引用库…");
  try {
    const result = await chrome.runtime.sendMessage({
      type: "VERIPAPER_SAVE",
      item: current,
      // 开放 HTML 页正文：随保存消息透传（当前版本只用于预览，不写回题录字段）。
      extractedText:
        typeof currentSource?.extracted_text === "string" ? currentSource.extracted_text : "",
      attachment,
      apiBase: settings.apiBase,
      apiKey: settings.apiKey,
    });
    if (!result?.ok) {
      const detail = describeDetail(
        result?.body,
        result?.error || `HTTP ${result?.status}`
      );
      throw new Error(detail);
    }

    const counts = result.body || {};
    const created = counts.created ?? 0;
    const skipped = counts.skipped ?? 0;
    if (!attachment) {
      // 未选 PDF：与旧版完全一致的文案（只保存题录）。
      setStatus(`已保存：新增 ${created} 条，已存在 ${skipped} 条`, "ok");
      return;
    }

    const savedMessage = `已保存：新增题录 ${created} 条，已存在 ${skipped} 条`;
    if (result.document?.ok) {
      const chunkCount = result.document.body?.chunk_count;
      setStatus(
        `${savedMessage}；全文已关联（${result.refId || "本地题录 id"}）${
          Number.isFinite(chunkCount) ? `，${chunkCount} 个分块` : ""
        }`,
        "ok"
      );
    } else {
      const reason =
        describeDetail(result.document?.body, result.document?.error || `HTTP ${result.document?.status}`) ||
        "未知错误";
      setStatus(
        `${savedMessage}；PDF 上传失败：${reason}（题录已保存，可稍后到知识库页手动上传）`,
        "error"
      );
    }
  } catch (error) {
    setStatus(
      `保存失败：${String(error?.message || error)}（如提示 401，请检查 API Key；提示 403，请确认套餐含开放 API）`,
      "error"
    );
  } finally {
    setBusy(false);
  }
}

els.saveSettings.addEventListener("click", () => {
  void saveSettings();
});
els.extract.addEventListener("click", () => {
  void extract();
});
els.save.addEventListener("click", () => {
  void save();
});
els.attachPdf.addEventListener("change", () => {
  handleFileChange();
});

void loadSettings();
