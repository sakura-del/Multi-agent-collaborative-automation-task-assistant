/**
 * VeriPaper Clipper —— 内容脚本：从文献详情页提取 **题录元数据（含摘要）**。
 *
 * ## 设计红线（Issue #67 Step 2）
 *
 * 1. **零爬虫**：只读当前用户主动打开的详情页 DOM / `<meta>`，
 *    不发起任何站内请求、不翻页、不批量抓取。
 * 2. **不造假**：拿不到 URL / 年份就留空（后端会拒绝入库，绝不编造）。
 * 3. **摘要只是题录摘要**：标记 `abstract_only` 语义，后端不会把它当全文分片。
 *
 * 提取顺序：`citation_*` 标准 meta 优先（Google Scholar 等通用方案），
 * 缺失字段再用知网/维普/万方的 DOM 选择器补。
 */

const MAX_ABSTRACT_LENGTH = 4000;

function metaContents(name) {
  return Array.from(document.querySelectorAll(`meta[name="${name}"], meta[property="${name}"]`))
    .map((node) => (node.getAttribute("content") || "").trim())
    .filter(Boolean);
}

function firstMeta(...names) {
  for (const name of names) {
    const value = metaContents(name)[0];
    if (value) {
      return value;
    }
  }
  return "";
}

function textOf(selectors) {
  for (const selector of selectors) {
    const node = document.querySelector(selector);
    const text = node?.textContent?.trim();
    if (text) {
      return text;
    }
  }
  return "";
}

function normalizeDoi(raw) {
  const text = (raw || "").trim();
  const match = text.match(/10\.\d{4,9}\/[^\s"'<>]+/i);
  return match ? match[0].replace(/[.,;]+$/, "") : "";
}

function normalizePage(raw) {
  const match = (raw || "").match(/([A-Za-z]?\d+)\s*[-–—]\s*([A-Za-z]?\d+)/);
  return match ? `${match[1]}-${match[2]}` : (raw || "").trim();
}

function parseYear(raw) {
  const match = (raw || "").match(/(19|20)\d{2}/);
  return match ? Number(match[0]) : null;
}

function splitAuthors(raw) {
  return (raw || "")
    .split(/[;；]|(?<=\.)\s+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function toCslName(raw) {
  const text = (raw || "").trim();
  if (!text) {
    return null;
  }
  const comma = text.indexOf(",");
  if (comma > 0) {
    return { family: text.slice(0, comma).trim(), given: text.slice(comma + 1).trim() };
  }
  return { family: text, given: "" };
}

function slugify(title) {
  return (title || "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 120);
}

function inferType(sourceHost) {
  const declared = firstMeta("citation_type");
  if (declared) {
    return declared;
  }
  if (/thesis|学位论文/.test(document.title)) {
    return "thesis";
  }
  if (sourceHost.includes("cnki") && /dissertation|cdmd/i.test(location.pathname)) {
    return "thesis";
  }
  if (sourceHost.includes("cnki") && /conference|cpfd/i.test(location.pathname)) {
    return "paper-conference";
  }
  return "article-journal";
}

/** 提取当前页面题录；返回 **CslItem 形态**（后端保存接口要求 `extra=forbid` 的精确字段名）。 */
function extractItem() {
  const sourceHost = location.hostname;
  const title =
    firstMeta("citation_title", "dc.title") ||
    textOf([
      "h1.title",
      ".title",
      ".detail-title",
      "#ChDivTitle",
      "h1",
    ]) ||
    document.title;

  const authorRaw =
    metaContents("citation_author").join("; ") ||
    textOf([".author", ".authors", "#authorList", "#ChDivAuthor", ".journal-author"]);
  const authors = splitAuthors(authorRaw).map(toCslName).filter(Boolean);

  const abstract =
    firstMeta("citation_abstract", "dc.description", "description") ||
    textOf([
      ".abstract",
      "#abstract",
      ".abstract-content",
      "#ChDivSummary",
      ".summary",
      "[class*='abstract']",
    ]);
  const containerTitle =
    firstMeta("citation_journal_title", "citation_conference_title", "dc.source") ||
    textOf([".journal-title", ".source-title", "#ChDivSummary", ".periodical-title"]);

  const doi = normalizeDoi(
    firstMeta("citation_doi", "dc.identifier") ||
      textOf(["meta[name='citation_doi']", ".doi"]) ||
      document.body.innerText.match(/10\.\d{4,9}\/[^\s"'<>]+/)?.[0]
  );
  const year = parseYear(
    firstMeta("citation_publication_date", "citation_date", "citation_year", "dc.date") ||
      textOf([".year", ".publish-year", ".date"])
  );
  const url = location.href;
  const type = inferType(sourceHost);

  const id = doi ? `doi:${doi}` : `title:${slugify(title)}`;

  /** CslItem 形态；字段名必须与后端 `CslItem` 完全一致（`extra=forbid`）。 */
  const item = {
    id,
    type,
    title: title.trim(),
    author: authors,
    url,
    language: /[\u4e00-\u9fff]/.test(title) ? "zh-CN" : "en",
  };
  if (containerTitle) {
    item.container_title = containerTitle;
  }
  if (abstract) {
    item.abstract = abstract.slice(0, MAX_ABSTRACT_LENGTH);
  }
  if (year) {
    item.issued_year = year;
  }
  if (doi) {
    item.doi = doi;
  }

  return {
    item,
    source: {
      host: sourceHost,
      url,
      has_doi: Boolean(doi),
      has_abstract: Boolean(abstract),
      abstract_only: true,
    },
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "VERIPAPER_EXTRACT") {
    try {
      sendResponse({ ok: true, ...extractItem() });
    } catch (error) {
      sendResponse({ ok: false, error: String(error?.message || error) });
    }
  }
  return false;
});
