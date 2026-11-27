"""Zotero → 引用库 / 知识库同步编排（Phase B Task B3）。

## 流程

1. 读取并解密当前用户的 ``DataSourceCredential(provider="zotero")``：
   密文是紧凑 JSON ``{"zotero_user_id": "...", "zotero_api_key": "..."}``
   （复用 ``CredentialCipher`` 的 AES-256-GCM + AAD=user_id，跨用户搬运必失败）；
2. :class:`~app.tools.literature.zotero_client.ZoteroClient` 拉取条目
   （一次最多 100 条）；
3. 普通条目归一化为 ``CslItem``，复用
   :class:`~app.knowledge.services.reference_library_service.ReferenceLibraryService`
   的 ``save()`` —— 按 DOI / 题名派生的 ``ref_id`` 幂等，重复条目跳过；
4. ``itemType=attachment`` 且 ``contentType=application/pdf`` 的条目，
   按 ``parentItem`` 绑定到父条目的 ``ref_id``，走
   :class:`KnowledgeDocumentService.ingest` 统一入库，入库后把
   ``metadata.origin`` 标记为 ``"zotero"``（与 ``oa_auto`` 同一约定，
   见全文获取 spec 的 origin 枚举）；
5. 单文件超过 30MB、或单个附件下载 / 入库失败只跳过该附件，**不中断整批**。

## 为什么自己查凭据而不是复用 `DataSourceCredentialService`

Zotero 不是检索数据源，``DATA_SOURCE_PROVIDERS`` 白名单里没有它（为不改动
既有「数据源凭据」列表契约）。``DataSourceCredentialService.require_provider``
会拒绝 ``zotero``，因此本服务直接查 ORM 行并用 ``CredentialCipher`` 解密；
provider 白名单的放宽只发生在 HTTP 适配层（``data_source_credentials.py``）。

## 合规

只同步**用户自己的** Zotero 库（用户自己的 API Key 授权），不抓取付费墙全文；
附件同步失败只影响该附件，返回计数里如实体现。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import select

from app.core.exceptions import (
    NotFoundException,
    UnauthorizedException,
    UpstreamError,
    ValidationException,
)
from app.core.logging import get_logger
from app.db.models.data_source_credential import DataSourceCredential
from app.db.models.knowledge import KnowledgeDoc
from app.knowledge.models.document import ChunkStrategy
from app.knowledge.services.document_service import KnowledgeDocumentService
from app.knowledge.services.factory import get_index_manager, get_kb, get_storage
from app.knowledge.services.reference_library_service import ReferenceLibraryService
from app.security.credential_cipher import CredentialCipher, CredentialDecryptError
from app.tools.csl.models import (
    CslItem,
    CslName,
    build_ref_id,
    detect_language,
    normalize_doi,
    parse_year,
)
from app.tools.literature.zotero_client import (
    ZoteroAttachmentTooLarge,
    ZoteroAuthError,
    ZoteroClient,
    ZoteroClientError,
)

logger = get_logger(__name__)

#: 凭据表里的 provider 名。
ZOTERO_PROVIDER = "zotero"

#: 单次同步最多拉取条目数（与 HTTP 请求模型上限一致，防止整库拉取打爆上游）。
MAX_SYNC_ITEMS = 100

#: Zotero itemType → CSL type（未列出的一律按预印本 manuscript 处理）。
_ZOTERO_TYPE_MAP: dict[str, str] = {
    "journalArticle": "article-journal",
    "book": "book",
    "bookSection": "chapter",
    "conferencePaper": "paper-conference",
    "thesis": "thesis",
    "report": "report",
    "webpage": "webpage",
    "blogPost": "webpage",
    "forumPost": "webpage",
    "manuscript": "manuscript",
    "preprint": "manuscript",
    "dataset": "dataset",
    "standard": "standard",
    "patent": "patent",
}

#: 不进入引用库的条目类型（笔记 / 标注；附件单独处理）。
_SKIP_ITEM_TYPES = frozenset({"note", "annotation", "attachment"})

#: `knowledge_doc.title` 是 String(256)。
_MAX_TITLE_LENGTH = 256

#: `knowledge_doc.metadata.origin` 取值（全文获取 spec 的 origin 枚举）。
_ORIGIN_ZOTERO = "zotero"

#: 全文获取 spec 中 `ref_id` 允许的最大长度（与 `Form(max_length=200)` 对齐）。
_MAX_REF_ID_LENGTH = 200


@dataclass
class ZoteroSyncResult:
    """同步结果计数：**部分成功如实报告**。

    - ``imported``：新写入引用库的条目数；
    - ``skipped``：已存在（同 DOI / 同题名）或按规则跳过的条目 / 附件数；
    - ``attachments``：成功入库的 PDF 附件数；
    - ``failed``：被引用库拒绝、或单个附件下载 / 入库失败的数目。
    """

    imported: int = 0
    skipped: int = 0
    attachments: int = 0
    failed: int = 0


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = _text(value)
        if text:
            return text
    return None


def _author_names(creators: Any) -> list[CslName]:
    """Zotero creators → CSL 作者列表（机构作者写 ``name``，个人作者拆姓 / 名）。"""
    if not isinstance(creators, list):
        return []
    entries = [entry for entry in creators if isinstance(entry, Mapping)]
    authors = [entry for entry in entries if _text(entry.get("creatorType")) in (None, "author")]
    if not authors:
        authors = entries
    names: list[CslName] = []
    for entry in authors:
        literal = _text(entry.get("name"))
        if literal:
            names.append(CslName(literal=literal))
            continue
        family = _text(entry.get("lastName")) or ""
        given = _text(entry.get("firstName")) or ""
        if family or given:
            names.append(CslName(family=family, given=given))
    return names


def _map_item_type(item_type: str | None) -> str:
    if not item_type:
        return "manuscript"
    return _ZOTERO_TYPE_MAP.get(item_type, "manuscript")


def normalize_zotero_item(item: Mapping[str, Any]) -> CslItem | None:
    """Zotero item（``{"key": ..., "data": {...}}``）→ ``CslItem``；无题名返回 ``None``。

    ``ref_id`` 用 :func:`build_ref_id` 的既有规则：**优先归一化 DOI**，否则用
    题名哈希 —— 这正是「同 DOI 已存在则跳过 / 同题名幂等」的来源。
    """
    data = item.get("data")
    if not isinstance(data, Mapping):
        return None
    title = _text(data.get("title"))
    if not title:
        return None

    doi = normalize_doi(_first_text(data.get("DOI"), data.get("doi")))
    authors = _author_names(data.get("creators"))
    issued_year = parse_year(_first_text(data.get("date"), data.get("issued")))
    container_title = _first_text(
        data.get("publicationTitle"),
        data.get("bookTitle"),
        data.get("proceedingsTitle"),
        data.get("websiteTitle"),
        data.get("seriesTitle"),
    )
    abstract = _text(data.get("abstractNote"))
    if abstract:
        abstract = abstract[:4000]
    author_texts = [
        name.literal or " ".join(part for part in (name.family, name.given) if part)
        for name in authors
    ]

    return CslItem(
        id=build_ref_id(doi=doi, title=title),
        type=_map_item_type(_text(data.get("itemType"))),
        title=title,
        author=authors,
        container_title=container_title,
        volume=_text(data.get("volume")),
        issue=_text(data.get("issue")),
        page=_first_text(data.get("pages"), data.get("page")),
        publisher=_text(data.get("publisher")),
        publisher_place=_first_text(data.get("place"), data.get("publisherPlace")),
        issued_year=issued_year,
        doi=doi,
        url=_text(data.get("url")),
        abstract=abstract,
        medium=None,
        language=detect_language(
            title=title,
            authors=author_texts,
            explicit=_text(data.get("language")),
        ),
    )


class ZoteroSyncService:
    """把用户 Zotero 库同步进引用库 / 知识库。

    ``client_factory`` / ``document_service`` 是测试注入点：
    前者按 API Key 构造 :class:`ZoteroClient`（测试用 ``httpx.MockTransport``），
    后者避免单测依赖真实向量库。
    """

    def __init__(
        self,
        db: Any,
        *,
        client_factory: Callable[[str], ZoteroClient] | None = None,
        document_service: KnowledgeDocumentService | None = None,
        cipher: CredentialCipher | None = None,
    ) -> None:
        self._db = db
        self._client_factory = client_factory
        self._document_service = document_service
        self._credential_cipher = cipher

    # ===== 公开接口 =====

    async def sync(
        self,
        user_id: UUID,
        *,
        library_type: str = "user",
        collection_key: str | None = None,
        limit: int = 50,
    ) -> ZoteroSyncResult:
        """拉取 Zotero 条目并入库；返回四个计数（失败不抛，计数里体现）。"""
        zotero_user_id, api_key = await self._load_credential(user_id)
        result = ZoteroSyncResult()

        client = self._make_client(api_key)
        async with client:
            try:
                items = await client.fetch_items(
                    library_type=library_type,
                    library_id=zotero_user_id,
                    collection_key=collection_key,
                    limit=min(max(int(limit), 1), MAX_SYNC_ITEMS),
                )
            except ZoteroAuthError as exc:
                raise UnauthorizedException(f"Zotero 凭据无效：{exc}") from exc
            except ZoteroClientError as exc:
                raise UpstreamError(f"Zotero 同步失败：{exc}") from exc
            await self._sync_items(
                user_id,
                client=client,
                library_type=library_type,
                library_id=zotero_user_id,
                items=items,
                result=result,
            )

        logger.info(
            "zotero_sync_completed",
            user_id=str(user_id),
            imported=result.imported,
            skipped=result.skipped,
            attachments=result.attachments,
            failed=result.failed,
        )
        return result

    # ===== 凭据 =====

    @property
    def _cipher(self) -> CredentialCipher:
        if self._credential_cipher is None:
            self._credential_cipher = CredentialCipher.from_settings()
        return self._credential_cipher

    async def _load_credential(self, user_id: UUID) -> tuple[str, str]:
        """解密 Zotero 凭据，返回 ``(zotero_user_id, api_key)``。

        错误语义（端点直接透传，均为可读消息）：
        - 未配置 / 已停用 → 404；
        - 密文解不开（换了主密钥 / 被篡改 / 跨用户搬运）→ 401；
        - 内容不是合法 JSON 或缺字段 → 422。
        """
        row = await self._db.scalar(
            select(DataSourceCredential).where(
                DataSourceCredential.user_id == user_id,
                DataSourceCredential.provider == ZOTERO_PROVIDER,
            )
        )
        if row is None or row.status != "active":
            raise NotFoundException(
                "尚未配置 Zotero 凭据，请先在「数据源凭据」中填写 " "Zotero 用户 ID 与 API Key"
            )
        try:
            plaintext = self._cipher.decrypt(row.api_key_ciphertext, user_id=user_id)
        except CredentialDecryptError as exc:
            raise UnauthorizedException("Zotero 凭据无法解密，请重新填写") from exc

        try:
            payload = json.loads(plaintext)
        except (TypeError, ValueError) as exc:
            raise ValidationException(
                "Zotero 凭据格式不合法：需要 JSON {zotero_user_id, zotero_api_key}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValidationException(
                "Zotero 凭据格式不合法：需要 JSON {zotero_user_id, zotero_api_key}"
            )
        zotero_user_id = _text(payload.get("zotero_user_id"))
        api_key = _text(payload.get("zotero_api_key"))
        if not zotero_user_id or not api_key:
            raise ValidationException("Zotero 凭据缺少 zotero_user_id 或 zotero_api_key")
        return zotero_user_id, api_key

    # ===== 客户端 / 文档服务 =====

    def _make_client(self, api_key: str) -> ZoteroClient:
        if self._client_factory is not None:
            return self._client_factory(api_key)
        return ZoteroClient(api_key)

    def _get_document_service(self) -> KnowledgeDocumentService:
        if self._document_service is None:
            self._document_service = KnowledgeDocumentService(
                self._db,
                storage=get_storage(),
                index_manager=get_index_manager(),
                kb=get_kb(),
            )
        return self._document_service

    # ===== 条目 / 附件编排 =====

    async def _sync_items(
        self,
        user_id: UUID,
        *,
        client: ZoteroClient,
        library_type: str,
        library_id: str,
        items: list[dict[str, Any]],
        result: ZoteroSyncResult,
    ) -> None:
        entries: list[CslItem] = []
        csl_by_key: dict[str, CslItem] = {}
        attachments: list[tuple[str, Mapping[str, Any]]] = []

        for raw in items:
            data = raw.get("data")
            if not isinstance(data, Mapping):
                result.failed += 1
                continue
            item_type = _text(data.get("itemType")) or ""
            item_key = _text(raw.get("key")) or _text(data.get("key")) or ""

            if item_type == "attachment":
                attachments.append((item_key, data))
                continue
            if item_type in _SKIP_ITEM_TYPES:
                continue

            csl_item = normalize_zotero_item(raw)
            if csl_item is None:
                result.skipped += 1
                continue
            entries.append(csl_item)
            if item_key:
                csl_by_key[item_key] = csl_item

        saved = await ReferenceLibraryService(self._db).save(user_id, entries)
        result.imported = saved.created
        result.skipped += saved.skipped + saved.merged
        result.failed += len(saved.rejected)

        rejected_ref_ids = {item.ref_id for item in saved.rejected}
        accepted_ref_id_by_key = {
            key: item.id for key, item in csl_by_key.items() if item.id not in rejected_ref_ids
        }
        title_by_key = {key: item.title for key, item in csl_by_key.items()}

        if not attachments:
            return

        for item_key, data in attachments:
            content_type = (_text(data.get("contentType")) or "").lower()
            if content_type != "application/pdf":
                result.skipped += 1
                continue
            parent_key = _text(data.get("parentItem")) or ""
            ref_id = accepted_ref_id_by_key.get(parent_key)
            if not ref_id:
                # 父条目缺失 / 被引用库拒绝：附件没有可追溯的引用库锚点，跳过
                result.skipped += 1
                continue

            try:
                downloaded = await client.download_attachment(
                    library_type=library_type,
                    library_id=library_id,
                    item_key=item_key,
                )
            except ZoteroAttachmentTooLarge as exc:
                logger.info("zotero_attachment_skipped", item_key=item_key, reason=str(exc))
                result.skipped += 1
                continue
            except ZoteroAuthError as exc:
                raise UnauthorizedException(f"Zotero 凭据无效：{exc}") from exc
            except ZoteroClientError as exc:
                logger.warning("zotero_attachment_failed", item_key=item_key, reason=str(exc))
                result.failed += 1
                continue
            if downloaded is None:
                logger.warning("zotero_attachment_missing", item_key=item_key)
                result.failed += 1
                continue

            title = (
                title_by_key.get(parent_key)
                or _text(data.get("title"))
                or item_key
                or "zotero-attachment"
            )[:_MAX_TITLE_LENGTH]
            try:
                doc = await self._get_document_service().ingest(
                    user_id,
                    UploadFile(
                        filename=f"{item_key or 'attachment'}.pdf",
                        file=BytesIO(downloaded.content),
                    ),
                    title=title,
                    strategy=ChunkStrategy.RECURSIVE,
                    ref_id=ref_id[:_MAX_REF_ID_LENGTH],
                )
            except Exception as exc:
                logger.warning(
                    "zotero_attachment_ingest_failed",
                    item_key=item_key,
                    reason=str(exc),
                )
                result.failed += 1
                continue

            await self._mark_origin(doc, ref_id)
            result.attachments += 1

    async def _mark_origin(self, doc: KnowledgeDoc, ref_id: str) -> None:
        """把统一入库的文档标记为 ``origin="zotero"``（不新增 schema 字段）。"""
        meta = dict(getattr(doc, "meta_info", None) or {})
        meta["origin"] = _ORIGIN_ZOTERO
        meta.setdefault("ref_id", ref_id[:_MAX_REF_ID_LENGTH])
        doc.meta_info = meta  # type: ignore[assignment]  # ORM JSON 列，dict 即合法值
        await self._db.commit()
