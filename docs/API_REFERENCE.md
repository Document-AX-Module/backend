# AX Document Platform — API 레퍼런스

> 이 문서는 **각 엔드포인트의 역할 + Request + Response**를 상세히 정리한 참고 문서다.
> "무엇이 구현됐고 왜 그렇게 만들었는지"의 설계 의도·변경 이력은 [api-spec.md](api-spec.md)를,
> 전체 아키텍처·DB 설계는 [DESIGN.md](DESIGN.md)를 본다.
>
> **✅ 구현됨** 섹션의 스키마는 실제 `/openapi.json`(2026-08-19 생성 서버 기준)에서 그대로 뽑아 정확성을 맞췄다 — 코드가 바뀌면 이 문서도 갱신 필요.
> **⏳ 예정** 섹션은 아직 스키마가 확정되지 않은 설계 초안이다.

## 공통 규약

- 모든 엔드포인트는 `/api/v1` 아래. 요청/응답 JSON은 **snake_case**(대시보드 섹션만 예외로 camelCase).
- 인증이 필요한 엔드포인트는 헤더에 `Authorization: Bearer <access_token>`. 토큰 없음/만료/무효 시 공통으로 `401`.
- 존재하지 않는 리소스는 `404`, 요청 바디/쿼리 검증 실패는 FastAPI 기본 `422`(`HTTPValidationError`, 아래 공통 스키마 참고) — 두 응답은 사실상 모든 엔드포인트에 붙으므로 이후 각 엔드포인트 표에서는 도메인 특화 에러만 적는다.
- 에러 바디는 기본적으로 `{"detail": "..."}` (문자열). `422`만 `{"detail": [ValidationError, ...]}` 구조.

### 공통 스키마

#### `HTTPValidationError`
| field | type | 설명 |
|---|---|---|
| detail | `ValidationError[]` | |

#### `ValidationError`
| field | type | 설명 |
|---|---|---|
| loc | any[] | 어느 필드가 실패했는지 경로 |
| msg | string | 에러 메시지 |
| type | string | 에러 종류 |

---

## 1. Health

### `GET /api/v1/health` — 헬스체크
**역할**: 서버·DB 생존 확인. 인증 불필요. DB 핑에 실패해도 `db` 필드로만 에러를 알리고 HTTP 상태는 항상 `200`을 유지한다(모니터링이 "서버는 떠 있는데 DB만 죽었다"를 구분할 수 있도록).

**Response `200`**
```json
{ "status": "ok", "db": "ok" }
```
`db`는 `ok` 또는 `error`.

---

## 2. Auth (`/api/v1/auth`)

Google OAuth + 백엔드 발급 JWT. access 토큰은 `Authorization: Bearer`, refresh 토큰은 httpOnly 쿠키. `mock` provider로 크리덴셜 없이 로그인 플로우를 검증할 수 있다.

### `GET /api/v1/auth/providers` — 활성 로그인 수단 목록
**역할**: `AUTH_PROVIDERS` 환경변수로 켜져 있는 provider 키를 알려준다. 프론트 로그인 화면이 어떤 버튼을 그릴지 여기서 결정.

**Response `200`** → `ProvidersResponse`
| field | type |
|---|---|
| providers | string[] |

예: `{"providers": ["mock", "google"]}`

### `GET /api/v1/auth/login/{provider}` — OAuth 로그인 시작 (브라우저 전용)
**역할**: IdP 인증 페이지로 리다이렉트하고 CSRF 방지용 `state`를 쿠키에 심는다. **리다이렉트라 Swagger "Try it out"으로는 동작하지 않는다** — 브라우저 주소창에서 직접 열 것.

**Parameters**
| name | in | type | required | 설명 |
|---|---|---|---|---|
| provider | path | string | Y | `google`, `mock` 등 |
| origin | query | string | N | 로그인 시작한 프론트 origin(`window.location.origin`). PC/모바일(LAN IP)을 구분해 콜백을 돌려보내는 데 씀. `CORS_ALLOW_ORIGINS` 화이트리스트 밖이면 무시 |

**Response**: `307` IdP로 리다이렉트 + state 쿠키. `400` 지원하지 않는 provider.

### `GET /api/v1/auth/callback/{provider}` — OAuth 콜백 (IdP가 호출)
**역할**: IdP가 인증 후 되돌아오는 지점. code를 access/refresh 토큰으로 교환하고 프론트 `/auth/callback`으로 리다이렉트한다.

**Parameters**
| name | in | type | required | 설명 |
|---|---|---|---|---|
| provider | path | string | Y | |
| code | query | string | N | IdP가 붙여주는 authorization code |
| state | query | string | N | 로그인 시작 때 심은 state. 쿠키 값과 불일치하면 CSRF로 간주 |

**Response**: `307` 프론트로 리다이렉트 + refresh 쿠키(httpOnly). `400` state 불일치 또는 code 교환 실패.

### `POST /api/v1/auth/token/refresh` — access 토큰 재발급
**역할**: httpOnly refresh 쿠키를 검증하고 새 access 토큰을 발급한다. 프론트는 콜백 착지 시 이걸 호출해 access 토큰을 메모리에 확보한다.

**Response `200`** → `TokenResponse`
| field | type | 설명 |
|---|---|---|
| access_token | string | |
| token_type | string | 기본 `bearer` |
| expires_in | integer | 초 단위 |

**Response `401`**: refresh 쿠키 없음/만료/무효, 또는 비활성 사용자.

### `POST /api/v1/auth/logout` — 로그아웃
**역할**: refresh 쿠키를 삭제한다. refresh 토큰이 stateless JWT라 서버측 강제 폐기는 없음(로그아웃 = 쿠키 삭제).

**Response**: `204` 본문 없음.

### `GET /api/v1/auth/me` — 현재 로그인 사용자
**역할**: Bearer 토큰이 가리키는 사용자 정보 조회. 프론트 상태 초기화/헤더 표시용.

**Response `200`** → `UserRead`
| field | type |
|---|---|
| id | integer |
| email | string |
| name | string \| null |
| role | string |

**Response `401`**: 토큰 없음/만료/무효.

---

## 3. Dashboard (`/api/v1/dashboard`, `/api/v1/activity`)

> **이 섹션의 응답만 camelCase.** 프론트가 먼저 확정한 계약에 맞추기 위한 예외 — 나머지 API는 전부 snake_case.

### `GET /api/v1/dashboard/summary` — 대시보드 집계
**역할**: 홈 대시보드에 필요한 통계 4종 + 최근 7일 처리량 + 문서 유형 분포 + 파이프라인 단계별 카운트를 한 번에 내려준다.

**Parameters**
| name | in | type | required | 설명 |
|---|---|---|---|---|
| project_id | query | integer | N | 지정 시 해당 프로젝트만 집계, 생략하면 전체. 없는 id면 `404` |

**Response `200`** → `DashboardSummary`
| field | type | 설명 |
|---|---|---|
| stats | `DashboardStats` | |
| weeklyProcessing | integer[] | 오늘 포함 최근 7일, 과거→오늘 순 |
| documentTypes | `DocumentTypeCount[]` | |
| pipeline | `PipelineStage[]` | 8개 상태 고정 순서, 카운트 0이어도 항상 포함 |

**`DashboardStats`**
| field | type | 설명 |
|---|---|---|
| projects | integer | |
| documents | integer | |
| processing | integer | `PARSING`/`CHUNKING`/`EMBEDDING`만 카운트 |
| ragToday | integer | RAG 미구현이라 **항상 0** |

**`DocumentTypeCount`**: `{ label: string, value: integer }`
**`PipelineStage`**: `{ status: string, count: integer }`

**Response `404`**: `project_id`로 지정한 프로젝트 없음.

### `GET /api/v1/activity` — 최근 활동 피드
**역할**: `activity_logs`(2026-08-16 도입, append-only, FK 없음)를 최신순으로 반환한다. 대시보드 "최근 활동" 카드와 프로젝트별 활동 탭에서 공용으로 쓴다.

**Parameters**
| name | in | type | required | 설명 |
|---|---|---|---|---|
| limit | query | integer | N | 가져올 최대 건수 |
| project_id | query | integer | N | 지정 시 해당 프로젝트만. 없는 id면 `404` |
| action | query | string | N | 액션 코드 정확 일치 — `auth.login` · `project.create\|update\|delete` · `folder.create\|update\|delete` · `document.upload\|delete` · `analysis.start\|complete\|fail` |

**Response `200`** → `ActivityItem[]`
| field | type | 설명 |
|---|---|---|
| targetType | string \| null | `document`/`project`/... |
| targetId | integer \| null | **테이블별 시퀀스라 targetType 없이 쓰면 엉뚱한 리소스로 연결될 수 있음** |
| targetAlive | boolean | 응답 생성 시점에 대상 행이 실제로 있는지(스냅샷). 프론트는 `targetType && targetId != null && targetAlive` 세 조건을 모두 확인한 뒤에만 링크를 걸어야 함 |
| at | string | ISO8601 |

(그 외 액션 코드/문구/`kind` 등 세부 필드는 `app/schemas/dashboard.py`의 `ActivityItem` 참고 — 이 표는 링크 안전성 관련 핵심 필드만 발췌)

**Response `404`**: `project_id`로 지정한 프로젝트 없음.

> **참고**: `GET /api/v1/documents/recent`(대시보드 "최근 문서" 카드용)는 이 섹션이 아니라 4절 문서 API에 있다. `GET /api/v1/rag/history`는 RAG가 아직 없어 ⏳(9절).

---

## 4. Projects & Folders (`/api/v1/projects`, `/api/v1/folders`)

전 라우트 `Authorization: Bearer` 필수. `created_by`는 클라이언트가 지정하지 않고 서버가 로그인 사용자로 채운다.

### `GET /api/v1/projects` — 프로젝트 목록
**역할**: 전체 프로젝트를 id 오름차순으로 반환. 아직 멤버십 필터링이 없어 로그인만 하면 전부 보인다.

**Response `200`** → `ProjectRead[]` (아래 스키마 참고)

### `POST /api/v1/projects` — 프로젝트 생성
**Request Body** → `ProjectCreate`
| field | type | required |
|---|---|---|
| name | string | Y |
| description | string \| null | N |

**Response `201`** → `ProjectRead`

### `GET /api/v1/projects/{project_id}` — 프로젝트 상세
**Parameters**: `project_id` (path, integer, required)
**Response `200`** → `ProjectRead`. **`404`**: 없음.

### `PATCH /api/v1/projects/{project_id}` — 프로젝트 부분 수정
**역할**: 이름·설명을 부분 수정. 보낸 필드만 반영(`exclude_unset`), 생략한 필드는 기존 값 유지.

**Request Body** → `ProjectUpdate`: `{ name?: string, description?: string }`
**Response `200`** → `ProjectRead`. **`404`**: 없음.

### `DELETE /api/v1/projects/{project_id}` — 프로젝트 삭제
**역할**: 소속 폴더·문서·버전·섹션·청크가 **CASCADE로 함께 삭제**되며 되돌릴 수 없다.

**Response**: `204`. **`404`**: 없음.

**`ProjectRead`**
| field | type |
|---|---|
| id | integer |
| name | string |
| description | string \| null |
| created_by | string \| null |
| created_at | string(datetime) |
| updated_at | string(datetime) |

### `GET /api/v1/projects/{project_id}/folders` — 폴더 목록(트리 구성용)
**역할**: 해당 프로젝트의 모든 폴더를 **평탄한 배열**로 반환 — 트리 조립은 프론트가 `parent_id`로 한다.

**Response `200`** → `FolderRead[]`. **`404`**: 프로젝트 없음.

### `POST /api/v1/projects/{project_id}/folders` — 폴더 생성
**Request Body** → `FolderCreate`: `{ name: string, parent_id?: integer }` — `parent_id` 생략 시 최상위.
**Response `201`** → `FolderRead`. **`400`**: `parent_id`가 없거나 다른 프로젝트 소속. **`404`**: 프로젝트 없음.

### `PATCH /api/v1/folders/{folder_id}` — 폴더 이름 변경 / 이동
**Request Body** → `FolderUpdate`: `{ name?: string, parent_id?: integer }`
**Response `200`** → `FolderRead`. **`400`**: 순환 이동(자기 자신·자손 밑으로 이동) 또는 다른 프로젝트 부모. **`404`**: 없음.

### `DELETE /api/v1/folders/{folder_id}` — 폴더 삭제
**역할**: 하위 폴더는 함께 삭제되지만, 그 안의 문서는 `folder_id`가 `null`이 되어 프로젝트 최상위로 올라온다(문서는 안 지워짐).

**Response**: `204`. **`404`**: 없음.

**`FolderRead`**
| field | type |
|---|---|
| id | integer |
| project_id | integer |
| parent_id | integer \| null |
| name | string |
| created_at | string(datetime) |

---

## 5. Documents (`/api/v1/...`)

Document Core — 업로드(멀티파트) → 파싱 → 청킹 파이프라인, 버전·섹션·청크 조회. 전 라우트 인증 필수.

### `GET /api/v1/projects/{project_id}/documents` — 문서 목록
**Parameters**
| name | in | type | required | 설명 |
|---|---|---|---|---|
| project_id | path | integer | Y | |
| folder_id | query | integer | N | 지정 시 해당 폴더 직속만. 생략하면 프로젝트 전체(하위 폴더 포함) |
| status | query | `ProcessingStatus` | N | **현재 버전의 처리 상태**로 필터(문서 생명주기 컬럼이 아님). enum 검증 실패 시 `422` |
| type | query | string | N | `document_type` 정확 일치(예: `pdf`, `RULE`) |

**Response `200`** → `DocumentRead[]` (작성자·현재 버전 포함, 최근 등록순). **`404`**: 프로젝트 없음.

### `POST /api/v1/projects/{project_id}/documents` — 문서 업로드 (멀티파트)
**역할**: 파일을 저장하고 문서 + v1 버전을 만든 뒤 **즉시 `202`**. 이후 백그라운드에서 파싱·청킹이 진행되며 `GET /versions/{id}/status`로 폴링한다.

**Parameters**: `project_id` (path, required)
**Request Body** (`multipart/form-data`) → `Body_upload_document_...`
| field | type | required | 설명 |
|---|---|---|---|
| file | binary | Y | 업로드 원본 파일 |
| folder_id | integer | N | 생략하면 프로젝트 최상위 |
| name | string | N | 생략하면 업로드 파일명 |
| description | string | N | |

**Response `202`** → `DocumentUploadResponse`: `{ document: DocumentRead, version: DocumentVersionRead }`
**Response `400`**: 지원하지 않는 확장자(화이트리스트: pdf/hwpx/hwp/docx/xlsx/pptx/txt/md), 용량 초과(기본 50MB), 빈 파일, 또는 잘못된 `folder_id`.
**Response `404`**: 프로젝트 없음.

### `GET /api/v1/documents/recent` — 최근 문서 (프로젝트 교차)
**역할**: 대시보드 "최근 문서" 카드 전용 — `/projects/{id}/documents`와 달리 **프로젝트를 가로질러** 최근 등록순으로 반환. 이 라우트는 `router.py`에서 반드시 `/documents/{document_id}` **앞에** 등록돼야 한다(뒤에 두면 `recent`가 id로 매칭돼 `422`).

**Parameters**
| name | in | type | required | 설명 |
|---|---|---|---|---|
| limit | query | integer | N | 1~50, 기본 5 |
| project_id | query | integer | N | 생략 시 전체 프로젝트 |

**Response `200`** → `RecentDocumentRead[]` — `project_name` + 현재 버전 `processing_status`가 펼쳐진 납작한 뷰. `author`/버전 상세는 없음. **`404`**: `project_id` 지정했는데 없음.

### `GET /api/v1/documents/{document_id}` — 문서 상세
**Response `200`** → `DocumentRead`. **`404`**: 없음.

**`DocumentRead`**
| field | type | 설명 |
|---|---|---|
| id | integer | |
| project_id | integer | |
| folder_id | integer \| null | |
| name | string | |
| description | string \| null | |
| document_type | string \| null | |
| current_version_id | integer \| null | |
| created_by | string \| null | |
| author | `AuthorInfo` \| null | `{ id, name, email }` |
| current_version | `DocumentVersionRead` \| null | |
| created_at / updated_at | string(datetime) | |

> `status` 필드는 **없다** — 처리 상태는 `current_version.processing_status`를 본다.

### `DELETE /api/v1/documents/{document_id}` — 문서 삭제
**역할**: 문서 삭제(저장된 파일 정리 포함).
**Response**: `204`. **`404`**: 없음.

### `GET /api/v1/documents/{document_id}/versions` — 버전 목록
**Response `200`** → `DocumentVersionRead[]` (`version_no` 내림차순, 최신 우선). **`404`**: 문서 없음.

### `GET /api/v1/documents/{document_id}/download` — 원본 파일 다운로드
**역할**: 파싱 결과가 아니라 **업로드했던 원본 바이트**를 그대로 내려준다. 현재 버전 기준.
**Response `200`**: 파일 스트림(업로드 당시 파일명·MIME). **`404`**: 문서 없음 또는 저장 파일 없음.

### `GET /api/v1/versions/{version_id}` — 버전 상세
> `version_id`는 **문서 id가 아니다** — `document.current_version.id`를 쓴다.

**Response `200`** → `DocumentVersionRead`. **`404`**: 버전 없음.

**`DocumentVersionRead`**
| field | type |
|---|---|
| id | integer |
| document_id | integer |
| version_no | integer |
| original_file_name | string |
| mime_type | string \| null |
| file_size | integer \| null |
| checksum | string \| null |
| processing_status | string |
| parser_version | string \| null |
| created_at | string(datetime) |

### `GET /api/v1/versions/{version_id}/status` — 처리 상태 폴링
**역할**: 업로드 후 파싱·청킹 진행 상황 확인용. 프론트는 업로드 직후 이걸 폴링한다.

**Response `200`** → `VersionStatusResponse`: `{ version_id: integer, status: string, progress: integer(0~100) }`
상태값: `UPLOADED · PARSING · PARSED · CHUNKING · CHUNKED · EMBEDDING · READY · FAILED`. **현재 파이프라인은 `CHUNKED`(75%)에서 종착** — `EMBEDDING→READY`는 MVP2(임베딩) 이후.
**`404`**: 버전 없음.

### `GET /api/v1/versions/{version_id}/sections` — 파싱된 섹션 목록
**역할**: 파서가 만든 섹션을 `order_no`(문서 등장 순서)로 반환. `level`(heading 깊이 1~9, 본문/표는 100)로 프론트가 접이식 트리를 그린다.

**Response `200`** → `SectionRead[]`. **`404`**: 버전 없음.

### `GET /api/v1/versions/{version_id}/chunks` — 청크 목록
**역할**: 임베딩·검색의 최소 단위. `chunk_index` 오름차순, `heading_path`로 breadcrumb 제공.

**Response `200`** → `ChunkRead[]`. **`404`**: 버전 없음.

---

## 6. AI 모듈 — Proofread (오탈자 검증)

결과는 `ai_analysis_results` 테이블에 공용으로 쌓인다(`analysis_type=proofread`).

### `POST /api/v1/documents/{document_id}/proofread` — 오탈자 검증 시작 (비동기)
**역할**: 교정을 시작하고 **즉시 `202`** + `RUNNING` 레코드를 반환한다. 실제 GPT 호출은 백그라운드에서 진행되며, 완료 여부는 `GET .../analyses/{id}`를 폴링해 확인한다.

**Response `202`** → `ProofreadResult` (아래 스키마)
**`404`**: 문서가 없거나 저장된 버전이 없음. **`409`**: 아직 파싱이 안 끝나 검사할 섹션이 없음.

### `GET /api/v1/documents/{document_id}/analyses` — AI 분석 이력
**역할**: 이 문서에서 실행된 AI 분석 목록을 최신순으로, **결과 본문 없이 메타만** 반환.

**Parameters**: `type` (query, string, N) — 지금 실제로 쌓이는 값은 `proofread`뿐, `classify`/`validate`/`related`는 예약값.
**Response `200`** → `AnalysisSummary[]`. **`404`**: 문서 없음.

### `GET /api/v1/documents/{document_id}/analyses/{analysis_id}` — 분석 결과 상세 (폴링용)
**역할**: `status`(RUNNING→COMPLETED/FAILED)와 완료 시 `findings`를 반환. 프론트가 `POST .../proofread` 직후부터 이걸 폴링한다.

**Response `200`** → `ProofreadResult`. **`404`**: 해당 분석 없음.

**`ProofreadResult`** — *proofread 실행 결과. `ai_analysis_results` 1건에 대응.*
| field | type | 설명 |
|---|---|---|
| id | integer | |
| document_id | integer | |
| document_version_id | integer | |
| analysis_type | string | |
| status | string | `RUNNING`/`COMPLETED`/`FAILED` |
| provider | string \| null | `mock` 또는 `openai` |
| findings | `ProofreadFindingRead[]` | |
| sections_scanned | integer | |
| error | string \| null | 실패 사유 |
| created_at | string(datetime) | |
| applied_version_id | integer \| null | 교정 적용(apply) 완료 시 채워짐 — 재적용 가드 |
| applied_at | string(datetime) \| null | |

**`ProofreadFindingRead`** — *교정 제안 1건 (+ 원문 섹션/페이지 위치).*
| field | type | 설명 |
|---|---|---|
| id | string | 위치 기반 안정 id(`f0`, `f1`, ...) |
| original | string | 원문 |
| suggestion | string | 제안 |
| reason | string | |
| category | string | `spelling`/`spacing`(기본 범위) |
| section_id | integer \| null | |
| page_start / page_end | integer \| null | |
| status | `FindingStatus` | 기본 `pending` |

**`FindingStatus`** enum: `pending` \| `accepted` \| `rejected`

### `PATCH /api/v1/documents/{document_id}/analyses/{analysis_id}/findings/{finding_id}` — finding 승인/반려 상태 변경
**역할**: finding 하나를 accepted/rejected/pending으로 표시한다. `apply`는 **accepted만** 반영한다.

**Parameters**: `finding_id` (path, string) — 분석 결과의 `finding.id`
**Request Body** → `FindingStatusUpdate`: `{ status: FindingStatus }`
**Response `200`** → `ProofreadResult`(갱신된 findings 포함). **`404`**: 분석 또는 finding 없음. **`409`**: 분석이 아직 완료되지 않음.

### `POST /api/v1/documents/{document_id}/analyses/{analysis_id}/apply` — 승인된 교정을 원본 파일에 반영 (새 버전 생성)
**역할**: accepted 상태인 finding 전부를 DOCX/HWPX는 실제 텍스트 편집, PDF는 시각적 패치(redact+다시 그리기), TXT/MD는 오프셋 스플라이스로 반영해 **새 문서 버전**을 만들고, 그 파일을 다시 파싱/청킹하는 백그라운드 작업을 건다. 분석 1건당 1회만 적용 가능.

**Response `202`** → `ApplyCorrectionsResponse`
| field | type | 설명 |
|---|---|---|
| analysis_id | integer | |
| document_id | integer | |
| new_version_id | integer | |
| new_version_no | integer | |
| applied_count | integer | |
| skipped_count | integer | |
| skipped_reasons | string[] | 표(table) 섹션, bbox/텍스트 드리프트 등으로 스킵된 이유 |

**`400`**: 지원하지 않는 파일 형식이거나 승인된 finding이 없음/전부 매칭 실패. **`404`**: 분석 없음. **`409`**: 분석 미완료 또는 이미 적용됨(`result.applied_version_id` 존재).

---

## 7. RAG 검색 (`/api/v1/rag`) — ⏳ MVP2, 스키마 초안

| Method | Endpoint | 역할 |
|---|---|---|
| POST | `/api/v1/rag/query` | `{question, scope:{project_ids[], folder_ids[]}}` → `{answer, citations[]}`. citation: `document_id, section_id, page_start, page_end, score` |
| POST | `/api/v1/rag/query/stream` | 답변 토큰 SSE(선택) |
| GET | `/api/v1/rag/history?limit=` | 최근 질의 이력 |

---

## 8. 퀴즈 (`/api/v1/quizzes`) — ⏳ 2026-08-19 확정, MVP2 범위, 스키마 초안

RAG Retrieval Engine을 재사용해 문서 기반 문제/문제집을 생성한다. 자세한 설계 방향은 [DESIGN.md §18-③-1](DESIGN.md), 상태는 [api-spec.md §6-1](api-spec.md) 참고.

| Method | Endpoint | 역할 |
|---|---|---|
| POST | `/api/v1/quizzes/generate` | `{scope, count, difficulty?}` → RAG 기반 문제 생성(정답+해설 포함) |
| GET | `/api/v1/quizzes` | 문제집 목록 |
| GET | `/api/v1/quizzes/{quiz_id}` | 문제집 상세(문항+정답+해설) |
| PATCH | `/api/v1/quizzes/{quiz_id}` | 문제 수정/커스텀 |
| POST | `/api/v1/quizzes/{quiz_id}/attempts` | 답안 제출 → 채점 + 해설 |
| GET | `/api/v1/quizzes/{quiz_id}/attempts` | 응시 히스토리 |

---

## 9. Embedding Lab (관리자) — ⏳ MVP2, 스키마 초안

| Method | Endpoint | 역할 |
|---|---|---|
| GET | `/api/v1/embedding/models` | 등록된 임베딩 모델 목록(BGE/KURE/Qwen, status/is_active) |
| POST | `/api/v1/embedding/models/{id}/activate` | 활성 모델 전환(`is_active=true`) |
| POST | `/api/v1/embedding/models/{id}/reembed` | 전체 청크 재임베딩 트리거 |
| GET | `/api/v1/embedding/benchmarks` | 벤치마크 실행 이력 |
| POST | `/api/v1/embedding/benchmarks/run` | 벤치마크 실행(Recall@1/3/5, MRR, NDCG, latency) |
| GET | `/api/v1/embedding/benchmarks/{run_id}` | 벤치마크 결과 상세 |

---

## 10. PPT 지원 — ⏳ 2026-08-19 로드맵 백로그

새 엔드포인트는 없음 — 5절(문서)·6절(AI 모듈) 엔드포인트가 PPTX에도 그대로 동작하게 만드는 것이 목표(현재는 업로드는 되지만 파서가 없어 `FAILED`). 자세한 내용은 [api-spec.md §8](api-spec.md).

---

## 11. 알림 (`/api/v1/notifications`) — ⏳ 2026-08-19 로드맵 백로그, 스키마 초안

| Method | Endpoint | 역할 |
|---|---|---|
| GET | `/api/v1/notifications?unread_only=` | 내 알림 목록 |
| PATCH | `/api/v1/notifications/{id}/read` | 읽음 처리 |

---

## 12. 문서 정리 / 즐겨찾기 / 메모 / Notion 연동 — ⏳ 2026-08-19 로드맵 백로그, 스키마 초안

| Method | Endpoint | 역할 |
|---|---|---|
| POST | `/api/v1/documents/{doc_id}/favorite` | 즐겨찾기 토글 |
| GET | `/api/v1/favorites` | 내 즐겨찾기 목록 |
| GET/POST | `/api/v1/documents/{doc_id}/notes` | 메모 조회/작성 |

Notion 연동은 방향(유입 소스 vs export)이 미정이라 엔드포인트 초안 없음 — [api-spec.md §10](api-spec.md) 참고.

---

## 13. MCP 서버 — ⏳ 2026-08-19 로드맵 백로그 (REST 아님)

Document Core를 이 백엔드 밖의 다른 서비스/에이전트가 표준 프로토콜(MCP)로 재사용하도록 tool로 노출. REST 엔드포인트가 아니라 MCP tool 목록이라 이 문서의 표 형식이 적용되지 않는다 — 후보 tool과 착수 조건은 [api-spec.md §11](api-spec.md) / [DESIGN.md §27-3](DESIGN.md) 참고.

---

## 14. 공통 / 기타

| Method | Endpoint | 역할 | 상태 |
|---|---|---|---|
| GET | `/api/v1/search?q=` | Topbar 전역 빠른검색(문서·프로젝트 키워드) | ⏳ |

백엔드가 실행 중이면 `/openapi.json`(+ Swagger `/docs`)에서 이 문서보다 항상 최신인 스펙을 볼 수 있다.
