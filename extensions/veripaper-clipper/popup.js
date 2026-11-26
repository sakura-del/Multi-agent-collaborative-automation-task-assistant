/**
 * VeriPaper Clipper —— 弹窗逻辑。
 *
 * 流程：读取设置 → 向内容脚本要当前页题录 → 预览（含「摘要 / DOI」标记）
 * → 用户点保存 → 交给 Service Worker 发 POST（X-API-Key）。
 *
 * 所有网络请求都在 background.js 里；弹窗只负责 UI 与 chrome.storage。
 */

const DEFAULT_API_BASE = "https://veripaper.me";
const SETTINGS_KEY = "veripaper_clipper_settings";

const els = {
  settings: document.getElementById("settings"),
  apiBase: document.getElementById("api-base"),
  apiKey: document.getElementById("api-key"),
  saveSettings: document.getElementById("save-settings"),
  extract: document.getElementById("extract"),
  save: document.getElementById("save"),
  status: document.getElementById("status"),
  preview: document.getElementById("preview"),
  previewTitle: document.getElementById("preview-title"),
  previewMeta: document.getElementById("preview-meta"),
  badgeDoi: document.getElementById("badge-doi"),
  badgeAbstract: document.getElementById("badge-abstract"),
};

let current = null;
let settings = { apiBase: DEFAULT_API_BASE, apiKey: "" };

function setStatus(message, kind) {
  els.status.textContent = message || "";
  els.status.className = `status${kind ? ` ${kind}` : ""}`;
}

function setBusy(busy) {
  els.extract.disabled = busy;
  els.save.disabled = busy || !current;
  els.saveSettings.disabled = busy;
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
    els.previewTitle.textContent = current.title || "（无题名）";
    const meta = [current.container_title, current.issued_year].filter(Boolean).join(" · ");
    els.previewMeta.textContent = meta || current.url || "";
    els.badgeDoi.style.display = response.source?.has_doi ? "inline-block" : "none";
    els.badgeAbstract.style.display = response.source?.has_abstract
      ? "inline-block"
      : "none";
    els.preview.classList.add("visible");
    els.save.disabled = false;
    setStatus("已提取题录，确认无误后点「保存到引用库」", "ok");
  } catch (error) {
    setStatus(String(error?.message || error), "error");
  } finally {
    setBusy(false);
  }
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
  setBusy(true);
  setStatus("正在保存到引用库…");
  try {
    const result = await chrome.runtime.sendMessage({
      type: "VERIPAPER_SAVE",
      item: current,
      apiBase: settings.apiBase,
      apiKey: settings.apiKey,
    });
    if (!result?.ok) {
      const detail =
        result?.body?.detail || result?.body?.message || result?.error || `HTTP ${result?.status}`;
      throw new Error(String(detail));
    }
    const counts = result.body || {};
    setStatus(
      `已保存：新增 ${counts.created ?? 0} 条，已存在 ${counts.skipped ?? 0} 条`,
      "ok"
    );
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

void loadSettings();
