# AX Document Platform — Backend API 계약 (백엔드 기준)

> 프론트 세션의 API 설계(APIDOC.md)를 **백엔드 관점으로 정리한 계약**. 프론트 타입(`src/lib/*`)은 이 문서와 백엔드 Pydantic 스키마를 손으로 미러링한다.
> 실제 엔드포인트 구현은 이 백엔드 리포(`sidepjt/backend`) 몫이다.

## 공통 규약 (결정 완료)

- **Prefix**: 모든 엔드포인트는 `/api/v1/...` 아래. (health 포함 — `/api/v1/health`)
- **네이밍**: 요청/응답 JSON은 **snake_case** (FastAPI/Pydantic 기본). 경로 파라미터도 `{project_id}`, `{version_id}` 등 snake. 프론트가 camel을 쓰면 프론트 쪽 매핑.
- **인증 헤더**: `Authorization: Bearer <access_jwt>`. refresh 토큰은 httpOnly Secure 쿠키.
- **에러 응답**: `{ "detail": string }` (FastAPI 기본).
- **페이지네이션**: `?page=&limit=` 통일 (목록 엔드포인트).
- **비동기 처리**: 업로드/무거운 AI 작업은 `202 Accepted` + 상태 폴링(`/status`), SSE는 선택 확장.
- **Swagger 가 1차 레퍼런스**: 모든 엔드포인트에 용도·파라미터·오류 응답 설명이 붙어 있다(`summary`/docstring/`Path`/`Query`). 서버를 띄우고 [`/docs`](http://localhost:8000/docs) 를 보는 게 이 표보다 항상 최신이다. 이 문서는 **구현 상태와 설계 의도**를 추적하는 용도.

### 문서 "상태"는 하나뿐 — `processing_status`

문서의 상태는 **현재 버전의 `document_versions.processing_status`** 하나다:
`UPLOADED → PARSING → PARSED → CHUNKING → CHUNKED → EMBEDDING → READY` (실패 시 `FAILED`).
임베딩(MVP2)이 없어 **정상 종료는 `CHUNKED`(progress 75)** 이므로 `READY`/100% 를 기다리면 무한 폴링이 된다.

문서 목록의 `?status=` 필터도 이 값을 본다(`ProcessingStatus` enum 검증 → 잘못된 값은 422).

> **2026-08-16 변경.** 예전엔 `documents.status` 컬럼이 따로 있었다. 생성 시 `"DRAFT"` 로 박힌 뒤 아무도 갱신하지 않는 죽은 컬럼이었는데, 살아 있는 처리 상태와 이름이 겹쳐 `?status=` 필터가 어떤 값을 줘도 빈 배열을 내놓는 버그를 냈다. 필터를 `processing_status` 기준으로 고치고 컬럼은 **드롭**했다(`72b852f975d6`). `DocumentRead` 응답에서도 `status` 키가 사라졌으니 프론트는 `current_version.processing_status` 를 쓸 것. 발행/보관 생명주기가 실제로 필요해지면 그때 `lifecycle_status` 같은 별도 이름으로 다시 넣는다.

## 구현 상태 범례

- ✅ 구현됨 · 🟡 부분 구현(보강 필요) · ⏳ 예정(Phase 표기)

---

## 1. 인증 (Auth) — Google OAuth + JWT (지금 도입, 최소 실인증)

결정: **1차는 Google OAuth(authorization code) + 자체 JWT 발급**. 사내 SSO(OIDC/SAML)는 후순위(⏳). `users` 테이블 추가 완료.

provider 추상화(`app/services/auth_providers.py`): `mock`(dev 전용, 크리덴셜 없이 플로우 검증) + `google`(실제). 활성 목록은 `AUTH_PROVIDERS` 환경변수.

| Method | Endpoint | 용도 | 상태 |
|---|---|---|---|
| GET | `/api/v1/auth/providers` | 활성 로그인 수단 목록(`AUTH_PROVIDERS`) | ✅ |
| GET | `/api/v1/auth/login/{provider}` | OAuth 시작(IdP 리다이렉트) + state 쿠키 | ✅ |
| GET | `/api/v1/auth/callback/{provider}` | 콜백 → refresh 쿠키(httpOnly) + 프론트로 리다이렉트 | ✅ |
| POST | `/api/v1/auth/token/refresh` | refresh 쿠키로 access JWT 재발급 | ✅ |
| POST | `/api/v1/auth/logout` | refresh 쿠키 삭제 | ✅ |
| GET | `/api/v1/auth/me` | 현재 사용자(`id, email, name, role`), Bearer 필요 | ✅ |

- 로그인 후 프론트는 콜백 착지 시 `POST /auth/token/refresh`(쿠키 사용)로 access 토큰을 메모리에 확보 → 이후 `Authorization: Bearer`.
- 보호 라우트는 `app/api/deps.py`의 `get_current_user` 의존성 사용. `role` 기반 게이팅(관리자 전용 Embedding Lab 등)은 **아직 없다** — 첫 관리자 전용 라우트가 생길 때 의존성을 추가한다.
- **TODO**: `projects.created_by` 등 임시 String → `users` FK 승격, 프로젝트 `members`(멤버십 테이블). SSO(OIDC/SAML)는 IdP 확정 후.
- refresh 토큰은 현재 **stateless JWT**(logout=쿠키 삭제) — 서버측 강제 폐기가 필요하면 refresh jti denylist/테이블 추가.

---

## 2. 대시보드 (`/api/v1/dashboard`)

| Method | Endpoint | 용도 | 상태 |
|---|---|---|---|
| GET | `/api/v1/dashboard/summary?project_id=` | 스탯 4종 + 주간 처리량 + 문서타입 분포 + 파이프라인 상태 카운트 | 🟡 구현됨, **`ragToday`는 RAG 미구현이라 항상 0** |
| GET | `/api/v1/activity?limit=&project_id=&action=` | 최근 활동 피드 — `activity_logs` 조회 | ✅ |
| GET | `/api/v1/rag/history?limit=` | 최근 RAG 질의(대시보드/검색 공용) | ⏳ |

- **응답만 camelCase.** 프론트가 먼저 확정해 둔 계약(`frontend/src/lib/types.ts` 의 `DashboardSummary`/`ActivityItem`)에 맞추기 위해 이 섹션의 스키마만 `alias_generator=to_camel` 로 직렬화한다(`ragToday`, `weeklyProcessing`, `documentTypes`). 나머지 API 는 그대로 snake_case.
- `project_id` 는 선택 — 없으면 전체 집계, 주면 해당 프로젝트로 스코프. **없는 id 면 빈 결과가 아니라 404** 다. `/dashboard/summary`·`/activity`·`/documents/recent` 세 엔드포인트가 동일하게 동작하므로, 프로젝트 스코프 대시보드에서 `Promise.all` 로 묶어도 일부만 404 가 나는 일은 없다(2026-08-16 통일 — 그전엔 summary 만 200 빈 집계였다). 조용히 빈 결과를 주면 프론트가 오타 난 id 를 "데이터 없음"으로 오인하기 때문.
- `pipeline` 은 카운트가 0 이어도 8개 상태(`UPLOADED`~`FAILED`)를 항상 고정 순서로 내려준다. 집계 기준은 **현재 버전의 `document_versions.processing_status`**. `stats.processing` 은 `PARSING`/`CHUNKING`/`EMBEDDING` 만 센다.
- `weeklyProcessing` 은 **오늘 포함 최근 7일**의 업로드 건수를 과거 → 오늘 순으로 담은 길이 7 배열(요일 고정이 아니므로 라벨은 프론트가 계산). 날짜 경계는 UTC.
- `activity.at` 은 ISO8601 문자열 — "10분 전" 같은 상대시간 포맷은 프론트 담당.
- **활동 피드에서 링크 걸기** — 응답 키는 `targetType` / `targetId` / `targetAlive` (스키마 필드명은 snake 지만 이 섹션은 camelCase 직렬화). 반드시 **세 조건을 모두** 본다:

  ```ts
  targetType === "document" && targetId != null && targetAlive
  ```

  | 조건 | 빠뜨리면 |
  |---|---|
  | `targetType` 확인 | `target_id` 는 **테이블별 시퀀스**라 project id 4 로 `/documents/4` 를 열면 404 도 안 나고 **엉뚱한 문서가 정상적으로 열린다**. 실제로 project 5 와 document 5 가 동시에 존재한다 |
  | `targetAlive` 확인 | 로그는 FK 가 없어 대상보다 오래 산다. "삭제 액션만 제외" 로는 부족하다 — `document.upload`/`analysis.complete` 로그도 그 문서가 **나중에** 지워졌으면 404 다 |

  `targetAlive` 는 응답 생성 시점에 대상 행이 실제로 있는지 확인한 값이다(타입별 `IN` 조회 1회씩, 최대 3쿼리 — limit 이 커져도 쿼리 수는 안 늘어난다). 대상이 없는 활동(로그인)은 `false`.

  **단, 스냅샷이다.** 사용자가 몇 초 뒤 클릭하는 사이 지워질 수 있으니 클라이언트는 여전히 404 를 처리해야 한다.

- `target_id` 를 살아 있지 않을 때 `null` 로 죽이지 않고 `targetAlive` 를 따로 둔 이유: 감사 로그에서 "삭제된 문서 8" 과 "애초에 대상이 없음" 은 다른 사실이고, 전자를 지우면 로그의 값이 떨어진다. 프론트 규칙 길이는 어느 쪽이든 같다.
- 로그에는 `project_id` 스코프도 있으니 폴더/프로젝트 활동의 프로젝트 링크가 필요하면 노출 가능.
- "최근 문서" 카드는 `GET /documents/recent` 를 쓴다 — 아래 4절 참고. 요약에 얹지 않고 별도 엔드포인트로 둔 이유는 `/activity` 와 같은 성격(집계가 아닌 목록)이고 limit 을 따로 주기 때문이다.
- 구현: `api/v1/dashboard.py → services/dashboard_service.py → repositories/{dashboard,activity}_repository.py`, 스키마는 `schemas/dashboard.py`.

### 활동 로그 (`activity_logs`)

2026-08-16 도입. **소급 기록이 불가능하므로 그 이전 활동은 없다.**

기록하는 액션(12종) — DB 에는 이 **코드**를 저장하고, 표시 문구·배지 `kind` 는 읽을 때 `activity_service.describe()` 가 붙인다(문구를 바꿔도 과거 로그가 안 깨지게):

| 코드 | 문구 | kind |
|---|---|---|
| `auth.login` | 로그인 | 로그인 |
| `project.create` / `.update` / `.delete` | 프로젝트 생성/수정/삭제 | 프로젝트 · 프로젝트 · 삭제 |
| `folder.create` / `.update` / `.delete` | 폴더 생성/수정·이동/삭제 | 폴더 · 폴더 · 삭제 |
| `document.upload` / `.delete` | 문서 업로드/삭제 | 업로드 · 삭제 |
| `analysis.start` / `.complete` / `.fail` | (모듈명) 시작/완료/실패 | 오타 · 규정 · 분류 · 추천 |

**설계 원칙:**

- **FK 를 하나도 걸지 않는다.** 대상이나 사용자가 지워질 때 CASCADE 로 로그까지 사라지면 정작 가장 알고 싶은 "누가 그걸 지웠는지"를 잃는다. 대신 그 시점 이름을 `actor_label` / `target_label` 에 복사해 두므로, 삭제된 프로젝트의 활동도 이름과 함께 그대로 읽힌다.
- **append-only** — 수정·삭제하지 않는다.
- **기록은 best-effort** — 로그 저장이 실패해도 본 작업(업로드·삭제 등)을 깨뜨리지 않고 경고만 남긴다. 반대로 본 작업이 커밋된 **뒤에** 기록하므로 롤백된 작업의 로그가 남지도 않는다.
- 요청 핸들러는 `Depends(get_activity_service)` 로 로그인 사용자가 행위자로 자동 세팅된다. 백그라운드 잡(교정 완료/실패)과 OAuth 콜백은 요청 컨텍스트 밖이라 `activity_service_for(db, actor=...)` 로 직접 만든다.
- 인덱스는 `(created_at)` 과 `(project_id, created_at)`. 보존 기간 정책은 아직 없다 — 무한히 쌓인다.

---

## 3. 프로젝트 / 폴더 (`/api/v1/projects`)

| Method | Endpoint | 용도 | 상태 |
|---|---|---|---|
| GET | `/api/v1/projects` | 목록(+폴더수/문서수/READY수/멤버/updated_at) | 🟡 기본 CRUD는 됨, **집계·members 필드 미구현** |
| POST | `/api/v1/projects` | 생성 | ✅ |
| GET | `/api/v1/projects/{project_id}` | 상세 | ✅ |
| PATCH | `/api/v1/projects/{project_id}` | 부분 수정(`exclude_unset`) | ✅ |
| DELETE | `/api/v1/projects/{project_id}` | 삭제(204) | ✅ |
| GET | `/api/v1/projects/{project_id}/members` | 멤버 | ⏳ (멤버십 테이블 미구현) |
| GET | `/api/v1/projects/{project_id}/folders` | 폴더 트리(project별 목록) | ✅ |
| POST | `/api/v1/projects/{project_id}/folders` | 폴더 생성(parent_id로 트리) | ✅ |
| PATCH | `/api/v1/folders/{folder_id}` | 폴더 수정/이동(순환 방지) | ✅ |
| DELETE | `/api/v1/folders/{folder_id}` | 폴더 삭제(204) | ✅ |

> - **인증**: Project·Folder의 모든 라우트는 `get_current_user`(Bearer) 필수. 무토큰 시 차단.
> - `created_by`는 클라이언트가 지정하지 않고 **서버가 로그인 사용자 id로 채운다**(임시 String → 향후 users FK).
> - 현재 `ProjectRead` = `id, name, description, created_by, created_at, updated_at`. 목록 뷰의 집계 필드(폴더수/문서수/READY수/멤버)는 별도 `ProjectListItem` 스키마로 추가 예정.
> - Folder 수정 시 부모를 자기 자손 밑으로 옮기면 `400`(순환 방지), 다른 프로젝트 부모도 `400`.

---

## 4. 문서 (`/api/v1/...`)

업로드는 `202 Accepted` + 백그라운드 처리(파싱→청킹), 상태 폴링. 전 라우트 인증 필수. (Phase 5·6·7 완료)

| Method | Endpoint | 용도 | 상태 |
|---|---|---|---|
| GET | `/api/v1/documents/recent?limit=&project_id=` | 대시보드용 **프로젝트 교차** 최근 문서(+project_name+processing_status) | ✅ |
| GET | `/api/v1/projects/{project_id}/documents?folder_id=&status=&type=` | 문서 목록(+author+current_version). `status` 는 **현재 버전의 처리 상태**(enum 검증 → 잘못된 값은 422) | ✅ |
| POST | `/api/v1/projects/{project_id}/documents` (multipart) | 업로드 → 202 `{document, version}` | ✅ |
| GET | `/api/v1/documents/{doc_id}` | 상세(+author+current_version) | ✅ |
| PATCH | `/api/v1/documents/{doc_id}` | 이름/폴더/타입 변경(예: RULE 지정) | ⏳ |
| DELETE | `/api/v1/documents/{doc_id}` | 삭제(파일 정리 포함, 204) | ✅ |
| GET | `/api/v1/documents/{doc_id}/download` | 원본 다운로드 | ✅ |
| GET | `/api/v1/documents/{doc_id}/versions` | 버전 목록 | ✅ |
| GET | `/api/v1/versions/{version_id}` | 버전 상세 | ✅ |
| GET | `/api/v1/versions/{version_id}/status` | 처리상태 폴링 `{status, progress}` | ✅ |
| GET | `/api/v1/versions/{version_id}/sections` | 파싱된 Section(계층, bbox meta) | ✅ |
| GET | `/api/v1/versions/{version_id}/chunks` | 청크(heading_path·bbox meta) | ✅ |
| GET | `/api/v1/versions/{version_id}/events` | 진행 SSE(선택) | ⏳ |

- 상태 값: `UPLOADED · PARSING · PARSED · CHUNKING · CHUNKED · EMBEDDING · READY · FAILED` (모델 `document_versions.processing_status`). `status→progress`(%) 매핑 제공. **현재 파이프라인은 `CHUNKED`(75%)에서 종착** — `EMBEDDING→READY`는 임베딩(Phase 9)에서 이어짐.
- **응답 보강**: 문서 상세/목록은 `author`(created_by id 해석 → id/name/email)와 `current_version`(version_no/processing_status/파일메타)를 포함(목록은 batch 조회로 N+1 방지). **`DocumentRead` 에 `status` 키는 없다** — 처리 상태는 `current_version.processing_status` 를 볼 것(위 "문서 상태는 하나뿐" 참고).
- **`/documents/recent`** (대시보드 전용): `/projects/{id}/documents` 와 달리 **프로젝트를 가로질러** 최근 등록순으로 준다. 카드에서 추가 요청 없이 프로젝트 경로와 상태 배지를 그릴 수 있게 `project_name` 과 현재 버전의 `processing_status` 를 펼쳐 담은 납작한 뷰(`RecentDocumentRead`)이고, `author`/버전 상세는 없다. `limit` 1~50(기본 5), `project_id` 는 선택. 라우터에서 이 경로는 반드시 `/documents/{document_id}` **앞에** 등록해야 한다(뒤면 `recent` 가 id 로 매칭돼 422).
- **업로드**: multipart `file`(필수) + `folder_id`/`name`/`description`(선택). 확장자 화이트리스트(pdf/hwpx/hwp/docx/xlsx/pptx/txt/md, `.env`로 조정) + 최대 50MB + 빈 파일 거부, 위반 시 `400`. 새 문서=version 1, `current_version_id` 세팅. sha256/file_size 저장. `description`은 문서(Document) 단위 컬럼.
- **저장**: `StorageProvider`(Local) — `storage/documents/project-{pid}/document-{did}/v{n}/{uuid}.{ext}`, DB엔 상대 `storage_path`만. 컨테이너는 `/data/storage` 볼륨.
- **파이프라인(BackgroundTasks)**: 업로드 → `run_parse_task` → Parser(포맷별 어댑터 + PDF 스캔본 OCR) → `document_sections`(계층/bbox) → Chunking(구조 인지, heading breadcrumb, 문장분할+overlap) → `document_chunks`. 미지원 확장자(hwp/xlsx/pptx)는 `FAILED`.
- **Section 표현 규칙**: `heading` 섹션은 `title`만 채우고 `content`는 비운다(제목 중복 표시 방지). 본문/표는 `title` 없이 `content`만. `level`(heading 깊이 1~9, 본문/표는 100)을 저장해 `SectionRead.level`로 노출 → 프론트가 `level`+`parent_section_id`로 접이식 트리 렌더. heading 은 chunk 로 만들지 않고 breadcrumb(`chunk_metadata.heading_path`)로만 쓰인다.
- **아직**: `PATCH /documents/{id}`, 새 버전 업로드(v2+), SSE, 임베딩(READY).

---

## 5. AI 모듈 (결과는 `ai_analysis_results`에 적재)

| Method | Endpoint | 결과 | 상태 |
|---|---|---|---|
| POST | `/api/v1/documents/{doc_id}/proofread` | 교정 시작 → `202` + RUNNING 레코드(백그라운드 처리) | ✅ (Phase 8, MVP1 마지막) |
| GET | `/api/v1/documents/{doc_id}/analyses?type=` | AI 분석 이력(메타 목록) | ✅ |
| GET | `/api/v1/documents/{doc_id}/analyses/{analysis_id}` | 분석 결과 상세(status 폴링 + findings) | ✅ |
| POST | `/api/v1/documents/{doc_id}/classify` | 문서타입·카테고리·태그 | ⏳ (MVP3) |
| POST | `/api/v1/documents/{doc_id}/validate` | 규정 검증(RULE 검색 → GPT) | ⏳ (MVP5) |
| POST | `/api/v1/documents/{doc_id}/related` | 관련 문서 추천(vector search) | ⏳ (MVP3) |
| POST | `/api/v1/documents/compare` | `{base_version_id, target_version_id}` → diff + GPT 설명 | ⏳ (MVP4) |

- **proofread 흐름(202 + 폴링)**: `POST /proofread` 는 검증 후 `RUNNING` 레코드를 만들고 **즉시 `202`** 반환 → BackgroundTasks 가 배치 교정 수행 → 프론트는 `GET .../analyses/{analysis_id}` 로 `status`(RUNNING→COMPLETED/FAILED)를 폴링. 파싱 전(`UPLOADED/PARSING`)이면 `409`, 문서 없음 `404`.
- **배치 호출**: 섹션을 글자수/개수 상한(`proofread_batch_max_chars`·`_max_sections`)으로 묶어 **한 프롬프트에 여러 섹션**을 보낸다(`[번호]` 마커 → 결과 index 로 되매핑). 섹션당 1회 순차 호출 대비 호출 수·시간·비용 대폭 절감(11섹션 기준 순차 17s → 배치 1회 ~7.5s). 각 finding 은 `original→suggestion`·`reason`·`category`(spelling/spacing/grammar/expression) + `section_id`·`page_start/end`.
- **LLM provider 추상화**(`app/services/llm/`): `MockLLMProvider`(규칙 기반, 키 불필요) + `OpenAILLMProvider`(httpx Chat Completions, `proofread`/`proofread_batch`). `LLM_PROVIDER=auto`면 `OPENAI_API_KEY` 유무로 자동 선택. 프롬프트는 `app/services/llm/prompts.py`에 분리. 최신 모델(gpt-5.x)은 `temperature` 커스텀 불가라 payload 에서 생략.
- **결과 적재**: `ai_analysis_results`(document/version FK, `analysis_type`, `status`=RUNNING/COMPLETED/FAILED, `provider`, `result` JSONB, `error`) — proofread 외 classify·validate·related 결과도 이 테이블을 공용으로 쓴다.

> 참고: 초기의 `POST /api/v1/spell-check`(입력을 그대로 돌려주던 스텁)는 **삭제됐다**(2026-08-16). 소비자가 없었고 실제 교정은 `proofread` 가 담당한다.

---

## 6. RAG 검색 (`/api/v1/rag`) — MVP2

| Method | Endpoint | 결과 | 상태 |
|---|---|---|---|
| POST | `/api/v1/rag/query` | `{question, scope:{project_ids[], folder_ids[]}}` → `{answer, citations[]}` | ⏳ |
| POST | `/api/v1/rag/query/stream` | 답변 토큰 SSE(선택) | ⏳ |
| GET | `/api/v1/rag/history?limit=` | 최근 질의 | ⏳ |

- citation: `document_id, section_id, page_start, page_end, score`.

---

## 7. Embedding Lab (관리자) — MVP2

| Method | Endpoint | 상태 |
|---|---|---|
| GET | `/api/v1/embedding/models` | ⏳ |
| POST | `/api/v1/embedding/models/{id}/activate` | ⏳ |
| POST | `/api/v1/embedding/models/{id}/reembed` | ⏳ |
| GET | `/api/v1/embedding/benchmarks` | ⏳ |
| POST | `/api/v1/embedding/benchmarks/run` | ⏳ |
| GET | `/api/v1/embedding/benchmarks/{run_id}` | ⏳ |

---

## 8. 공통 / 기타

| Method | Endpoint | 용도 | 상태 |
|---|---|---|---|
| GET | `/api/v1/health` | 헬스체크(DB 핑 포함) | ✅ |
| GET | `/api/v1/search?q=` | Topbar 전역 빠른검색(문서·프로젝트 키워드) | ⏳ |

- 백엔드 OpenAPI: `/openapi.json` (+ Swagger `/docs`) 자동 제공 → 프론트 타입 대조에 활용.
