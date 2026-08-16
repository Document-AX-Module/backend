# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repository is the **backend** for the **AX Document Platform** — a project-scoped document platform that ingests business documents into a shared **Document Core** (File → Version → Section → Chunk → Embedding) and layers AI features on top: proofread (오탈자 검증), auto-classification, version diff, regulation validation, RAG chat, and related-document recommendation. It sits under a parent monorepo-style directory (`sidepjt/`) alongside sibling folders `frontend/` (Next.js, its own git repo), `embedding/`, `storage/`, `models/`, and `docker/`, with the root `docker-compose.yml` orchestrating them from the parent.

**What actually exists today** (don't overstate):
- **DB / migrations**: the full Document Core schema (8 tables: projects, folders, documents, document_versions, document_sections, document_chunks, embedding_models, chunk_embeddings) + a `users` table, all created via **Alembic** migrations. `chunk_embeddings.embedding` is `pgvector.Vector(1024)`.
- **Auth**: Google OAuth + backend-issued JWT (access via `Authorization: Bearer`, refresh in httpOnly cookie), plus a `mock` provider for dev login without credentials. `get_current_user` dependency in `app/api/deps.py` (role-based gating isn't wired yet — add it when the first admin-only route lands).
- **Projects & Folders**: full CRUD, **auth-protected**, layered as `router → service → repository → model`. `created_by` is filled server-side from the logged-in user. Folder moves are cycle-checked.
- **Document Upload (Phase 5)**: multipart upload → validates extension/size → stores file via `StorageProvider` (local, under `storage/`) → creates `document` + `document_version` (v1, sha256) → `202`. Plus list/detail/versions/download/status/delete, all auth-protected. Detail & list are enriched with `author` (created_by id → name/email) and `current_version`.
- **Parse pipeline (Phase 6)**: a `BackgroundTasks` job (`run_parse_task`) parses the uploaded file into `document_sections` (hierarchy via parent_section_id + a `level` column — heading depth 1-9, body/table = 100; page/bbox in `meta` JSONB). **Section rule** (normalized in `parsing_service._persist_sections`, format-agnostic): `heading` sections carry `title` only (`content` emptied to avoid duplicate display); body/table carry `content` only. Format adapters in `app/services/parsing/`: TXT/MD, DOCX (python-docx, heading styles + tables), PDF (PyMuPDF native text + bbox + font-size heading heuristic, **+ Tesseract OCR fallback for scanned pages**, kor+eng), HWPX (OWPML XML). `GET /versions/{id}/sections` exposes `level` so the frontend can render a collapsible tree.
- **Chunking (Phase 7)**: same job continues into `document_chunks` — structure-aware (skips headings, carries a `heading_path` breadcrumb, sentence-split with overlap only when a section exceeds `chunk_max_chars`). `GET /versions/{id}/chunks`. Status flow: `UPLOADED→PARSING→PARSED→CHUNKING→CHUNKED` (terminal for now; `EMBEDDING→READY` comes with Phase 9).
- **Proofread (Phase 8, MVP1's last step) — now wired to real GPT, async + batch**: `POST /documents/{id}/proofread` validates + creates a `RUNNING` row and returns **`202` immediately**; a `BackgroundTasks` job (`run_proofread_task`) then runs the current version's parsed sections through an `LLMProvider` and updates the row to `COMPLETED`/`FAILED`. Frontend polls `GET /documents/{id}/analyses/{analysis_id}` (status RUNNING→COMPLETED). **Batching**: sections are grouped (`proofread_batch_max_chars`/`_max_sections`) and sent in one prompt via `[n]` markers → results remapped by index (11 sections: sequential ~17s → one batch ~7.5s, 11 calls → 1). Each finding: `original→suggestion`, `reason`, `category`, + `section_id`/page. Results persist to a shared `ai_analysis_results` table (analysis_type=`proofread`; classify/validate/related reuse it). Provider abstraction in `app/services/llm/`: `MockLLMProvider` (rule-based, no key) + `OpenAILLMProvider` (httpx Chat Completions, `proofread`/`proofread_batch`); prompts live in `app/services/llm/prompts.py`. `LLM_PROVIDER=auto` auto-selects by `OPENAI_API_KEY` presence — **currently a real key is set, so proofread hits OpenAI GPT** (model via `OPENAI_MODEL`, e.g. `gpt-5.6-luna`; gpt-5.x models reject custom `temperature`, so it's omitted). `.env` change needs a **server restart** (env is read at startup; `--reload` only reloads code). See [[proofread-gpt-setup]].
- **health**: `/api/v1/health` pings the DB.
- **Not yet**: embedding / RAG (Phase 9); document `PATCH` + new-version (v2+) upload + SSE; project list aggregate fields; `members` (membership table); real created_by FK; parsers for hwp/xlsx/pptx (those upload but `FAILED` on parse). Frontend wiring for the async proofread poll + section tree render is still pending.

The full design lives in [docs/DESIGN.md](docs/DESIGN.md), the API contract + build status in [docs/api-spec.md](docs/api-spec.md); the platform is being built in phases (see "Development phases" below).

## Project goal

**AX Document Platform** — 프로젝트별 비정형 문서를 Document Core로 통합 관리하고, 로컬 Embedding + PostgreSQL/pgvector 검색과 GPT 분석을 결합해 문서 검색·분류·비교·규정검증·RAG·관련문서 추천을 제공하는 확장형 AI 문서 플랫폼.

Near-term release target: **2026-08-31** (carried over from the original spell-check phase 1; re-confirm against the AX Document Platform scope). The MVP 1 slice is the document ingest path plus the first AI module (proofread):

```
AX Document Platform
├─ Frontend (Next.js)
├─ Backend API (FastAPI)
├─ Document Core        # File · Version · Section · Chunk · Metadata (shared by every AI module)
├─ Local Embedding      # local model (BGE-M3 / KURE-v1 / Qwen3 candidates), NOT OpenAI embeddings
├─ LLM = OpenAI GPT API # proofread, classify, summarize, compare, validate, RAG answer
└─ AI Modules (behind a common Service/interface split)
    ├─ Proofread (오탈자 검증)   # first module = MVP 1
    ├─ RAG Chat                  # MVP 2
    ├─ Classification / Recommend # MVP 3
    ├─ Version Diff               # MVP 4
    └─ Regulation Validation      # MVP 5
```

**Key architectural principles** (from the design doc — keep these when shaping module boundaries):
- **Document Core is the center** — ingest once (Upload → Parser → Section → Chunk); every AI feature reuses the same document data.
- **Don't couple to a specific embedding model** — go through `RAGService → EmbeddingService → EmbeddingProvider`; models (BGE/KURE/Qwen/future) are swappable behind the interface.
- **Separate Chunk from Embedding** — `chunk` 1:N `chunk_embedding` so multiple models can be compared side by side and swapped safely (rollback-able). Embedding is NOT stored on the chunk row.
- **Local embedding vs GPT split** — local model does retrieval/search; GPT does analysis/judgement/generation.
- **Pick the embedding model on real data** — benchmark candidates against actual project docs/queries, not public leaderboards.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | Next.js (TypeScript) |
| Backend API | Python, FastAPI |
| Database | PostgreSQL |
| Vector DB | pgvector |
| Embedding | Local model (BGE-M3 / KURE-v1 / Qwen3-Embedding candidates, 1024-dim) — self-hosted, not OpenAI |
| LLM | OpenAI GPT API |
| DB GUI | DBeaver Community |
| Container | Docker |
| Repo / collaboration | GitHub |

Data flow: `Next.js/TypeScript → FastAPI → AI Module Layer (Document Core + Local Embedding + GPT) → PostgreSQL + pgvector`

## Infrastructure notes

- Target deployment is a company **local desktop server**, containerized via Docker Compose (Frontend + Backend + PostgreSQL/pgvector + local model storage). External access, backup strategy, and HTTPS/domain are still open decisions.
- PostgreSQL/pgvector hosting: self-hosted vs. Supabase is still under evaluation (cost/plan not finalized). Don't assume Supabase is committed until confirmed.
- **DB naming:** the running stack uses DB `ai_platform` / user `postgres` (see root `.env`). The design doc ([docs/DESIGN.md](docs/DESIGN.md)) writes these as `ax_document` / `ax_user` — those are illustrative names in the design, **not** the current config. Don't "fix" the running values to match the doc unless we deliberately migrate.
- **Local embedding models** are meant to live under the sibling `models/` folder (`models/embeddings/{bge-m3,kure-v1,qwen3-...}`) and be mounted into the embedding service — prepared once at setup, not downloaded per request. Not wired up yet.
- **GPT** uses the OpenAI API (needs an API key in env once the first GPT-backed module is built). Embeddings are local, NOT OpenAI.
- **DBeaver Community** is the intended DB GUI client (Host `localhost`, Port `5432`, DB `ai_platform`) — it's just a client; the DB lives in the Docker `db_data` volume.

## Commands

Package manager is **pip + requirements.txt** (deliberately chosen over poetry/uv for this project).

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload                      # dev server at http://localhost:8000
```

Docker Compose lives in the **parent directory** (`AX-Module/docker-compose.yml`, the folder the docs call `sidepjt/`), not in `backend/`. It orchestrates the whole platform (db + backend today; frontend/embedding/storage as commented placeholders). Run it from the parent:

```bash
cd ..            # into the AX-Module root
docker compose up --build
```

The root compose builds the backend via `build: ./backend` (backend keeps its own `Dockerfile`) and reads `./backend/.env` (app secrets: `JWT_SECRET`, `GOOGLE_CLIENT_ID/SECRET`, `AUTH_PROVIDERS`, ...). `DATABASE_URL` is set in compose `environment:` (overrides `.env`) so the container always talks to the `db` service. `db` uses `pgvector/pgvector:pg16`. **Two env files are needed:** the root `.env` (compose-level: `POSTGRES_*`, `*_PORT`, `CORS_ALLOW_ORIGINS` — copy from the root `.env.example`) and `backend/.env` (app secrets — copy from `backend/.env.example`). Both before running either the bare `uvicorn` command or `docker compose up` (Compose errors if the `env_file` is missing). **`backend/.env` holds real secrets and is gitignored — never commit it; keep `.env.example` in sync with placeholders.** Note the root directory is *not* a git repo (only `backend/` and `frontend/` are), so the root `.env`/`.env.example` aren't version-controlled — carry them by hand when moving machines.

**Database migrations (Alembic).** Schema is managed by migrations in `alembic/`, never hand-DDL in DBeaver. Run from `backend/` (host `.venv`, hits `localhost:5432`):

```bash
alembic revision --autogenerate -m "message"   # after editing models (and registering in app/models/__init__.py)
alembic upgrade head                            # apply
```

Team onboarding: `cd .. && docker compose up --build`, then `cd backend && alembic upgrade head`. **When to rebuild vs restart:** editing `app/` code needs neither — the container runs uvicorn with `--reload` (dev override in compose `command:`) and the `./backend/app` volume mount, so saves auto-reload. Rebuild the image (`docker compose up -d --build backend`) only when `requirements.txt` (new pip deps) or the `Dockerfile` (system deps like tesseract) changes. The `env.py` renders `pgvector.Vector` and the first migration self-creates the `vector` extension.

FastAPI auto-generates interactive API docs at `/docs` (Swagger UI) and `/redoc` (ReDoc) once the server is running — no extra setup needed. Note: browser-navigation endpoints (`/auth/login/*`, `/auth/callback/*`) return redirects and will fail from Swagger's "Try it out" (fetch/CORS) — test them in the browser address bar instead.

No test runner or linter is configured yet — add one (e.g. pytest, ruff) when the first tests/lint rules are introduced, and update this section.

**Environment note:** as of 2026-08-14 Python 3.12.10 is installed at `C:\Users\pcn\AppData\Local\Programs\Python\Python312` (the bare `python` alias may still resolve to the Windows Store stub — use the full path or an activated `.venv` if so). Docker availability on this machine is still unverified — confirm before relying on the compose commands.

## Architecture

Module-per-feature layout, so new AI features (proofread, RAG, classification, diff, validation, ...) can be added without reworking existing code. **This is the current, actually-present structure:**

The design-doc layered split (`api / core / models / schemas / repositories / services`) is now the **actual** structure. A feature flows `router → service → repository → model`, with Pydantic `schemas` at the edge:

```
app/
├── main.py                 # FastAPI app, CORS; imports app.models (mapper registration), mounts api_router
├── core/
│   ├── config.py           # pydantic-settings Settings (env-driven): DB, JWT, Google OAuth, cookies
│   ├── database.py         # engine/session, Base (naming convention), get_db()
│   └── security.py         # JWT create/decode (access + refresh)
├── models/                 # SQLAlchemy tables — ADD every new model to models/__init__ (Alembic discovery)
│   ├── mixins.py           #   CreatedAtMixin / TimestampMixin
│   ├── user.py  project.py  folder.py
│   ├── document.py  document_version.py  document_section.py  document_chunk.py
│   ├── embedding_model.py  chunk_embedding.py   # chunk_embedding.embedding = Vector(1024)
│   └── ai_analysis_result.py   # shared AI-result table (proofread/classify/validate/related)
├── schemas/                # Pydantic request/response (project, folder, auth, document) — snake_case
├── repositories/           # DB access per aggregate (project_, folder_, user_, document_)
├── services/               # business logic + get_*_service() DI factories
│   ├── project_service.py  folder_service.py  document_service.py
│   ├── parsing_service.py   # run_parse_task (BackgroundTasks entry): parse → chunk, status transitions
│   ├── chunking_service.py  # structure-aware chunking (heading breadcrumb, sentence split + overlap)
│   ├── proofread_service.py # Phase 8: sections → LLMProvider → ai_analysis_results
│   ├── llm/                 # LLMProvider: base, mock_provider, openai_provider, factory(get_llm_provider), prompts
│   ├── parsing/             # format adapters: base, registry, text/docx/pdf/hwpx_parser, ocr (Tesseract)
│   ├── storage.py           # StorageProvider (LocalStorageProvider today; MinIO/S3 later)
│   └── auth_service.py  auth_providers.py   # MockProvider / GoogleProvider behind one interface
├── api/
│   ├── deps.py             # get_current_user (HTTPBearer)
│   └── v1/
│       ├── router.py       # aggregates module routers — register new ones here
│       ├── health.py  auth.py  projects.py  folders.py  documents.py  proofread.py
alembic/                     # migrations (run: alembic upgrade head)
docs/                        # DESIGN.md (design), api-spec.md (contract + build status)
```

When adding a feature, follow the layered flow: `schemas/` (I/O) → `repositories/` (DB) → `services/` (logic + `get_*_service()` DI) → `api/v1/<feature>.py` (routes) registered in `api/v1/router.py`. Protect routes with `Depends(get_current_user)`. **Always add new models to `app/models/__init__.py`** or Alembic autogenerate won't see them. Shared plumbing every AI module reuses (Document Core, the `EmbeddingProvider` interface, GPT client) lives in shared `services/`, not inside one feature module.

The frontend mirrors these schemas by hand in `../frontend/src/lib/types.ts` (no shared type generation across the two repos) — update both sides when changing the API contract. Swagger (`/docs`) is the live contract: every endpoint carries a summary, description, and per-parameter docs.

## Development phases

The platform is built in phases; MVP 1 is the ingest path + proofread. Full detail (DB schema, benchmark plan, per-feature flows) is in [docs/DESIGN.md](docs/DESIGN.md). High-level order:

1. **MVP 1** — Docker PG/pgvector · FastAPI · Next.js · Project/Folder · Document Upload · Parser (PDF/HWPX/DOCX → Unified Document) · **Document Core** (Document→Version→Section→Chunk) · GPT proofread. *Current position: DB schema + Alembic, auth (Google+JWT), Project & Folder CRUD, Document Upload + Storage (P5), **Parser incl. OCR (P6)**, **Chunking (P7)**, and **Proofread (P8)** all done — the full ingest path Upload→Parse→Chunk plus the first AI module (proofread) work end-to-end. **MVP1 backend is functionally complete** — proofread is now wired to **real OpenAI GPT** (async 202+polling + batched calls); the mock provider remains as the keyless fallback. Remaining before calling MVP1 fully closed: frontend wiring (async proofread poll + section tree). **Next big step: MVP2 (local embedding + RAG, Phase 9).***
2. **MVP 2** — Local embedding env → connect BGE-M3/KURE-v1/Qwen3 behind one `EmbeddingProvider` interface → benchmark on real project docs (`chunk_embeddings` keyed by `embedding_model_id` so models compare side by side) → pick final model → embed all chunks → pgvector Vector Search → RAG Chat (hybrid vector + full-text, with citations).
3. **MVP 3** — Auto-classification · related-document recommendation (reuse Document Core + embeddings).
4. **MVP 4** — Version management · Document Diff.
5. **MVP 5** — RULE-type documents · regulation validation.
