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
- `role`로 관리자 메뉴(Embedding Lab) 노출 제어. 보호 라우트는 `app/api/deps.py`의 `get_current_user` / `require_role(...)` 의존성 사용.
- **TODO**: `projects.created_by` 등 임시 String → `users` FK 승격, 프로젝트 `members`(멤버십 테이블). SSO(OIDC/SAML)는 IdP 확정 후.
- refresh 토큰은 현재 **stateless JWT**(logout=쿠키 삭제) — 서버측 강제 폐기가 필요하면 refresh jti denylist/테이블 추가.

---

## 2. 대시보드 (`/api/v1/dashboard`)

| Method | Endpoint | 용도 | 상태 |
|---|---|---|---|
| GET | `/api/v1/dashboard/summary` | 스탯 4종 + 주간 처리량 + 문서타입 분포 + 파이프라인 상태 카운트 | ⏳ |
| GET | `/api/v1/activity?limit=` | 최근 활동 피드 | ⏳ |
| GET | `/api/v1/rag/history?limit=` | 최근 RAG 질의(대시보드/검색 공용) | ⏳ |

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
| GET | `/api/v1/projects/{project_id}/documents?folder_id=&status=&type=` | 문서 목록(+author+current_version) | ✅ |
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
- **응답 보강**: 문서 상세/목록은 `author`(created_by id 해석 → id/name/email)와 `current_version`(version_no/processing_status/파일메타)를 포함(목록은 batch 조회로 N+1 방지).
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

> 참고: 기존 `POST /api/v1/spell-check`(✅ stub, 단문 입력용)는 최종적으로 `proofread` 모듈로 흡수 예정.

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
