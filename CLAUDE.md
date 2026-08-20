# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Reference docs

This file stays short and always-relevant (goal, stack, infra, commands, architecture). Anything that changes per-feature or per-date lives in one of these — read them when the task needs that level of detail, and **update them when you land a feature**, not this file:

| File | Role | Read when |
|---|---|---|
| [docs/STATUS.md](docs/STATUS.md) | Feature-by-feature snapshot of what's actually implemented today (dated entries) | before claiming a feature exists/doesn't; after shipping one — add an entry |
| [docs/ROADMAP.md](docs/ROADMAP.md) | MVP phase plan (current position) + undated backlog | deciding what's next, or which phase a new feature belongs to |
| [docs/DESIGN.md](docs/DESIGN.md) | Design rationale, DB schema, benchmark plan | the "why" behind an architecture choice matters |
| [docs/api-spec.md](docs/api-spec.md) | API contract + per-feature build status | adding/changing an endpoint's contract |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | Generated per-endpoint request/response reference (from `/openapi.json`) | need the exact request/response shape; regenerate after route/schema changes |

## Project status

This repository is the **backend** for the **AX Document Platform** — a project-scoped document platform that ingests business documents into a shared **Document Core** (File → Version → Section → Chunk → Embedding) and layers AI features on top: proofread (오탈자 검증), auto-classification, version diff, regulation validation, RAG chat, and related-document recommendation. It sits under a parent monorepo-style directory (`sidepjt/`) alongside sibling folders `frontend/` (Next.js, its own git repo), `embedding/`, `storage/`, `models/`, and `docker/`, with the root `docker-compose.yml` orchestrating them from the parent.

**MVP1 (ingest path + proofread) backend is functionally complete**; MVP2 (local embedding + RAG) is next. For the full, dated, feature-by-feature breakdown of what's actually implemented vs. not, see **[docs/STATUS.md](docs/STATUS.md)** — that's the source of truth, don't assume this paragraph stays current.

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

Docker Compose lives in the **parent directory** (`sidepjt/docker-compose.yml`, the folder the docs call `sidepjt/`), not in `backend/`. It orchestrates the whole platform (db + backend today; frontend/embedding/storage as commented placeholders). Run it from the parent:

```bash
cd ..            # into the sidepjt root
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
│   ├── ai_analysis_result.py   # shared AI-result table (proofread/classify/validate/related)
│   └── activity_log.py     # append-only activity feed (see api-spec.md §2) — FK-less by design
├── schemas/                # Pydantic request/response — project, folder, auth, document, dashboard, proofread — snake_case (dashboard.py aliases to camelCase on output, see api-spec.md §2)
├── repositories/           # DB access per aggregate — project_, folder_, user_, document_, activity_, dashboard_, analysis_
├── services/               # business logic + get_*_service() DI factories
│   ├── project_service.py  folder_service.py  document_service.py
│   ├── parsing_service.py   # run_parse_task (BackgroundTasks entry): parse → chunk, status transitions
│   ├── chunking_service.py  # structure-aware chunking (heading breadcrumb, sentence split + overlap)
│   ├── proofread_service.py # Phase 8: sections → LLMProvider → ai_analysis_results; finding id/status + rehydrate_findings
│   ├── correction_service.py # apply accepted findings → DOCX/HWPX real edit, PDF visual patch, TXT/MD splice → new version
│   ├── dashboard_service.py # dashboard summary aggregation (stats/weekly/pipeline)
│   ├── activity_service.py  # activity_log writer/reader — describe() maps code→label/kind, activity_service_for() for non-request contexts (background jobs, OAuth callback)
│   ├── llm/                 # LLMProvider: base, mock_provider, openai_provider, factory(get_llm_provider), prompts
│   ├── parsing/             # format adapters: base, registry, text/docx/pdf/hwpx_parser, ocr (Tesseract + OpenCV preprocess)
│   ├── storage.py           # StorageProvider (LocalStorageProvider today; MinIO/S3 later)
│   └── auth_service.py  auth_providers.py   # MockProvider / GoogleProvider behind one interface
├── api/
│   ├── deps.py             # get_current_user (HTTPBearer)
│   └── v1/
│       ├── router.py       # aggregates module routers — register new ones here
│       ├── health.py  auth.py  dashboard.py  projects.py  folders.py  documents.py  proofread.py
alembic/                     # migrations (run: alembic upgrade head)
docs/                        # STATUS.md (what's built), ROADMAP.md (phases+backlog), DESIGN.md (design), api-spec.md (contract), API_REFERENCE.md (generated per-endpoint ref)
```

When adding a feature, follow the layered flow: `schemas/` (I/O) → `repositories/` (DB) → `services/` (logic + `get_*_service()` DI) → `api/v1/<feature>.py` (routes) registered in `api/v1/router.py`. Protect routes with `Depends(get_current_user)`. **Always add new models to `app/models/__init__.py`** or Alembic autogenerate won't see them. Shared plumbing every AI module reuses (Document Core, the `EmbeddingProvider` interface, GPT client) lives in shared `services/`, not inside one feature module.

The frontend mirrors these schemas by hand in `../frontend/src/lib/types.ts` (no shared type generation across the two repos) — update both sides when changing the API contract. Swagger (`/docs`) is the live contract: every endpoint carries a summary, description, and per-parameter docs.

## Development phases

The platform is built in five MVP phases (Document Core+proofread → local embedding+RAG → classification/recommend → version diff → regulation validation). Current position: **MVP1 backend done, MVP2 next.** Full phase list, current position detail, and the undated feature backlog live in **[docs/ROADMAP.md](docs/ROADMAP.md)**; DB schema and benchmark plan behind the phases are in [docs/DESIGN.md](docs/DESIGN.md).
