# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repository is the **backend** for the **AX Document Platform** — a project-scoped document platform that ingests business documents into a shared **Document Core** (File → Version → Section → Chunk → Embedding) and layers AI features on top: proofread (오탈자 검증), auto-classification, version diff, regulation validation, RAG chat, and related-document recommendation. It sits under a parent monorepo-style directory (`sidepjt/`) alongside sibling folders `frontend/` (Next.js, its own git repo), `embedding/`, `storage/`, `models/`, and `docker/`, with the root `docker-compose.yml` orchestrating them from the parent.

**What actually exists today** (don't overstate):
- **DB / migrations**: the full Document Core schema (8 tables: projects, folders, documents, document_versions, document_sections, document_chunks, embedding_models, chunk_embeddings) + a `users` table, all created via **Alembic** migrations. `chunk_embeddings.embedding` is `pgvector.Vector(1024)`.
- **Auth**: Google OAuth + backend-issued JWT (access via `Authorization: Bearer`, refresh in httpOnly cookie), plus a `mock` provider for dev login without credentials. `get_current_user` dependency in `app/api/deps.py` (role-based gating isn't wired yet — add it when the first admin-only route lands).
- **Projects & Folders**: full CRUD, **auth-protected**, layered as `router → service → repository → model`. `created_by` is filled server-side from the logged-in user. Folder moves are cycle-checked.
- **Document Upload (Phase 5)**: multipart upload → validates extension/size → stores file via `StorageProvider` (local, under `storage/`) → creates `document` + `document_version` (v1, sha256) → `202`. Plus list/detail/versions/download/status/delete, all auth-protected. Detail & list are enriched with `author` (created_by id → name/email) and `current_version`.
- **Parse pipeline (Phase 6)**: a `BackgroundTasks` job (`run_parse_task`) parses the uploaded file into `document_sections` (hierarchy via parent_section_id + a `level` column — heading depth 1-9, body/table = 100; page/bbox in `meta` JSONB). **Section rule** (normalized in `parsing_service._persist_sections`, format-agnostic): `heading` sections carry `title` only (`content` emptied to avoid duplicate display); body/table carry `content` only. Format adapters in `app/services/parsing/`: TXT/MD, DOCX (python-docx, heading styles + tables), PDF (PyMuPDF native text + bbox + font-size heading heuristic, **+ Tesseract OCR fallback for scanned pages**, kor+eng), HWPX (OWPML XML), PPTX (python-pptx, slide→shape→paragraph, recursing into group shapes; title placeholder = heading, tables = table), XLSX (openpyxl, read-only — sheet name = heading, sheet data = one table section per sheet; no `apply` support yet, see backlog). `GET /versions/{id}/sections` exposes `level` so the frontend can render a collapsible tree.
- **OCR quality (scanned PDF pages)**: the OCR path (`app/services/parsing/ocr.py`, `pdf_parser.py`) now mirrors the native-text pipeline instead of dumping every line as flat body text. `preprocess_for_ocr()` runs grayscale → deskew (`minAreaRect` angle estimate, ±15° cap) → CLAHE contrast before Tesseract (no hard binarization — Tesseract's own LSTM Otsu step does better on grayscale input); needs `opencv-python-headless` (added to `requirements.txt`, no Dockerfile change since it's headless). Heading detection on OCR'd lines uses the same line-height-vs-median-ratio heuristic (×1.3) as the native path's font-size heuristic. Each OCR line below `OCR_LOW_CONFIDENCE_THRESHOLD` (60) gets `meta.low_confidence=true` (existing `document_sections.meta` JSONB, no migration) and the document-level ratio is logged as a warning at ≥30% low-confidence lines — server-log-only, no UI badge wired yet.
- **Chunking (Phase 7)**: same job continues into `document_chunks` — structure-aware (skips headings, carries a `heading_path` breadcrumb, sentence-split with overlap only when a section exceeds `chunk_max_chars`). `GET /versions/{id}/chunks`. Status flow: `UPLOADED→PARSING→PARSED→CHUNKING→CHUNKED` (terminal for now; `EMBEDDING→READY` comes with Phase 9).
- **Proofread (Phase 8, MVP1's last step) — now wired to real GPT, async + batch**: `POST /documents/{id}/proofread` validates + creates a `RUNNING` row and returns **`202` immediately**; a `BackgroundTasks` job (`run_proofread_task`) then runs the current version's parsed sections through an `LLMProvider` and updates the row to `COMPLETED`/`FAILED`. Frontend polls `GET /documents/{id}/analyses/{analysis_id}` (status RUNNING→COMPLETED). **Batching**: sections are grouped (`proofread_batch_max_chars`/`_max_sections`) and sent in one prompt via `[n]` markers → results remapped by index (11 sections: sequential ~17s → one batch ~7.5s, 11 calls → 1). Each finding: `original→suggestion`, `reason`, `category`, + `section_id`/page. **Scope is deliberately narrow** — `PROOFREAD_CATEGORIES` defaults to `["spelling","spacing"]` because on longer docs GPT floods the results with "make it flow better" expression/context notes and buries the actual typos (measured on one doc: 12 findings → 9, all 4 expression/grammar ones gone). The prompt narrows the target list, the forbidden list, **and the category enum together** — leaving the enum open just makes the model relabel excluded findings into whatever buckets remain. Anything that still leaks through is dropped by the provider and logged; unknown labels are coerced to the default category rather than dropped, so a mislabelled typo isn't lost. Add all four to `.env` to get expression fixes back. Results persist to a shared `ai_analysis_results` table (analysis_type=`proofread`; classify/validate/related reuse it). Provider abstraction in `app/services/llm/`: `MockLLMProvider` (rule-based, no key) + `OpenAILLMProvider` (httpx Chat Completions, `proofread`/`proofread_batch`); prompts live in `app/services/llm/prompts.py`. `LLM_PROVIDER=auto` auto-selects by `OPENAI_API_KEY` presence — **currently a real key is set, so proofread hits OpenAI GPT** (model via `OPENAI_MODEL`, e.g. `gpt-5.6-luna`; gpt-5.x models reject custom `temperature`, so it's omitted). `.env` change needs a **server restart** (env is read at startup; `--reload` only reloads code). See [[proofread-gpt-setup]].
- **Correction apply (new)**: findings now carry a stable `id` + accept/reject `status` (`PATCH /documents/{id}/analyses/{analysis_id}/findings/{finding_id}`, still just the same `result` JSONB, no migration). `POST /documents/{id}/analyses/{analysis_id}/apply` takes every `accepted` finding and writes a **new document version** — `app/services/correction_service.py` dispatches by extension: DOCX/HWPX/PPTX get a real text edit via `_replace_span_in_nodes` (a run/XML-text-node-boundary-aware substitution shared by all three formats — only the run(s)/`<hp:t>` node(s) the matched span actually touches get rewritten, so formatting outside the edited span survives; python-docx `Run`, python-pptx run, and `ElementTree` `Element` all just need `.text` get/set, so one function serves all three; table sections (all three formats) resolve to the one cell containing the match via `_apply_*_table_cell` before calling `_replace_span_in_nodes`, so a span is never searched for across cell boundaries), PDF gets a best-effort visual patch (bbox `add_redact_annot`+`apply_redactions` then `insert_textbox`, since PDF can't be losslessly edited), TXT/MD get a block-offset string splice. The new version re-runs the existing `run_parse_task` (parse+chunk), so corrected sections/chunks just fall out of the normal pipeline — no hand-patching DB content. One-shot per analysis: `result.applied_version_id` guards a second `apply` call (409). `DocumentService.create_version()` is the new v2+-from-bytes helper (`upload_document` still owns v1-with-a-new-Document). No frontend wiring yet (accept/reject UI, apply button).
- **Dashboard & activity log (2026-08-16)**: `GET /dashboard/summary`, `GET /activity`, `GET /documents/recent` — stats/weekly-processing/pipeline-status aggregation + an append-only `activity_logs` table (12 action codes, no FKs — deletion doesn't cascade the log away, `actor_label`/`target_label` snapshot names so history survives deletes). Dashboard response schemas alias to camelCase (rest of the API stays snake_case) to match a frontend contract set in advance. See api-spec.md §2 for the `targetType`/`targetId`/`targetAlive` link-safety rule frontend must apply. `GET /rag/history` (also under this section in the spec) is still ⏳ — no RAG yet to have history of.
- **health**: `/api/v1/health` pings the DB.
- **Not yet**: embedding / RAG (Phase 9); `PATCH /documents/{id}` (rename/move/retype); a general user-facing "upload a new version of this doc" endpoint — v2+ creation as a *mechanism* now exists (`DocumentService.create_version`) but is only reachable today via the proofread-apply flow above, not its own upload route; SSE; project list aggregate fields; `members` (membership table); real created_by FK; a parser for hwp (uploads but `FAILED` on parse; pptx and xlsx now have parsers, see backlog below); `apply`(교정 적용) for XLSX (parse/조회만 지원, 표 셀 교정 반영은 아직). Frontend wiring for the async proofread poll + section tree render + accept/reject/apply UI is still pending.

The full design lives in [docs/DESIGN.md](docs/DESIGN.md), the API contract + build status in [docs/api-spec.md](docs/api-spec.md), and the full per-endpoint role/request/response reference in [docs/API_REFERENCE.md](docs/API_REFERENCE.md) (generated from the live `/openapi.json` — regenerate when routes/schemas change); the platform is being built in phases (see "Development phases" below).

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
docs/                        # DESIGN.md (design), api-spec.md (contract + build status)
```

When adding a feature, follow the layered flow: `schemas/` (I/O) → `repositories/` (DB) → `services/` (logic + `get_*_service()` DI) → `api/v1/<feature>.py` (routes) registered in `api/v1/router.py`. Protect routes with `Depends(get_current_user)`. **Always add new models to `app/models/__init__.py`** or Alembic autogenerate won't see them. Shared plumbing every AI module reuses (Document Core, the `EmbeddingProvider` interface, GPT client) lives in shared `services/`, not inside one feature module.

The frontend mirrors these schemas by hand in `../frontend/src/lib/types.ts` (no shared type generation across the two repos) — update both sides when changing the API contract. Swagger (`/docs`) is the live contract: every endpoint carries a summary, description, and per-parameter docs.

## Development phases

The platform is built in phases; MVP 1 is the ingest path + proofread. Full detail (DB schema, benchmark plan, per-feature flows) is in [docs/DESIGN.md](docs/DESIGN.md). High-level order:

1. **MVP 1** — Docker PG/pgvector · FastAPI · Next.js · Project/Folder · Document Upload · Parser (PDF/HWPX/DOCX → Unified Document) · **Document Core** (Document→Version→Section→Chunk) · GPT proofread. *Current position: DB schema + Alembic, auth (Google+JWT), Project & Folder CRUD, Document Upload + Storage (P5), **Parser incl. OCR (P6)**, **Chunking (P7)**, and **Proofread (P8)** all done — the full ingest path Upload→Parse→Chunk plus the first AI module (proofread) work end-to-end. **MVP1 backend is functionally complete** — proofread is now wired to **real OpenAI GPT** (async 202+polling + batched calls), scanned-PDF OCR got heading detection/quality-flagging/preprocessing, and findings can now be accepted/rejected and applied back to the source file as a new version (DOCX/HWPX real edit, PDF visual patch, TXT/MD splice); the mock provider remains as the keyless fallback. Remaining before calling MVP1 fully closed: frontend wiring (async proofread poll + section tree + accept/reject/apply UI). **Next big step: MVP2 (local embedding + RAG, Phase 9).***
2. **MVP 2** — Local embedding env → connect BGE-M3/KURE-v1/Qwen3 behind one `EmbeddingProvider` interface → benchmark on real project docs (`chunk_embeddings` keyed by `embedding_model_id` so models compare side by side) → pick final model → embed all chunks → pgvector Vector Search → RAG Chat (hybrid vector + full-text, with citations). **Scope now includes Quiz** (2026-08-19 확정) — RAG retrieval 위에 문제/문제집 생성·수정·커스텀·직접생성, 답변+해설, 히스토리를 얹는다. 같은 Retrieval Engine 재사용, 별도 채점/문제집 저장 스키마는 착수 시 설계.
3. **MVP 3** — Auto-classification · related-document recommendation (reuse Document Core + embeddings).
4. **MVP 4** — Version management · Document Diff.
5. **MVP 5** — RULE-type documents · regulation validation.

### 로드맵 백로그 (2026-08-19 확정, 우선순위·순서 미정)

아직 어느 MVP phase로 편입할지 확정하지 않은 항목들. 착수 시점이 오면 적절한 phase에 편입하고 이 목록에서 뺀다.

- [x] **문서 직접 수정/조회 (PDF 포함)** — 이미 구현됨. 위 "Correction apply" 항목 참고(DOCX/HWPX/PDF/TXT/MD 지원, 교정 적용 흐름).
- [x] **PPT 파일 지원 (수정/조회)** — 2026-08-19 구현. `app/services/parsing/pptx_parser.py`(`PptxParser`, registry 등록) — 슬라이드→도형→문단 순회, title placeholder는 heading(level 1), 나머지 텍스트 프레임(부제/본문 placeholder·순수 텍스트박스 구분 없이)은 paragraph, 표는 table. 텍스트 프레임이 없는 도형(이미지·차트·OLE 등)은 섹션화 대상에서 제외. 그룹 도형은 `_iter_shapes`가 재귀적으로 풀어 안의 텍스트박스/표까지 잡는다(중첩 그룹도 지원). `correction_service._apply_pptx`가 기존 `_replace_span_in_nodes`를 그대로 재사용(python-pptx run도 `.text` getter/setter라 호환). `python-pptx==1.0.2` 추가.
  - **버그 수정 (2026-08-19)**: `_is_title_shape`가 placeholder가 아닌 도형(텍스트박스 등)에서 `shape.placeholder_format`에 바로 접근해 `ValueError`를 던졌다 — python-pptx는 non-placeholder에서 이 프로퍼티 접근 자체가 예외를 던지지 `None`을 주지 않는다. 슬라이드에 placeholder 아닌 도형이 하나만 있어도 파싱 전체가 죽어(백그라운드 파싱 실패 → 업로드는 202로 성공해도 상태가 FAILED로 표시) 실제 배포 후 바로 발견됨. `shape.is_placeholder`로 먼저 걸러내도록 수정.
- [x] **표 셀 apply 지원 (DOCX/HWPX/PPTX)** — 2026-08-19 구현. 표 섹션도 이제 교정 반영이 된다. `correction_service._apply_docx_table_cell`/`_apply_hwpx_table_cell`/`_apply_pptx_table_cell`가 표의 셀을 순회하며 원문을 담은 셀 하나만 찾아 그 셀의 run/텍스트노드에만 `_replace_span_in_nodes`를 적용 — 셀 경계를 넘어 span을 찾지 않으므로 다른 셀 데이터가 섞이지 않는다. HWPX는 `hwpx_parser.iter_table_cells`(신규, `<hp:tc>` 순회)를 추가해 재사용. 표 전체 텍스트가 분석 시점과 달라졌으면(drift) 그 표의 findings는 통째로 skip. PDF는 표 구조 자체를 파싱하지 않아(PyMuPDF 네이티브 텍스트만) 해당 없음.
- [x] **XLSX 파일 지원 (조회만)** — 2026-08-19 구현. `app/services/parsing/xlsx_parser.py`(`XlsxParser`, registry 등록) — `openpyxl.load_workbook(data_only=True, read_only=True)`로 워크북을 열어 시트별로 시트명은 heading(level 1), 시트 데이터는 한 시트당 table 섹션 하나(빈 행은 skip)로 변환. `data_only=True`라 수식이 아니라 마지막 저장 시점의 계산값을 읽는다(엑셀에서 한 번도 열어본 적 없는 파일은 캐시된 값이 없어 수식 문자열이 그대로 나올 수 있음 — openpyxl 자체 한계). `openpyxl==3.1.5` 추가 — **새 pip 의존성이라 `--reload` 핫리로드로는 안 잡히고 `docker compose up -d --build backend` rebuild가 필요했다.** correction_service에 `_apply_xlsx_*`가 아직 없어 **교정 적용(표 셀 재작성)은 미지원** — 조회/프루프리드까지만 된다.
- [ ] **알림 기능**
- [ ] **RAG 챗봇 + 퀴즈 기능** — 위 MVP 2 확장 항목과 동일 (여기 중복 기재는 생략).
- [ ] **OneDrive 동기화** (storage 아님, ingest source) — 설계 메모는 [[onedrive-ingest-source-idea]] / DESIGN.md §26. 아직 미착수.
- [ ] **문서 정리 / Notion 연동 / 즐겨찾기 / 메모 기능**
- [ ] **MCP 서버** — Document Core(문서 검색·섹션·교정 결과 등)를 이 백엔드 밖의 다른 서비스/에이전트가 표준 프로토콜로 재사용하도록 tool로 노출. **내부적으로 우리 자신의 RAG/챗봇이 우리 DB를 쓰는 것 자체는 MCP가 필요 없다** (그냥 function calling으로 충분) — MCP는 외부 팀의 에이전트(예: 별도 AI 서비스)가 커스텀 연동 코드 없이 붙어야 하는 실제 수요가 생겼을 때 그 tool 노출 계층으로 추가한다.
