# Foundation And Knowledge Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复当前 P0 知识库越权与路径读取问题，并使 Docker Compose 能自行构建、迁移和真实检查全部基础依赖。

**Architecture:** HTTP 上传先写入用户专属受控目录，再由 `KnowledgeDocumentService` 协调 ORM、分块与索引。所有检索通道携带 `user_id` 过滤；FastAPI lifespan 显式初始化 RAG，Compose 只暴露 Nginx。

**Tech Stack:** FastAPI、SQLAlchemy asyncio、PostgreSQL、Chroma、BM25、Redis、AnyIO、Alembic、Docker Compose、Nginx、pytest。

---

## File Structure

- Create: `backend/app/schemas/knowledge.py` - 知识库 HTTP 响应与检索请求模型。
- Create: `backend/app/db/repositories/knowledge.py` - owner-scoped 文档与分块查询。
- Create: `backend/app/knowledge/services/document_storage.py` - 受控上传目录、签名与大小校验。
- Create: `backend/app/knowledge/services/document_service.py` - ORM 与 RAG 索引事务编排。
- Create: `backend/tests/test_document_storage.py` - 路径、大小、文件签名测试。
- Create: `backend/tests/test_knowledge_repository.py` - 两用户隔离测试。
- Create: `backend/tests/test_readiness.py` - 真实依赖 readiness 测试。
- Create: `backend/entrypoint.sh` - 容器启动前迁移。
- Create: `nginx/Dockerfile` - 前端构建与 Nginx 运行镜像。
- Modify: `backend/app/api/v1/endpoints/auth.py` - 配置化令牌有效期。
- Modify: `backend/app/knowledge/api/endpoints.py` - 鉴权 multipart 接口。
- Modify: `backend/app/knowledge/models/document.py` - 文档所有者元数据。
- Modify: `backend/app/knowledge/services/{bm25_index,vector_store,hybrid_retriever,knowledge_base,cache,index_manager,factory}.py` - owner 过滤与显式初始化。
- Modify: `backend/app/core/config.py` and `config/default.yaml` - Chroma、上传和 artifact 配置。
- Modify: `backend/app/api/v1/endpoints/health.py` and `backend/app/main.py` - lifecycle 与 readiness。
- Modify: `backend/Dockerfile`, `docker-compose.yml`, `nginx/nginx.conf`, `.env.example` - 自包含部署。
- Modify: `backend/pyproject.toml` - 可安装的运行与模型 extras。

### Task 1: Make Token Expiry Configuration Truthful

**Files:**
- Modify: `backend/tests/test_auth.py`
- Modify: `backend/app/api/v1/endpoints/auth.py`

- [ ] **Step 1: Write the failing login expiry test**

Add to `TestLogin`:

```python
def test_login_expires_in_uses_settings(self, client, monkeypatch) -> None:
    from app.api.v1.endpoints import auth

    monkeypatch.setattr(auth.settings, "jwt_access_token_expire_minutes", 15)
    self._register(client, username="expiryuser")
    response = client.post(
        "/api/v1/auth/login",
        json={"account": "expiryuser", "password": "password123"},
    )
    assert response.status_code == 200
    assert response.json()["expires_in"] == 900
```

- [ ] **Step 2: Run the focused test and verify it fails**

```powershell
Set-Location backend
python -m pytest tests/test_auth.py::TestLogin::test_login_expires_in_uses_settings -v
```

Expected: FAIL because the response still returns `3600`.

- [ ] **Step 3: Replace both hard-coded values**

Import `settings` and use one helper in `auth.py`:

```python
from app.core.config import settings


def _access_token_expires_in() -> int:
    return settings.jwt_access_token_expire_minutes * 60
```

Set `expires_in=_access_token_expires_in()` in both `login()` and `refresh_token()`.

- [ ] **Step 4: Run auth tests**

```powershell
python -m pytest tests/test_auth.py -q
```

Expected: all auth tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/api/v1/endpoints/auth.py backend/tests/test_auth.py
git commit -m "fix(auth): derive token expiry from settings"
```

### Task 2: Add Controlled Document Storage

**Files:**
- Create: `backend/app/knowledge/services/document_storage.py`
- Create: `backend/tests/test_document_storage.py`
- Modify: `backend/app/core/config.py`
- Modify: `config/default.yaml`

- [ ] **Step 1: Write traversal, signature and size tests**

Create `backend/tests/test_document_storage.py`:

```python
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import UploadFile

from app.core.exceptions import ValidationException
from app.knowledge.services.document_storage import DocumentStorage


@pytest.mark.asyncio
async def test_save_ignores_client_path_and_scopes_user(tmp_path) -> None:
    user_id = uuid4()
    storage = DocumentStorage(tmp_path, max_file_bytes=1024)
    upload = UploadFile(filename="../../paper.pdf", file=BytesIO(b"%PDF-1.4\nbody"))

    stored = await storage.save(user_id, upload)

    assert stored.path.parent == tmp_path / str(user_id)
    assert stored.path.name != "paper.pdf"
    assert stored.original_name == "paper.pdf"


@pytest.mark.asyncio
async def test_save_rejects_bad_pdf_signature(tmp_path) -> None:
    storage = DocumentStorage(tmp_path, max_file_bytes=1024)
    upload = UploadFile(filename="paper.pdf", file=BytesIO(b"not-pdf"))
    with pytest.raises(ValidationException, match="文件签名"):
        await storage.save(uuid4(), upload)


@pytest.mark.asyncio
async def test_save_rejects_oversized_file(tmp_path) -> None:
    storage = DocumentStorage(tmp_path, max_file_bytes=4)
    upload = UploadFile(filename="paper.txt", file=BytesIO(b"12345"))
    with pytest.raises(ValidationException, match="30 MB"):
        await storage.save(uuid4(), upload)
```

- [ ] **Step 2: Run tests and verify import failure**

```powershell
Set-Location backend
python -m pytest tests/test_document_storage.py -v
```

Expected: collection FAIL because `DocumentStorage` does not exist.

- [ ] **Step 3: Add storage configuration**

Extend `AppConfig`:

```python
upload_root: str = "/app/data/uploads"
artifact_root: str = "/app/data/artifacts"
max_upload_bytes: int = 30 * 1024 * 1024
```

Add the same values under `app:` in `config/default.yaml`.

- [ ] **Step 4: Implement the storage interface**

Create `document_storage.py` with this public surface:

```python
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import anyio
from fastapi import UploadFile

from app.core.exceptions import NotFoundException, ValidationException


@dataclass(frozen=True)
class StoredDocument:
    path: Path
    original_name: str
    extension: str
    size: int


class DocumentStorage:
    ALLOWED = {".pdf", ".docx", ".md", ".markdown", ".txt"}

    def __init__(self, root: str | Path, *, max_file_bytes: int) -> None:
        self.root = Path(root).resolve()
        self.max_file_bytes = max_file_bytes

    async def save(self, user_id: UUID, upload: UploadFile) -> StoredDocument:
        original = Path(upload.filename or "").name
        extension = Path(original).suffix.lower()
        if extension not in self.ALLOWED:
            raise ValidationException("不支持的文件类型")
        target_dir = self.root / str(user_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{uuid4().hex}{extension}"
        size = 0
        head = b""
        async with await anyio.open_file(target, "wb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > self.max_file_bytes:
                    await output.aclose()
                    target.unlink(missing_ok=True)
                    raise ValidationException("文件超过 30 MB 限制")
                if not head:
                    head = chunk[:8]
                await output.write(chunk)
        self._validate_signature(extension, head, target)
        return StoredDocument(target, original, extension, size)

    def delete(self, user_id: UUID, path: str) -> None:
        candidate = Path(path).resolve()
        allowed_root = (self.root / str(user_id)).resolve()
        if candidate.parent != allowed_root:
            raise NotFoundException("文档不存在")
        candidate.unlink(missing_ok=True)

    @staticmethod
    def _validate_signature(extension: str, head: bytes, target: Path) -> None:
        valid = extension != ".pdf" or head.startswith(b"%PDF-")
        valid = valid and (extension != ".docx" or head.startswith(b"PK\x03\x04"))
        if not valid:
            target.unlink(missing_ok=True)
            raise ValidationException("文件签名与扩展名不一致")
```

Also open DOCX files with `zipfile.ZipFile` and require both `[Content_Types].xml` and `word/document.xml`; decode Markdown/TXT as UTF-8 before accepting them.

- [ ] **Step 5: Run storage tests and quality checks**

```powershell
python -m pytest tests/test_document_storage.py -q
python -m ruff check app/knowledge/services/document_storage.py tests/test_document_storage.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/core/config.py config/default.yaml backend/app/knowledge/services/document_storage.py backend/tests/test_document_storage.py
git commit -m "fix(knowledge): add controlled document storage"
```

### Task 3: Enforce Owner-Scoped Knowledge Records And HTTP Uploads

**Files:**
- Create: `backend/app/db/repositories/knowledge.py`
- Create: `backend/app/schemas/knowledge.py`
- Create: `backend/tests/test_knowledge_repository.py`
- Modify: `backend/app/knowledge/api/endpoints.py`
- Modify: `backend/tests/test_knowledge_api.py`

- [ ] **Step 1: Write repository isolation tests**

Create two `User` rows and two `KnowledgeDoc` rows, then assert:

```python
docs = await KnowledgeRepository(db_session).list_for_user(user_a.id)
assert [doc.id for doc in docs] == [doc_a.id]
assert await KnowledgeRepository(db_session).get_for_user(user_a.id, doc_b.id) is None
```

- [ ] **Step 2: Run the repository test and verify import failure**

```powershell
Set-Location backend
python -m pytest tests/test_knowledge_repository.py -v
```

Expected: FAIL because `KnowledgeRepository` does not exist.

- [ ] **Step 3: Implement owner-scoped queries**

Create `knowledge.py`:

```python
from uuid import UUID

from sqlalchemy import func, select

from app.db.models.knowledge import DocChunk, KnowledgeDoc
from app.db.repositories.base import BaseRepository


class KnowledgeRepository(BaseRepository[KnowledgeDoc]):
    model = KnowledgeDoc

    async def get_for_user(self, user_id: UUID, doc_id: UUID) -> KnowledgeDoc | None:
        result = await self.db.execute(
            select(KnowledgeDoc).where(
                KnowledgeDoc.id == doc_id,
                KnowledgeDoc.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: UUID) -> list[KnowledgeDoc]:
        result = await self.db.execute(
            select(KnowledgeDoc)
            .where(KnowledgeDoc.user_id == user_id)
            .order_by(KnowledgeDoc.created_at.desc())
        )
        return list(result.scalars().all())

    async def chunks_for_user(self, user_id: UUID) -> list[DocChunk]:
        result = await self.db.execute(
            select(DocChunk)
            .join(KnowledgeDoc, KnowledgeDoc.id == DocChunk.doc_id)
            .where(KnowledgeDoc.user_id == user_id)
        )
        return list(result.scalars().all())

    async def stats_for_user(self, user_id: UUID) -> tuple[int, int]:
        docs = await self.db.scalar(
            select(func.count()).select_from(KnowledgeDoc).where(KnowledgeDoc.user_id == user_id)
        )
        chunks = await self.db.scalar(
            select(func.count())
            .select_from(DocChunk)
            .join(KnowledgeDoc, KnowledgeDoc.id == DocChunk.doc_id)
            .where(KnowledgeDoc.user_id == user_id)
        )
        return int(docs or 0), int(chunks or 0)
```

- [ ] **Step 4: Move HTTP schemas out of the endpoint**

In `backend/app/schemas/knowledge.py`, define `KnowledgeDocumentOut`, `KnowledgeDocumentList`, `KnowledgeStatsOut`, `KnowledgeSearchIn`, `KnowledgeSearchItem`, and `KnowledgeSearchOut`. Use `UUID` IDs and `ConfigDict(from_attributes=True)`.

- [ ] **Step 5: Replace JSON `file_path` upload with authenticated multipart**

The route signature must become:

```python
@router.post("/documents", response_model=KnowledgeDocumentOut, status_code=201)
async def upload_document(
    file: UploadFile,
    title: Annotated[str, Form(min_length=1, max_length=256)],
    strategy: Annotated[ChunkStrategy, Form()] = ChunkStrategy.RECURSIVE,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeDocumentOut:
    service = get_document_service(db)
    return await service.ingest(current_user.id, file, title=title, strategy=strategy)
```

Every list, stats, search and delete route must inject `current_user`; deletion must call `get_for_user()` before touching indexes or files. Remove `DocumentUploadRequest.text` and `DocumentUploadRequest.file_path` from the public interface.

- [ ] **Step 6: Rewrite endpoint tests to register and authenticate users**

Use the existing register/login endpoints to create `Authorization: Bearer` headers. Add explicit tests that user B receives 404 for user A's document and cannot retrieve its chunks.

- [ ] **Step 7: Run knowledge API and repository tests**

```powershell
python -m pytest tests/test_knowledge_repository.py tests/test_knowledge_api.py -q
```

Expected: PASS; unauthenticated requests return 401; cross-user requests return 404.

- [ ] **Step 8: Commit**

```powershell
git add backend/app/db/repositories/knowledge.py backend/app/schemas/knowledge.py backend/app/knowledge/api/endpoints.py backend/tests/test_knowledge_repository.py backend/tests/test_knowledge_api.py
git commit -m "fix(knowledge): enforce document ownership"
```

### Task 4: Scope Vector, BM25 And Cache Retrieval By User

**Files:**
- Modify: `backend/app/knowledge/models/document.py`
- Modify: `backend/app/knowledge/services/bm25_index.py`
- Modify: `backend/app/knowledge/services/vector_store.py`
- Modify: `backend/app/knowledge/services/hybrid_retriever.py`
- Modify: `backend/app/knowledge/services/knowledge_base.py`
- Modify: `backend/app/knowledge/services/cache.py`
- Modify: `backend/tests/test_bm25_index.py`
- Modify: `backend/tests/test_hybrid_retriever.py`
- Modify: `backend/tests/test_cache.py`

- [ ] **Step 1: Write cross-user retrieval tests**

Create chunks with `metadata={"user_id": "user-a"}` and `metadata={"user_id": "user-b"}`. Assert each retrieval call returns only its requested owner:

```python
results = index.search("RAG", top_k=10, where={"user_id": "user-a"})
assert {item.metadata["user_id"] for item in results} == {"user-a"}
```

Add a cache test proving equal queries in namespaces `user-a` and `user-b` do not collide.

- [ ] **Step 2: Run focused tests and verify signature failures**

```powershell
Set-Location backend
python -m pytest tests/test_bm25_index.py tests/test_hybrid_retriever.py tests/test_cache.py -q
```

Expected: FAIL because `where` and `namespace` are not accepted.

- [ ] **Step 3: Add one filter contract through the full retrieval path**

Use these exact signatures:

```python
BM25Index.search(query: str, top_k: int = 10, *, where: dict[str, Any] | None = None)
VectorStore.search(query: str, top_k: int = 10, *, where: dict[str, Any] | None = None)
HybridRetriever.search(query: str, top_k: int = 10, *, where: dict[str, Any] | None = None, ...)
KnowledgeBase.query(query: str, top_k: int = 5, *, where: dict[str, Any], namespace: str, ...)
```

BM25 must filter all scored candidates before slicing to `top_k`; filtering only the already sliced global top results is incorrect.

- [ ] **Step 4: Namespace cache keys**

Change the key input to:

```python
raw = f"rag:retrieval:{namespace}:{query}:{top_k}"
```

Require `namespace` in `get()`, `set()` and `invalidate()`. `clear(namespace)` may only delete keys belonging to that namespace; never clear all users after one upload.

- [ ] **Step 5: Run retrieval tests**

```powershell
python -m pytest tests/test_bm25_index.py tests/test_vector_store.py tests/test_hybrid_retriever.py tests/test_knowledge_base.py tests/test_cache.py -q
```

Expected: PASS, including cross-user cases.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/knowledge backend/tests/test_bm25_index.py backend/tests/test_vector_store.py backend/tests/test_hybrid_retriever.py backend/tests/test_knowledge_base.py backend/tests/test_cache.py
git commit -m "fix(rag): isolate retrieval by user"
```

### Task 5: Persist Document Metadata And Initialize RAG Explicitly

**Files:**
- Create: `backend/app/knowledge/services/document_service.py`
- Modify: `backend/app/knowledge/services/index_manager.py`
- Modify: `backend/app/knowledge/services/factory.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_knowledge_api.py`

- [ ] **Step 1: Write the persistence test**

Upload one authenticated TXT file, then assert one `KnowledgeDoc`, at least one `DocChunk`, and matching user metadata in search results. Recreate the service against the same database and assert list/search still work after rebuilding BM25 from persisted chunks.

- [ ] **Step 2: Run the focused test and verify it fails**

```powershell
Set-Location backend
python -m pytest tests/test_knowledge_api.py -k "persists or rebuilds" -v
```

Expected: FAIL because current indexing only writes process memory and Chroma.

- [ ] **Step 3: Expose index preparation without hiding chunks**

Add to `IndexManager`:

```python
def prepare_document(
    self,
    source: str,
    *,
    doc_id: str,
    strategy: ChunkStrategy,
    metadata: dict[str, Any],
) -> tuple[ParsedDocument, list[TextChunk]]:
    document = self._parser.parse(source, doc_id=doc_id)
    document.metadata.update(metadata)
    chunks = self._chunker.chunk(document, strategy=strategy)
    return document, chunks

def index_chunks(self, chunks: list[TextChunk]) -> None:
    self._vector_store.add_chunks(chunks)
    self._bm25_index.add_chunks(chunks)
```

Keep existing `add_document()` as a compatibility wrapper around these methods.

- [ ] **Step 4: Implement the transaction owner**

`KnowledgeDocumentService.ingest()` must:

1. save the upload;
2. create `KnowledgeDoc(index_status="indexing")` and flush;
3. call `prepare_document()` and `index_chunks()` through `asyncio.to_thread`;
4. create `DocChunk` and `VectorIndex` rows;
5. set `index_status="ready"`, `chunk_count`, then commit;
6. on failure remove partial indexes/file, mark the document failed, commit, and raise `ValidationException`.

The public interface is:

```python
class KnowledgeDocumentService:
    async def ingest(self, user_id: UUID, upload: UploadFile, *, title: str, strategy: ChunkStrategy) -> KnowledgeDoc: ...
    async def delete(self, user_id: UUID, doc_id: UUID) -> None: ...
    async def list(self, user_id: UUID) -> list[KnowledgeDoc]: ...
    async def stats(self, user_id: UUID) -> tuple[int, int]: ...
    async def search(self, user_id: UUID, request: KnowledgeSearchIn) -> list[RetrievalResult]: ...
    async def rebuild_bm25(self) -> None: ...
```

- [ ] **Step 5: Remove automatic Mock fallback from production access**

`get_kb()` must raise a clear initialization error if `init_kb()` was not called. `main.lifespan()` must initialize the production embedding, reranker, Chroma client and BM25 index, then rebuild BM25 from persisted ready chunks. Tests explicitly call `init_kb(force_mock_*=True)`.

- [ ] **Step 6: Run RAG and API tests**

```powershell
python -m pytest tests/test_knowledge_api.py tests/test_index_manager.py tests/test_knowledge_base.py -q
```

Expected: PASS; no endpoint lazily creates an in-memory global knowledge base.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/knowledge backend/app/main.py backend/tests/test_knowledge_api.py backend/tests/test_index_manager.py backend/tests/test_knowledge_base.py
git commit -m "fix(knowledge): persist indexed documents"
```

### Task 6: Make Compose Startup Self-Contained And Readiness Real

**Files:**
- Create: `backend/entrypoint.sh`
- Create: `nginx/Dockerfile`
- Create: `backend/tests/test_readiness.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/api/v1/endpoints/health.py`
- Modify: `backend/Dockerfile`
- Modify: `backend/pyproject.toml`
- Modify: `docker-compose.yml`
- Modify: `nginx/nginx.conf`
- Modify: `.env.example`

- [ ] **Step 1: Write readiness dependency tests**

Override database, Redis and Chroma probes. Assert all `True` returns 200 with `status=ready`; one `False` returns 503 with that dependency marked `failed`.

- [ ] **Step 2: Run readiness tests and verify the static endpoint fails**

```powershell
Set-Location backend
python -m pytest tests/test_readiness.py -v
```

Expected: FAIL because readiness always returns skipped dependencies and 200.

- [ ] **Step 3: Add explicit probe functions**

Implement `probe_database()`, `probe_redis()`, and `probe_chroma()` as async dependencies. Database executes `SELECT 1`; Redis calls `RedisManager.ping()`; Chroma calls its heartbeat endpoint through the configured client. Return 503 when any mandatory probe fails. Do not include LLM availability in container readiness.

- [ ] **Step 4: Make Python installation derive from pyproject**

Move `sentence-transformers` and `FlagEmbedding` into a `[project.optional-dependencies].rag-models` group. The backend image installs `./backend[rag-models]`; CI installs `./backend[dev]` and uses Mock embedding/reranker.

- [ ] **Step 5: Add the migration entrypoint**

Create `backend/entrypoint.sh`:

```sh
#!/bin/sh
set -eu
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The backend Dockerfile must run as `appuser`, create `/app/data/uploads` and `/app/data/artifacts`, and use the entrypoint without `--reload`.

- [ ] **Step 6: Build frontend inside the Nginx image**

Create `nginx/Dockerfile`:

```dockerfile
FROM node:20-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:1.25-alpine
COPY nginx/nginx.conf /etc/nginx/nginx.conf
COPY --from=frontend /frontend/dist /usr/share/nginx/html
```

- [ ] **Step 7: Correct Compose environment names and exposure**

Use nested settings names such as `DATABASE__URL`, `REDIS__URL`, `RAG__CHROMA_HOST`, and `RAG__CHROMA_PORT`. Require `JWT_SECRET_KEY` through `.env`; remove the production fallback. Only Nginx exposes a host port. Add named volumes for uploads, artifacts and model cache. Backend depends on migrations through its entrypoint, not a manual operator step.

- [ ] **Step 8: Route WebSocket upgrades under `/api/`**

Remove the mismatched `/ws/` Nginx location. In `/api/`, set upgrade headers using a `map $http_upgrade $connection_upgrade` block so both HTTP and `/api/v1/tasks/{id}/ws` work.

- [ ] **Step 9: Verify build and readiness**

```powershell
docker compose config
docker compose build
docker compose up -d
Invoke-RestMethod http://localhost/api/v1/health/ready
```

Expected: config/build succeed; readiness returns `database=ok`, `redis=ok`, `chroma=ok`; `/` serves the Vue build.

- [ ] **Step 10: Run regression tests**

```powershell
Set-Location backend
python -m pytest tests -q
python -m black --check app tests
python -m ruff check app tests
```

Expected: PASS.

- [ ] **Step 11: Commit**

```powershell
git add backend/Dockerfile backend/entrypoint.sh backend/pyproject.toml backend/app/core/config.py backend/app/api/v1/endpoints/health.py backend/tests/test_readiness.py docker-compose.yml nginx/Dockerfile nginx/nginx.conf .env.example config/default.yaml
git commit -m "build(docker): make compose startup self-contained"
```

## Plan 1 Exit Gate

Run:

```powershell
Set-Location backend
python -m pytest tests -q
Set-Location ..
docker compose up -d --build
```

Then verify two users cannot list, delete or search each other's documents. Do not start Plan 2 until this gate passes and the result is recorded in the implementation handoff.
