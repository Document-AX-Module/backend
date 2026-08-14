# AX Document Platform 상세 설계서

> 원본 설계 문서를 정리한 버전(인코딩 정상화). 이 문서는 프로젝트의 **방향/설계 기준**이며, 실제로 구현된 현재 상태는 저장소 루트의 `CLAUDE.md`를 따른다. 설계 문서와 현재 스택이 다를 경우(예: DB 이름) CLAUDE.md의 값이 실제이다.

---

## 1. 프로젝트 개요

**AX Document Platform**은 프로젝트별 업무 문서를 통합 관리하고, GPT와 RAG를 활용하여 다음 기능을 제공하는 AI 문서 업무지원 플랫폼이다.

- 프로젝트별 문서 관리
- 문서 오탈자·맞춤법·표현 검수
- 문서 자동 분류
- 문서 버전 비교
- 규정 준수 여부 검증
- 프로젝트 문서 기반 RAG 질의응답
- 관련 문서 추천

핵심 구조는 단순 RAG 챗봇이 아니라 **Document Core**를 중심으로 한다.

```
Document Core (File · Version · Section · Chunk · Metadata · Local Embedding)
        │
        ▼
  PostgreSQL + pgvector
        │
   ┌────┼───────┐
   ▼    ▼       ▼
  RAG  추천   규정검증
 (문서검색 · 문서분류 · Document Diff)
```

> **문서를 한 번 Document Core에 등록하면 모든 AI 기능이 동일한 문서 데이터를 재사용하는 구조**로 설계한다.

---

## 2. 핵심 기술 방향

### LLM — OpenAI GPT API
사용 영역: 오탈자 검증, 문장 수정, 문서 요약, 문서 분류, 문서 비교 설명, 규정 판단, RAG 최종 답변 생성.

### Embedding — Local Embedding Model
Embedding은 OpenAI API를 사용하지 **않고** 로컬 임베딩 모델로 구축한다. 초기 단계에서 2~3개 후보 모델을 선정해 실제 프로젝트 문서·질의를 기준으로 벤치마크한 뒤, 선정된 하나를 기본 Embedding Model로 사용한다.

```
후보 A/B/C  →  동일 Benchmark Dataset  →  (검색정확도·응답속도·CPU/GPU·RAM/VRAM·처리량)  →  최종 Model 선정
```

---

## 3. 전체 기술 스택

| Layer | Tech |
|---|---|
| Frontend | Next.js, TypeScript |
| Backend | Python, FastAPI |
| Database | PostgreSQL + pgvector |
| DB GUI | DBeaver Community |
| Container | Docker / Docker Compose |
| LLM | OpenAI GPT API |
| Embedding | Local Embedding Model |

**Frontend 역할:** 프로젝트/문서 관리, 문서 업로드, AI 검수, 문서 Viewer, RAG Chat, Document Diff, 규정 검증, 관련 문서 추천, AI 처리 상태 표시.

**Backend 역할:** REST API, 프로젝트/폴더/문서 CRUD, 파일 업로드, Parser 실행, Chunk 생성, Local Embedding 실행, Vector Search, RAG Pipeline, GPT API 연동, AI Module 관리.

---

## 4. Database

일반 데이터와 Vector 데이터를 하나의 PostgreSQL에서 관리한다.

```
PostgreSQL
├─ Project / Folder / Document / Version / Section / Chunk
├─ AI Result / RAG History
└─ pgvector → Embedding Vector
```

개발자는 **DBeaver Community**를 PostgreSQL의 GUI 클라이언트로 사용한다. 중요: DBeaver는 클라이언트일 뿐 DB가 DBeaver 안에 존재하는 것이 아니다.

- Docker = PostgreSQL 서버를 실행
- PostgreSQL = 실제 Database
- pgvector = PostgreSQL Vector Extension
- Docker Volume = DB 데이터 보존
- DBeaver = PostgreSQL을 GUI로 관리하는 Client

---

## 5. Storage

초기에는 별도 Cloud Storage 없이 **Local File Storage + Docker Volume**로 구성한다. DB에는 실제 파일이 아니라 Metadata(파일 정보)를 저장한다.

```
storage/documents/project-001/document-001/v1|v2 ...
```

향후 교체 가능하도록 `StorageProvider` 인터페이스로 추상화한다: `LocalStorageProvider` / `MinIOStorageProvider` / `S3StorageProvider`.

---

## 6. 인프라

**초기 개발(Developer PC):** Next.js(`npm run dev`) + FastAPI(`uvicorn`) + DBeaver + Docker(PostgreSQL + pgvector).

**최종 배포(Local Desktop Server):** Docker Compose로 Frontend · Backend · PostgreSQL+pgvector 구동, Volume은 `postgres_data` / `document_storage` / `embedding_models`.

**개발자 PC 필수:** Git, Docker Desktop/Engine, DBeaver Community, Python, Node.js, IDE(VS Code / PyCharm / IntelliJ 중 택).

---

## 7. Backend Python Library

```
FastAPI, Uvicorn
SQLAlchemy, Alembic
psycopg
Pydantic
python-multipart
```
AI/Embedding: `openai`, `torch`, `transformers`, `sentence-transformers`, `accelerate`, (필요 시) `FlagEmbedding`.

---

## 8. Local Embedding 후보

초기에 2~3개 모델을 직접 테스트한 뒤 선정한다. 3개 후보 모두 **1024 Dimension**으로 맞춰 동일 조건에서 비교한다 → pgvector는 `vector(1024)`.

| 후보 | 모델 | 특징 |
|---|---|---|
| A | `BAAI/bge-m3` | Dense/Sparse/Multi-vector, 다국어(100+), 1024 dim, 최대 8192 token. 초기 테스트는 공정 비교 위해 **Dense만** 사용 |
| B | `nlpai-lab/KURE-v1` | BGE-M3 기반 한국어 fine-tuning, 1024 dim / 8192 seq. 사내 문서가 한국어 위주라 유력 후보 |
| C | `Qwen/Qwen3-Embedding-0.6B` | 0.6B, 100+ 언어, context 32K, 출력 차원 32~1024 조절 가능. 비교 위해 **1024 고정** |

---

## 9. Embedding 벤치마크

공개 벤치마크 점수만 보지 않고 **실제 프로젝트 문서**로 평가한다.

**Benchmark Dataset:** 50~100개 질문을 다양한 유형으로 구성.
- 정확한 Keyword 질문: "개인정보 보안 기간은 몇 년인가?"
- 의미 기반 질문: "사업 종료 후 사용자 데이터를 어떻게 처리해야 해?"
- 회의 내용 검색: "사업파라 API 관련해서 지난 회의에서 나온 내용이 뭐야?"
- 규정 검색: "개인정보 파기와 관련된 규정 찾아와."
- 용어/표현 차이: "PII 삭제 절차가 어떻게 돼?"
- 여러 문서 연결: "개인정보 파기 관련 내규와 필요로 내용을 같이 찾아와."

**Ground Truth:** 각 질문마다 사람이 정답 Chunk를 지정(`document_id` / `chunk_id`). 모델이 해당 Chunk를 얼마나 잘 찾는지 평가.

**평가 기준**
- 검색 품질: Recall@1/3/5, MRR, NDCG (실제 RAG에선 Top 3/5에 정답 Chunk가 들어오는지가 중요)
- 성능: Embedding/Query Latency, Documents·Chunks/sec, Model Loading Time
- 리소스: CPU/RAM/GPU/VRAM/Model Size

**모델 선정 가중치(예)**: Retrieval Accuracy 60% · Embedding Performance 15% · Query Latency 10% · RAM/VRAM 10% · 운영 편의성 5%. 가장 빠른 모델이 아니라 **검색 정확도**가 가장 중요.

**공정성 원칙:** 모델 비교 시 Document/Parser/Section/Chunk/Query/Similarity/Top-K를 동일하게 유지하고 **Embedding Model만** 바꾼다.

---

## 10. Embedding DB 구조 (모델 비교를 위한 분리)

Chunk 테이블에 embedding을 바로 넣지 않고 **별도 테이블로 분리**한다. 여러 모델의 결과를 동시에 비교해야 하기 때문.

**embedding_models** — 모델 정보 관리
```
id, model_key, model_name, model_version, dimension, max_tokens,
model_path, status, is_active, config(JSONB), created_at
```
예: `1, bge-m3, BAAI/bge-m3, 1024, 8192, /models/bge-m3, TEST`

**chunk_embeddings** — 실제 Chunk Embedding
```
id, chunk_id, embedding_model_id, embedding vector(1024), created_at
UNIQUE(chunk_id, embedding_model_id)
```
→ 하나의 Chunk에 BGE-M3 / KURE-v1 / Qwen3 벡터가 각각 존재할 수 있어 모델 비교가 쉽다.

**모델 선정 이후**: 최종 모델(예: KURE-v1)을 `is_active=true`로 설정하고, RAG 검색은 항상 `WHERE embedding_model_id = ACTIVE_MODEL`로 조회. 기존 모델 embedding은 즉시 삭제하지 않아 Rollback 가능.

**모델 교체 흐름:** 새 모델 테스트 → 전체 Chunk 재임베딩 → 검색 품질 검증 → Active Model 변경 → 이관.

---

## 11. Local Embedding Model 저장

서버에 Model Storage를 두고 매번 인터넷에서 다운로드하지 않는다. 초기 Setup/배포 단계에서 준비.
```
models/embeddings/{bge-m3, kure-v1, qwen3-embedding-0.6b}
```

---

## 12. Embedding Service 구조

```
RAGService → EmbeddingService → Active Embedding Provider
```
`EmbeddingProvider` 인터페이스: `load()`, `embed_documents()`, `embed_query()`, `get_dimension()`, `get_model_name()`.
Provider 계층: `LocalEmbeddingProvider` ← `SentenceTransformerProvider` / `BGEProvider` / `QwenEmbeddingProvider`. **AI Module은 특정 모델 이름을 알 필요가 없다.**

---

## 13. Embedding 처리 방식 (비동기)

업로드 HTTP 요청 안에서 모든 Embedding을 생성하지 않는다(문서가 크면 요청이 지나치게 길어짐).

**권장 흐름**
```
Upload → 파일 저장 → DB Document 생성 → 202 Accepted → Background Job
                                                      └ Parsing → Section → Chunk → Embedding → DB 저장
```
초기엔 `FastAPI + Background Job`으로 시작하고, 커지면 `Queue → Embedding Worker`, 이후 `Redis + Celery`로 분리.

```
FastAPI → Queue → (Parser Worker | Embedding Worker → Local Model, GPU/CPU)
```

---

## 14. Document Processing Status

Document Version 상태: `UPLOADED → PARSING → PARSED → CHUNKING → EMBEDDING → READY` (실패 시 `FAILED`).
Frontend 표시 예: "문서 분석 중… / Embedding 생성 중… / 분석 완료".

---

## 15. Docker PostgreSQL (설계 문서 예시)

> 아래는 설계 문서상 예시 명칭이다. **현재 실제 스택은 `ai_platform` / `postgres`를 사용한다(CLAUDE.md 참고).**

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: ax-postgres
    environment:
      POSTGRES_DB: ax_document
      POSTGRES_USER: ax_user
      POSTGRES_PASSWORD: ax_password
    ports: ["5432:5432"]
    volumes: ["postgres_data:/var/lib/postgresql/data"]
    restart: unless-stopped
volumes:
  postgres_data:
```
DBeaver 연결: Host `localhost`, Port `5432`, DB `ax_document`, User `ax_user`. DB 생성 후 `CREATE EXTENSION IF NOT EXISTS vector;`.

---

## 16. 프로젝트 Directory (설계 목표 구조)

```
ax-document-platform/
├─ frontend/            (app, components, features, services, types)
├─ backend/
│  └─ app/
│     ├─ api/ core/ models/ schemas/ repositories/
│     └─ services/{document, parser, embedding, rag, ai, compare, validation}/
│     └─ main.py
├─ models/embeddings/
├─ storage/documents/
├─ benchmark/{datasets, results, scripts}/
├─ docker/
├─ docker-compose.yml
├─ .env
└─ README.md
```

---

## 17. Document Core DB 구조

```
projects → folders → documents → document_versions → document_sections → document_chunks → chunk_embeddings
```

**projects**: `id, name, description, created_by, created_at, updated_at`
**folders**: `id, project_id, parent_id, name, created_at`
**documents** (논리적 문서): `id, project_id, folder_id, name, document_type, status, current_version_id, created_by, created_at, updated_at`
**document_versions** (실제 파일 버전): `id, document_id, version_no, original_file_name, storage_path, mime_type, file_size, checksum, processing_status, parser_version, created_at`
**document_sections** (문서 구조): `id, document_version_id, parent_section_id, title, section_type, page_start, page_end, order_no, content`
**document_chunks** (RAG 최소 검색 단위): `id, document_id, document_version_id, section_id, chunk_index, content, page_start, page_end, metadata(JSONB), created_at` — embedding은 여기 저장하지 않음
**chunk_embeddings**: `id, chunk_id, embedding_model_id, embedding vector(1024), created_at`
**ai_analysis_results**: `id, document_id, document_version_id, analysis_type, result(JSONB), model, prompt_version, created_at` — type: `PROOFREAD / CLASSIFICATION / SUMMARY / COMPARE / VALIDATION / EXTRACTION`

---

## 18. 기능별 설계

**① 프로젝트/문서 관리**: 프로젝트 생성 → Folder 생성 → Document Upload.

**Document Upload**: `Upload → File Validation → Storage 저장 → documents 생성 → document_versions 생성 → Processing Job 등록 → Response`.

**Document Parser**: 파일 종류별 Adapter — `PDFParser / HWPXParser / DOCXParser / XLSXParser / PPTXParser`. 초기엔 PDF·HWPX·DOCX부터. 결과는 **Unified Document**(Metadata + Section{Title, Content, Page, Order} + Table)로 통일 → AI 기능이 파일 포맷을 몰라도 됨.

**Chunking**: 단순 500자 분할이 아니라 제목/문단/조항/Section 구조를 우선 보존.

**Local Embedding Pipeline**: `Chunk → Active Local Embedding Model → Embedding → chunk_embeddings → pgvector`.

**Query Embedding**: `Question → 동일 Active Embedding Model → Query Vector → pgvector Similarity Search`. **문서와 Query는 반드시 동일 모델 사용.**

**② AI 오탈자 검증(첫 AI 모듈, 초기 MVP)**: `Document → GPT → Structured Result → ai_analysis_results → Frontend`. 검사 항목: 오탈자, 맞춤법, 띄어쓰기, 비문, 문법, 중복 표현, 용어 통일, 어색한 표현. 화면은 좌측 Document / 우측 AI Review(기존→제안, [적용]/[무시]).

**③ RAG Chat**: `Question → Local Query Embedding → Project/Folder Filter → pgvector Search + Keyword Search → Top-K → GPT → Answer + Citation`.
- **Hybrid Search**: Vector Search + PostgreSQL Full Text Search 결합(의미 검색 + 정확한 용어 검색).
- 출처는 `document_id, section_id, page_start, page_end` 기준으로 생성.

**④ 문서 자동 분류**: `Document → 대표 Section → GPT → Classification → 사용자 확인 → Metadata 저장`. 결과: 문서 유형/카테고리/태그.

**⑤ 관련 문서 추천**: `현재 Document → 대표 Chunk 또는 Document Vector → Vector Search → Related Documents(유사도 %)`. RAG Retrieval Engine 재사용.

**⑥ Document Diff**: `V1 + V2 → Section Matching → Text Diff → Semantic Diff → GPT Explanation`.

**⑦ 규정 검증**: 특정 Document를 `RULE` 타입으로 지정(예: 개인정보 처리지침, 보안규정). `대상 Document → 관련 Rule 검색 → Local Embedding → pgvector Retrieval → GPT Validation → Validation Result`.

---

## 19. 사용자 시나리오 (요약)

1. 프로젝트 생성: 로그인 → Dashboard → 프로젝트 생성 → 완료
2. 문서 Upload: Folder 선택 → 파일 업로드 → (분석/Chunk/Embedding 진행) → READY
3. AI 검수: 문서 선택 → AI 검수 → GPT 분석 → 오탈자/표현/맞춤법 건수 → 사용자 확인
4. RAG: 질문 → Local Embedding → Vector Search → 관련 Chunk → GPT → 답변 + 출처
5. 문서 비교: V1 vs V2 → 변경사항(추가/삭제/수정/의미 변경) 확인
6. 규정 검증: 대상 문서 → 관련 RULE 검색 → GPT 판단 → 주의사항

**개발자 시나리오 — Embedding Benchmark**: Embedding Lab에서 Dataset·Model 선택 → Benchmark 실행 → (Recall@5 / Latency / RAM 비교) → 최종 모델 선정. 관리자용 Embedding 관리 화면에서 Status(TESTED/ACTIVE), [Benchmark Result]/[Activate]/[Re-Embedding] 제공.

---

## 20. 주요 화면

- **Dashboard**: 최근 프로젝트/문서, 문서 처리 현황, 최근 AI 분석, 최근 RAG 질문
- **Project/Document List/Detail**: 문서 상세는 좌측 Viewer + 우측 AI Panel, 상단 [AI 검수][규정 검증][비교][추천]
- **RAG Search**: 좌측 Search Scope(프로젝트/폴더 필터) + 우측 AI Search(답변 + Sources)

---

## 21. 개발 순서 (Phase)

| Phase | 내용 |
|---|---|
| 0 | 기본 개발환경 (Git, Docker, DBeaver, Python, Node.js) |
| 1 | PostgreSQL + pgvector (Docker Volume, DBeaver 연결, `CREATE EXTENSION vector`) |
| 2 | FastAPI (DB Connection, SQLAlchemy, Alembic Migration, `GET /health`) |
| 3 | Next.js (Layout, Routing, API Client, Project/Document 화면) |
| 4 | Project / Folder CRUD |
| 5 | Document Upload (Local Storage, Metadata, Version) — AI 미포함 |
| 6 | Parser (PDF·HWPX·DOCX → Unified Document) |
| 7 | Document Core (Document→Version→Section→Chunk DB 저장) — **가장 중요** |
| 8 | 첫 AI Module = 오탈자 검증 (초기 MVP) |
| 9 | Local Embedding 환경 (PyTorch/Transformers/Sentence-Transformers, `/models/embeddings`) |
| 10 | Embedding 후보 연동 (BGE-M3/KURE/Qwen, 동일 Interface) |
| 11 | Embedding Benchmark (전체 RAG 만들기 전, Dataset으로 모델별 검색 기록) |
| 12 | Model 선정 (검색 정확도·속도·리소스·운영편의성) |
| 13 | 전체 Chunk Embedding (Final Model) |
| 14 | Vector Search (GPT 없이 검색 품질 먼저 검증, Top 5 vs 정답) |
| 15 | Vector Index (검증 후 HNSW 등 검토) |
| 16 | RAG (Retrieval → Context → GPT → Citation) |
| 17 | 문서 분류 / 추천 (기존 Embedding·Document Core 재사용) |
| 18 | Version / Diff |
| 19 | Regulation Validation (RULE 관리 · Retrieval · Validation · GPT) |

**최종 순서 요약**: Docker PG+pgvector → DBeaver → FastAPI → Next.js → Project/Folder → Upload → Parser → Document Core → GPT 오탈자검증 → Local Embedding 환경 → 후보 연동 → Benchmark Dataset → Benchmark → Model 선정 → 전체 Embedding → Vector Search → RAG → 분류 → 추천 → Version/Diff → 규정 검증.

---

## 22. MVP 단계

- **MVP 1**: Docker PG/pgvector · DBeaver · FastAPI · Next.js · Project · Upload · Parser · Document Core · GPT 오탈자검증
- **MVP 2**: Local Embedding · Benchmark · Final Model · Vector Search · RAG Chat
- **MVP 3**: 자동 분류 · 관련 문서 추천
- **MVP 4**: Version · Document Diff
- **MVP 5**: Rule 관리 · 규정 Validation

---

## 23. 최종 아키텍처

```
User → Next.js → FastAPI
   ├─ Document Service → Parser ─┐
   ├─ GPT Service                │
   └─ RAG Service → Embedding Service → (BGE | KURE | Qwen) → Final Active Model
                                         │
        ┌────────────────────────────────┘
        ▼
  PostgreSQL + pgvector  (Metadata · Chunk · Embedding)  ── DBeaver
```
**이관(Local Desktop Server) Docker Compose**: Frontend(Next.js) · Backend(FastAPI) · PostgreSQL+pgvector · Model Storage(Local Embedding Model) · Volume(DB/Documents/Models). 사용량 증가 시 `Redis + Celery + Embedding Worker` 추가.

---

## 24. 핵심 설계 원칙

1. **Document Core가 중심** — 업로드→Parser→Section→Chunk를 모든 기능이 공유.
2. **Embedding Model에 종속되지 않는다** — `RAGService → EmbeddingService → EmbeddingProvider`로 분리해 BGE→KURE→Qwen→미래 모델로 쉽게 교체.
3. **모델 변경 이력 관리** — model_name/version/dimension/max_tokens/config/created_at 저장.
4. **Embedding과 Chunk 분리** — `Chunk 1 : N ChunkEmbedding`으로 여러 모델 동시 비교·안전한 교체.
5. **Model 선정은 실제 데이터로** — 공개 벤치마크가 아니라 우리 문서/질문/검색 시나리오 기반.
6. **Local Embedding과 GPT 역할 분리** — Local Embedding=검색/Retrieval, GPT=분석/판단/생성. RAG는 `질문 → Local Embedding → pgvector Search → 관련 문서 → GPT → 최종 답변`.

---

## 25. 최종 프로젝트 정의

> **프로젝트별 비정형 문서를 Document Core로 통합 관리하고, 로컬 Embedding Model과 PostgreSQL pgvector 기반 검색, GPT 기반 문서 분석을 결합하여 문서 검색·분류·비교·규정 검증·RAG 검색·관련 문서 추천을 제공하는 확장형 AX Document Platform.**

핵심 키워드: Document Core · FastAPI · Next.js · Docker · PostgreSQL · pgvector · Local Embedding · Embedding Benchmark · GPT · RAG · Modular AI.

---

## 26. (향후 아이디어) 외부 문서 유입 소스 — Teams/OneDrive 연동

> 확정 아님. 수동 Upload(Phase 5) → Parser(Phase 6) 파이프라인이 돈 **이후**에 얹을 커넥터. 지금은 파킹.

사내가 **Microsoft 365(Teams/OneDrive)**를 쓰므로, Teams/OneDrive 공유 폴더를 우리 플랫폼의 **유입 소스(ingest source)**로 연결할 수 있다. 중요한 구분: 클라우드 폴더는 **Storage 가 아니라 소스**다. 정본(source of truth)은 여전히 우리 `StorageProvider`(Local/MinIO/S3) + Document Core 이고, 커넥터는 "외부 파일 → 우리 스토리지로 복사 + ingest 트리거" 역할만 한다.

**방식 A — 동기화 클라이언트 + 로컬 폴더 감시** (구현 간단)
- 서버(로컬 데스크톱 서버)에 OneDrive 동기화 앱 로그인 → 공유 폴더가 로컬 경로로 동기화 → 워커가 폴더를 watch 하다 새 파일 ingest.
- 주의: **Files On-Demand**(플레이스홀더/0바이트) → 해당 폴더를 "항상 이 기기에 유지"로 강제. 동기화 중 부분 파일 안정화 감지 필요. 헤드리스 GUI 앱이라 세션/재로그인 취약. 메타데이터(공유자·버전·권한)는 못 얻음.

**방식 B — Microsoft Graph API** (견고함 우선, 서비스로서 권장)
- OneDrive/SharePoint/Teams 파일 접근(Teams 파일 탭 = SharePoint 문서 라이브러리). OAuth 앱 등록 → `delta` 쿼리로 변경분만 → **webhook(subscription)**으로 변경 푸시.
- 파일 + 메타데이터 + 버전이력 + 권한까지 확보. 헤드리스로 안정적.

**아키텍처 반영**: 임베딩 Provider 추상화와 같은 패턴으로 `DocumentSource`(또는 `Connector`) 인터페이스를 두고 `ManualUpload / OneDriveSource(Graph)` 로 확장. 결정할 것: 공유 계정 1개로 접근할지 vs 사용자별 OAuth 로 권한 매핑할지.
