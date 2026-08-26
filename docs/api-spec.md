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
- 보호 라우트는 `app/api/deps.py`의 `get_current_user` 의존성 사용. **프로젝트 단위 접근 제어는 2026-08-20 구현됨** — `require_project_access`/`require_project_owner`(및 documents.py/folders.py/proofread.py의 동명 document/version/folder 버전)가 `projects.visibility`(private/invite/public) + `project_members`를 확인한다(DESIGN.md §28). 다만 이건 "이 프로젝트에 접근 가능한가"이지 "이 사용자가 관리자인가"는 아니다 — `role="admin"`은 접근 체크를 우회하지만, 그 자체를 **요구**하는 라우트(관리자 전용 Embedding Lab 등)는 아직 없다.
- **TODO**: `projects.created_by`/`documents.created_by` 등 임시 String → `users` FK 승격(`projects.owner_id`는 2026-08-20에 실제 FK로 추가됨, `created_by` 문자열 자체는 남아있음). SSO(OIDC/SAML)는 IdP 확정 후.
- refresh 토큰은 현재 **stateless JWT**(logout=쿠키 삭제) — 서버측 강제 폐기가 필요하면 refresh jti denylist/테이블 추가.

---

## 2. 대시보드 (`/api/v1/dashboard`)

| Method | Endpoint | 용도 | 상태 |
|---|---|---|---|
| GET | `/api/v1/dashboard/summary?project_id=` | 스탯 4종 + 주간 처리량 + 문서타입 분포 + 파이프라인 상태 카운트 | ✅ (`ragToday`도 2026-08-25부터 실제 값) |
| GET | `/api/v1/activity?limit=&project_id=&action=` | 최근 활동 피드 — `activity_logs` 조회 | ✅ |
| GET | `/api/v1/rag/history?limit=` | 최근 RAG 질의(대시보드/검색 공용) — 계약은 §6 | ✅ (2026-08-25) |

- **응답만 camelCase.** 프론트가 먼저 확정해 둔 계약(`frontend/src/lib/types.ts` 의 `DashboardSummary`/`ActivityItem`)에 맞추기 위해 이 섹션의 스키마만 `alias_generator=to_camel` 로 직렬화한다(`ragToday`, `weeklyProcessing`, `documentTypes`). 나머지 API 는 그대로 snake_case.
- `project_id` 는 선택 — 없으면 전체 집계, 주면 해당 프로젝트로 스코프. **없는 id 면 빈 결과가 아니라 404** 다. `/dashboard/summary`·`/activity`·`/documents/recent` 세 엔드포인트가 동일하게 동작하므로, 프로젝트 스코프 대시보드에서 `Promise.all` 로 묶어도 일부만 404 가 나는 일은 없다(2026-08-16 통일 — 그전엔 summary 만 200 빈 집계였다). 조용히 빈 결과를 주면 프론트가 오타 난 id 를 "데이터 없음"으로 오인하기 때문.
- **접근 제어(2026-08-20, DESIGN.md §28)**: `project_id`를 지정했는데 접근 권한이 없으면(3절 규칙과 동일) `403`. `project_id`를 생략하면 404/403 대신 조용히 **내가 접근 가능한 프로젝트로만 좁혀서** 집계·필터링한다 — `summary`의 전체 집계, `activity`의 피드 둘 다. 단 `activity`에서 `project_id`가 없는 로그(로그인 등)는 이 필터와 무관하게 그대로 보인다.
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

기록하는 액션(17종, 2026-08-20 기준) — DB 에는 이 **코드**를 저장하고, 표시 문구·배지 `kind` 는 읽을 때 `activity_service.describe()` 가 붙인다(문구를 바꿔도 과거 로그가 안 깨지게):

| 코드 | 문구 | kind |
|---|---|---|
| `auth.login` | 로그인 | 로그인 |
| `project.create` / `.update` / `.delete` | 프로젝트 생성/수정/삭제 | 프로젝트 · 프로젝트 · 삭제 |
| `project.visibility_change` | 프로젝트 가시성 변경 (2026-08-20) | 프로젝트 |
| `project.member_invite` / `.member_remove` | 멤버 초대/제거 (2026-08-20) | 프로젝트 · 프로젝트 |
| `folder.create` / `.update` / `.delete` | 폴더 생성/수정·이동·순서변경/삭제 | 폴더 · 폴더 · 삭제 |
| `document.upload` / `.delete` | 문서 업로드/삭제 | 업로드 · 삭제 |
| `document.version_add` | 기존 문서에 새 버전 추가 (2026-08-26) | 버전 |
| `document.move` / `.folder_change` | 문서 프로젝트간 이동/폴더변경 (2026-08-20) | 문서 · 문서 |
| `analysis.start` / `.complete` / `.fail` | (모듈명) 시작/완료/실패 | 오타 · 규정 · 분류 · 추천 |

- `folder.update` 는 이름 변경/부모 이동뿐 아니라 `PATCH /folders/{id}/reorder`(순서 변경)도 같은 코드로 기록된다(`meta.reordered=true`로 구분).
- `document.move` 는 프로젝트가 실제로 바뀐 경우, `document.folder_change` 는 같은 프로젝트 안에서 `folder_id`만 바뀐 경우 — 둘 다 `PATCH /documents/{doc_id}/move` 하나가 상황에 따라 나눠 기록한다.

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
| GET | `/api/v1/projects` | 목록(내가 접근 가능한 것만: public 전체 + 내 invite + 내 private) | 🟡 기본 CRUD·접근제어는 됨, **폴더수/문서수/READY수 집계 필드 미구현** |
| POST | `/api/v1/projects` | 생성(`visibility` 생략 시 기본 `public`) | ✅ |
| GET | `/api/v1/projects/{project_id}` | 상세 | ✅ |
| PATCH | `/api/v1/projects/{project_id}` | 이름/설명 부분 수정(`exclude_unset`), 멤버라면 누구나 | ✅ |
| PATCH | `/api/v1/projects/{project_id}/visibility` | private/invite/public 전환, **owner 전용** | ✅ (2026-08-20) |
| DELETE | `/api/v1/projects/{project_id}` | 삭제(204), **owner 전용** | ✅ |
| GET | `/api/v1/projects/{project_id}/members` | 멤버 목록(`status`로 active/pending/rejected 구분) | ✅ (2026-08-20) |
| POST | `/api/v1/projects/{project_id}/members` | `user_id` 또는 `email`로 멤버 **초대**(status=`pending`), **owner 전용** | ✅ (2026-08-20) |
| DELETE | `/api/v1/projects/{project_id}/members/{user_id}` | 멤버 제거 **겸 초대 취소**, **owner 전용**(owner 본인은 제거 불가) | ✅ (2026-08-20) |
| GET | `/api/v1/projects/invitations` | 내가 받은 수락 대기 초대 목록 | ✅ (2026-08-20) |
| POST | `/api/v1/projects/{project_id}/invitation/accept` | 초대 수락(→ active), 응답은 `ProjectRead` | ✅ (2026-08-20) |
| POST | `/api/v1/projects/{project_id}/invitation/decline` | 초대 거절(→ rejected, 204) | ✅ (2026-08-20) |
| GET | `/api/v1/users/search?q=&limit=&exclude_project_id=` | 사용자 이름/이메일 부분일치 검색(초대 대상 고르기) | ✅ (2026-08-20) |
| GET | `/api/v1/projects/{project_id}/folders` | 폴더 트리(project별 목록) | ✅ |
| POST | `/api/v1/projects/{project_id}/folders` | 폴더 생성(parent_id로 트리) | ✅ |
| PATCH | `/api/v1/folders/{folder_id}` | 폴더 수정/이동(순환 방지) | ✅ |
| PATCH | `/api/v1/folders/{folder_id}/reorder` | 폴더 순서 변경(같은 부모 내 target_index로, parent_id 지정 시 이동+순서 동시 처리) | ✅ (2026-08-20) |
| DELETE | `/api/v1/folders/{folder_id}` | 폴더 삭제(204) | ✅ |

> - **인증**: Project·Folder의 모든 라우트는 `get_current_user`(Bearer) 필수. 무토큰 시 차단.
> - **접근 제어(2026-08-20, DESIGN.md §28)**: `visibility=public`(기본값, 기존 동작과 동일 — 인증만 되면 조회/쓰기 다 가능)이 아니면 `owner`거나 `project_members`에 **`status='active'`로** 속해야(`invite`일 때만) 통과. `private`는 owner 전용(멤버로 등록돼 있어도 `invite`로 전환 전엔 통과 안 됨). 위반 시 403. Folder 라우트는 `folder_id` → project 를 거슬러 올라가 같은 규칙을 적용.
> - **초대 수락 흐름(2026-08-20 신규)**: `POST .../members`는 이제 즉시 멤버를 만들지 않고 `project_members.status='pending'`인 **초대장**만 만든다. 수락 전에는 접근 권한이 없어 `GET /projects`·`GET /projects/{id}`에 나오지 않으므로, 초대받은 사람은 `GET /projects/invitations`로 확인하고 `accept`(→`active`, 이때부터 접근 가능) 또는 `decline`(→`rejected`)한다. 거절해도 행은 남아 owner가 "거절함"을 보고 **재초대(같은 행이 다시 pending)** 할 수 있다. 수락 전 취소는 `DELETE .../members/{user_id}`(행 삭제) — 정식 멤버 제거와 같은 엔드포인트고, 응답만으로는 구분되지 않으니 프론트는 삭제 전 `status`로 문구를 정한다. 초대/수락/거절/제거는 모두 상대에게 알림(9절)을 보낸다.
> - `ProjectMemberRead` = `user_id, email, name, role, status, invited_by, created_at(=초대 시각), responded_at(수락·거절 시각, pending이면 null)`. `ProjectInvitationRead` = `project_id, project_name, project_description, visibility, invited_by, invited_by_name, invited_by_email, invited_at`.
> - **`GET /users/search`**: 인증만 필요(누구나 사용자를 찾을 수 있다 — 사내 플랫폼 전제). `q`는 이름/이메일 부분일치, 대소문자 무시, **최소 2자**(1자면 422), 앞부분 일치가 위로 정렬. 비활성(`is_active=false`) 사용자는 제외. 응답은 `id, email, name` 뿐(provider/role 등 미노출). `exclude_project_id`를 주면 그 프로젝트의 `active`+`pending` 사용자를 결과에서 빼며(재초대 가능한 `rejected`는 남긴다), 멤버 목록이 간접 유출되지 않도록 **그 프로젝트에 접근 가능한 사람만** 이 필터를 쓸 수 있다(아니면 403/404).
> - `created_by`는 클라이언트가 지정하지 않고 **서버가 로그인 사용자 id로 채운다**(임시 String → 향후 users FK 승격 예정). `owner_id`는 2026-08-20부터 실제 FK.
> - 현재 `ProjectRead` = `id, name, description, created_by, owner_id, visibility, created_at, updated_at`. 목록 뷰의 집계 필드(폴더수/문서수/READY수)는 별도 `ProjectListItem` 스키마로 추가 예정.
> - Folder 수정 시 부모를 자기 자손 밑으로 옮기면 `400`(순환 방지), 다른 프로젝트 부모도 `400`.
> - **폴더 순서(`rank`, 2026-08-20 신규)**: `folders.rank`(정수, 형제 사이 기본 간격 1000)로 정렬. 생성 시 해당 parent 안 최댓값+1000으로 끝에 붙는다. `reorder`는 `target_index`(0-based, 범위를 벗어나면 clamp)로 옮긴 뒤 영향받은 형제 전체를 0부터 1000 간격으로 재부여하고, `parent_id`를 함께 주면 부모 이동(순환/타 프로젝트 검사 동일 적용)까지 한 번에 처리한다. 응답은 새 부모 기준 형제 목록 전체(`FolderRead[]`, `rank` 포함). `FolderRead`에 `rank` 필드가 추가됐다.

---

## 4. 문서 (`/api/v1/...`)

업로드는 `202 Accepted` + 백그라운드 처리(파싱→청킹), 상태 폴링. 전 라우트 인증 필수. (Phase 5·6·7 완료)

- **접근 제어(2026-08-20, DESIGN.md §28)**: 이 절의 모든 라우트가 문서/버전 → 소속 프로젝트를 거슬러 올라가 3절과 동일한 규칙(`visibility` + `project_members`)을 적용한다 — 위반 시 `403`. `project_id`가 경로에 없는 라우트(`{doc_id}`, `{version_id}` 기준)는 `check_document_access`/`check_version_access` 의존성이 대상을 먼저 조회해 소속 프로젝트로 접근을 확인한다. `/documents/recent`·`/documents/search`는 `project_id`를 생략하면 조회 자체가 아예 내가 접근 가능한 프로젝트로 좁혀진다(403이 아니라 결과에서 제외).

| Method | Endpoint | 용도 | 상태 |
|---|---|---|---|
| GET | `/api/v1/documents/recent?limit=&project_id=` | 대시보드용 **프로젝트 교차** 최근 문서(+project_name+processing_status) | ✅ |
| GET | `/api/v1/documents/search?q=&limit=` | 전역 검색창용 **프로젝트 교차** 문서 검색(이름/설명 ILIKE) | ✅ |
| GET | `/api/v1/documents/{doc_id}/diff?from_version_id=&to_version_id=` | 두 버전 섹션 비교(added/deleted/modified/unchanged + 토큰 diff) | ✅ |
| GET | `/api/v1/projects/{project_id}/documents?folder_id=&status=&type=` | 문서 목록(+author+current_version). `status` 는 **현재 버전의 처리 상태**(enum 검증 → 잘못된 값은 422) | ✅ |
| POST | `/api/v1/projects/{project_id}/documents` (multipart) | 업로드 → 202 `{document, version}` | ✅ |
| GET | `/api/v1/documents/{doc_id}` | 상세(+author+current_version) | ✅ |
| PATCH | `/api/v1/documents/{doc_id}` | 이름/폴더/타입 변경(예: RULE 지정) | ⏳ |
| PATCH | `/api/v1/documents/{doc_id}/move` | 다른 프로젝트로 이동(`project_id`+선택 `folder_id`). 원본·대상 프로젝트 접근권한 둘 다 필요 | ✅ (2026-08-20) |
| DELETE | `/api/v1/documents/{doc_id}` | 삭제(파일 정리 포함, 204) | ✅ |
| GET | `/api/v1/documents/{doc_id}/download` | 원본 다운로드 | ✅ |
| GET | `/api/v1/documents/{doc_id}/versions` | 버전 목록 | ✅ |
| POST | `/api/v1/documents/{doc_id}/versions` (multipart) | 기존 문서에 새 버전(v2+) 추가 → 202 `version`. Document는 새로 만들지 않음 | ✅ (2026-08-26) |
| GET | `/api/v1/versions/{version_id}` | 버전 상세 | ✅ |
| GET | `/api/v1/versions/{version_id}/status` | 처리상태 폴링 `{status, progress}` | ✅ |
| GET | `/api/v1/versions/{version_id}/sections` | 파싱된 Section(계층, bbox meta) | ✅ |
| GET | `/api/v1/versions/{version_id}/chunks` | 청크(heading_path·bbox meta) | ✅ |
| GET | `/api/v1/versions/{version_id}/events` | 진행 SSE(선택) | ⏳ |

- 상태 값: `UPLOADED · PARSING · PARSED · CHUNKING · CHUNKED · EMBEDDING · READY · FAILED` (모델 `document_versions.processing_status`). `status→progress`(%) 매핑 제공. **2026-08-25부터 `CHUNKED` 다음 `EMBEDDING→READY`까지 자동 진행**(§7 Embedding Lab 참고) — 단, 활성 임베딩 모델이 없거나 임베딩 자체가 실패하면(모델 미설치/다운로드 실패 등) `FAILED`가 아니라 `CHUNKED`로 되돌아간다(오탈자 검증 등 `CHUNKED` 이상만 필요한 기능은 계속 동작해야 하므로).
- **응답 보강**: 문서 상세/목록은 `author`(created_by id 해석 → id/name/email)와 `current_version`(version_no/processing_status/파일메타)를 포함(목록은 batch 조회로 N+1 방지). **`DocumentRead` 에 `status` 키는 없다** — 처리 상태는 `current_version.processing_status` 를 볼 것(위 "문서 상태는 하나뿐" 참고).
- **`/documents/recent`** (대시보드 전용): `/projects/{id}/documents` 와 달리 **프로젝트를 가로질러** 최근 등록순으로 준다. 카드에서 추가 요청 없이 프로젝트 경로와 상태 배지를 그릴 수 있게 `project_name` 과 현재 버전의 `processing_status` 를 펼쳐 담은 납작한 뷰(`RecentDocumentRead`)이고, `author`/버전 상세는 없다. `limit` 1~50(기본 5), `project_id` 는 선택. 라우터에서 이 경로는 반드시 `/documents/{document_id}` **앞에** 등록해야 한다(뒤면 `recent` 가 id 로 매칭돼 422).
- **`/documents/search`** (전역 검색창용, 2026-08-20 신규): `/documents/recent`와 응답 shape(`RecentDocumentRead`)은 같지만 최신순이 아니라 `q` 키워드로 이름/설명을 대소문자 무시 부분일치(`ILIKE`) 검색 — 역시 프로젝트를 가로지른다. 빈 검색어는 빈 배열. **지금은 이름/설명 단순 키워드 매칭뿐** — 프로젝트/상태/기간 등으로 좁히는 상세검색은 아직 없고 추후 추가 예정(필터 파라미터를 늘리는 형태가 될 듯). 라우터 등록 순서도 `recent`와 동일한 이유로 `/documents/{document_id}` 보다 앞이어야 한다.
- **`/documents/{doc_id}/diff`** (2026-08-20 신규): "수많은 문서 중 비슷한 걸 찾는" 검색 문제가 아니라 "이미 정해진 v1·v2, 이 둘만 비교"하는 문제라 임베딩/LLM 없이 표준 라이브러리 `difflib`만으로 **동기** 계산한다(202+폴링 아님). 두 버전의 `document_sections`를 `(level, section_type, 정규화된 텍스트)` 키로 순서를 존중하며 정렬(`SequenceMatcher.get_opcodes`)해 섹션 단위 added/deleted/modified/unchanged를 가르고, "modified" 섹션만 단어+공백 토큰 단위로 다시 diff해 `{op, text}` 토큰 리스트를 만든다 — 프론트가 이 토큰을 순서대로 이어붙이면 원문이 그대로 재구성되고, 순서대로 렌더링하면 인텔리제이 git diff처럼 삭제/추가/유지 구간을 표시할 수 있다. `from_version_id`/`to_version_id`가 해당 문서 소속이 아니면 `404`. GPT 설명이나 저장(`ai_analysis_results`)은 없음 — 매 요청 재계산.
- **업로드**: multipart `file`(필수) + `folder_id`/`name`/`description`(선택). 확장자 화이트리스트(pdf/hwpx/hwp/docx/xlsx/pptx/txt/md, `.env`로 조정) + 최대 50MB + 빈 파일 거부, 위반 시 `400`. 새 문서=version 1, `current_version_id` 세팅. sha256/file_size 저장. `description`은 문서(Document) 단위 컬럼.
- **저장**: `StorageProvider`(Local) — `storage/documents/project-{pid}/document-{did}/v{n}/{uuid}.{ext}`, DB엔 상대 `storage_path`만. 컨테이너는 `/data/storage` 볼륨.
- **파이프라인(BackgroundTasks)**: 업로드 → `run_parse_task` → Parser(포맷별 어댑터 + PDF 스캔본 OCR) → `document_sections`(계층/bbox) → Chunking(heading 유무로 구조 인지/semantic 자동 분기, §4 위 항목 참고) → `document_chunks` → Embedding(활성 모델로 `chunk_embeddings` 채움, §7) → `READY`. 미지원 확장자(hwp/xlsx/pptx)는 `FAILED`.
- **PDF 스캔본 OCR 품질(2026-08-18)**: OCR 경로도 네이티브 텍스트 경로와 동등하게 취급한다 — Tesseract 전에 전처리(그레이스케일→디스큐→CLAHE, `opencv-python-headless`), 줄 높이 기반 제목 인식(네이티브의 폰트크기 휴리스틱과 동일 비율), confidence 60 미만 줄은 `meta.low_confidence=true`로 표시하고 문서 전체 저신뢰 비율 30% 이상이면 서버 로그에 경고. 프론트 배지는 아직 없음 — `meta.low_confidence`를 그대로 노출만 하는 상태.
- **Section 표현 규칙**: `heading` 섹션은 `title`만 채우고 `content`는 비운다(제목 중복 표시 방지). 본문/표는 `title` 없이 `content`만. `level`(heading 깊이 1~9, 본문/표는 100)을 저장해 `SectionRead.level`로 노출 → 프론트가 `level`+`parent_section_id`로 접이식 트리 렌더. heading이 하나라도 있는 문서는 구조 인지 청킹(heading은 chunk로 만들지 않고 breadcrumb `chunk_metadata.heading_path`로만 사용), heading이 전혀 없는 문서(txt/hwpx, 스타일 없는 docx 등)는 semantic 청킹(문장 임베딩 거리 기반 경계 탐지)으로 자동 분기 — `chunk_metadata.chunking_strategy`("structure"|"semantic")로 구분 가능.
- **아직**: `PATCH /documents/{id}`(이름/폴더/타입 변경), 사용자가 임의로 새 버전을 올리는 범용 업로드 엔드포인트(v2+ 생성 *메커니즘* 자체는 `DocumentService.create_version()`으로 이제 있지만, 지금은 아래 5절의 교정 적용(apply) 흐름에서만 호출된다 — "이 문서의 새 버전을 업로드" 라는 별도 라우트는 없음), SSE.

---

## 5. AI 모듈 (결과는 `ai_analysis_results`에 적재)

| Method | Endpoint | 결과 | 상태 |
|---|---|---|---|
| POST | `/api/v1/documents/{doc_id}/proofread` | 교정 시작 → `202` + RUNNING 레코드(백그라운드 처리) | ✅ (Phase 8, MVP1 마지막) |
| GET | `/api/v1/documents/{doc_id}/analyses?type=` | AI 분석 이력(메타 목록) | ✅ |
| GET | `/api/v1/documents/{doc_id}/analyses/{analysis_id}` | 분석 결과 상세(status 폴링 + findings) | ✅ |
| PATCH | `/api/v1/documents/{doc_id}/analyses/{analysis_id}/findings/{finding_id}` | finding 승인/반려/보류 상태 변경 | ✅ |
| POST | `/api/v1/documents/{doc_id}/analyses/{analysis_id}/apply` | 승인된 finding을 원본에 반영해 새 버전 생성(202, 파싱은 백그라운드) | ✅ |
| POST | `/api/v1/documents/{doc_id}/classify` | 문서타입·카테고리·태그 | ⏳ (MVP3) |
| POST | `/api/v1/documents/{doc_id}/validate` | 규정 검증(RULE 검색 → GPT) | ⏳ (MVP5) |
| POST | `/api/v1/documents/{doc_id}/related` | 관련 문서 추천(vector search) | ⏳ (MVP3) |

- **접근 제어(2026-08-20)**: 이 절의 모든 라우트도 4절과 동일하게 `document_id` → 소속 프로젝트로 접근을 확인한다(`proofread.py`의 `check_document_access`) — 위반 시 `403`.
- **문서비교는 여기 없다**: 처음엔 이 표에 있었지만, 검색(임베딩)도 GPT 판단도 필요 없는 "이미 정해진 두 버전 비교"라 4절 `/documents/{doc_id}/diff`(동기, `ai_analysis_results` 미사용)로 옮겼다. `classify`/`validate`/`related`는 반대로 셋 다 DESIGN.md상 "RAG Retrieval 재사용" 구조라 `chunk_embeddings`/`embedding_models`를 채우는 임베딩 파이프라인(MVP2, 아직 스키마만 있고 완전히 비어 있음)이 선행돼야 실제 동작한다 — 지금은 스텁만 있는 상태.
- **proofread 흐름(202 + 폴링)**: `POST /proofread` 는 검증 후 `RUNNING` 레코드를 만들고 **즉시 `202`** 반환 → BackgroundTasks 가 배치 교정 수행 → 프론트는 `GET .../analyses/{analysis_id}` 로 `status`(RUNNING→COMPLETED/FAILED)를 폴링. 파싱 전(`UPLOADED/PARSING`)이면 `409`, 문서 없음 `404`.
- **배치 호출**: 섹션을 글자수/개수 상한(`proofread_batch_max_chars`·`_max_sections`)으로 묶어 **한 프롬프트에 여러 섹션**을 보낸다(`[번호]` 마커 → 결과 index 로 되매핑). 섹션당 1회 순차 호출 대비 호출 수·시간·비용 대폭 절감(11섹션 기준 순차 17s → 배치 1회 ~7.5s). 각 finding 은 `original→suggestion`·`reason`·`category` + `section_id`·`page_start/end`.
- **LLM provider 추상화**(`app/services/llm/`): `MockLLMProvider`(규칙 기반, 키 불필요) + `OpenAILLMProvider`(httpx Chat Completions, `proofread`/`proofread_batch`). `LLM_PROVIDER=auto`면 `OPENAI_API_KEY` 유무로 자동 선택. 프롬프트는 `app/services/llm/prompts.py`에 분리. 최신 모델(gpt-5.x)은 `temperature` 커스텀 불가라 payload 에서 생략.
- **교정 범위**(`PROOFREAD_CATEGORIES`, 기본 `["spelling","spacing"]`): 문서가 길어질수록 GPT 가 "더 자연스럽게" 류의 표현·문맥 지적을 쏟아내 정작 오탈자가 묻히므로 기본을 오탈자·띄어쓰기로 좁혀 뒀다(같은 문서 실측 12건 → 9건, 표현·문법 4건 전부 제거). 프롬프트가 대상 유형·금지 항목·**category enum 을 함께 좁히고**(enum 을 열어 두면 모델이 제외 대상을 남은 카테고리에 욱여넣는다), 그래도 새어 나온 지적은 provider 가 응답에서 걸러 로그에 남긴다. 단 처음 보는 라벨은 버리지 않고 기본 카테고리로 보정한다 — 라벨 오타 때문에 멀쩡한 오탈자 지적을 잃지 않기 위함. 표현 교정까지 받으려면 `.env` 에 4종을 모두 적고 **재시작**.
- **결과 적재**: `ai_analysis_results`(document/version FK, `analysis_type`, `status`=RUNNING/COMPLETED/FAILED, `provider`, `result` JSONB, `error`) — proofread 외 classify·validate·related 결과도 이 테이블을 공용으로 쓴다.
- **교정 적용(apply, 2026-08-18 신규)**: finding마다 `id`(위치 기반, `f0`/`f1`.../구버전 레코드는 조회 시 같은 규칙으로 역채움)와 `status`(pending/accepted/rejected)가 붙는다. `PATCH .../findings/{finding_id}`로 하나씩 승인/반려한 뒤 `POST .../apply`를 호출하면 **accepted 상태인 finding 전부**를 원본 파일에 반영해 새 `document_version`을 만든다:
  - **DOCX/HWPX**: 실제 텍스트 편집. run(`<w:r>`)/HWPX 텍스트노드(`<hp:t>`) 경계를 인식하는 부분 치환(`app/services/correction_service.py`의 `_replace_span_in_nodes`)이라, 교정 구간과 안 겹치는 서식(볼드/색상 등)은 그대로 보존된다. `docx_parser`/`hwpx_parser`가 파싱 때 쓰는 것과 동일한 문단/블록 순회 규칙을 재사용해 `section.order_no`로 원본 위치를 되찾고, 되찾은 문단 텍스트가 분석 시점과 다르면(드리프트) 해당 finding만 skip한다.
  - **PDF**: 완전 편집이 불가능해 **시각적 패치**로 대체 — 저장해 둔 `bbox`(+ `font_size`/`line_height`) 자리를 `add_redact_annot`/`apply_redactions`로 지우고 교정된 텍스트를 `insert_textbox`로 다시 그려 넣는다.
  - **TXT/MD**: 서식 구조가 없어 블록(빈 줄 구분) 단위 오프셋 기반 문자열 치환. 여러 섹션을 동시에 고쳐도 오프셋이 밀리지 않도록 원본 좌표 기준으로 한 번에 스플라이스한다.
  - 새 버전은 기존 `run_parse_task`(parse+chunk)를 그대로 타므로, 재파싱된 섹션/청크에 교정 결과가 자연스럽게 반영된다(섹션/청크를 손으로 patch하지 않음) — 이게 MVP2 임베딩이 오탈자를 인덱싱하지 않게 만드는 핵심 이유.
  - 표(table) 섹션, bbox/텍스트 드리프트가 있는 finding은 skip되고 `skipped_reasons`로 반환된다. 분석 1건당 **1회만 적용 가능**(`result.applied_version_id`로 가드, 재호출은 `409`). accepted가 0건이거나 전부 매칭 실패, 또는 DOCX/HWPX/PDF/TXT/MD가 아닌 포맷이면 `400`.
  - `DocumentService.create_version()`이 이 흐름 전용으로 새로 생긴 v2+ 버전 생성 메서드다(`upload_document`는 여전히 v1 + 신규 Document만 담당). 프론트 UI(승인/반려 토글, 적용 버튼)는 아직 없다.

> 참고: 초기의 `POST /api/v1/spell-check`(입력을 그대로 돌려주던 스텁)는 **삭제됐다**(2026-08-16). 소비자가 없었고 실제 교정은 `proofread` 가 담당한다.

---

## 6. RAG 검색 (`/api/v1/rag`) — MVP2

| Method | Endpoint | 결과 | 상태 |
|---|---|---|---|
| POST | `/api/v1/rag/query` | `{question, scope?:{project_ids[], folder_ids[]}, conversation_id?, top_k?}` → `{conversation_id, message_id, question, search_question, answer, citations[], provider, embedding_model, retrieved_count, latency_ms}` | ✅ (2026-08-25) |
| POST | `/api/v1/rag/search` | 같은 입력(+`min_score?`) → `{question, embedding_model, hits[]}` — **GPT 없이 근거 청크만** | ✅ (2026-08-25) |
| GET | `/api/v1/rag/history?limit=` | 내 최근 질문 `[{conversation_id, message_id, question, at}]` | ✅ (2026-08-25) |
| GET | `/api/v1/rag/conversations?limit=` | 내 대화 목록(최근 수정순) | ✅ (2026-08-25) |
| GET | `/api/v1/rag/conversations/{id}` | 대화 상세(메시지 전체, assistant 턴에 citations) | ✅ (2026-08-25) |
| POST | `/api/v1/rag/query/stream` | 같은 입력 → Server-Sent Events(`text/event-stream`) | ✅ (2026-08-25) |

- **두 단계로 나뉜다**: ① 검색 = 로컬 임베딩(활성 모델) + pgvector, ② 생성 = GPT. `/search`는 ①만, `/query`는 ①+②다. 답변이 이상할 때 검색 문제인지 생성 문제인지 가르기 위해 ①을 따로 노출한다(DESIGN.md §21의 14→16 순서). `/search`는 의미 기반 문서검색 UI·관련문서 추천도 같은 결과를 쓴다.
- citation: `index, document_id, document_name, section_id, chunk_id, page_start, page_end, score, heading_path`. **답변 본문의 `[1]` 표기가 `citations[].index`와 대응**하므로 프론트는 이 번호로 각주 링크를 만든다. 근거로 답할 수 없으면 GPT가 "제공된 문서에서는 확인할 수 없습니다"로 답하고 `citations`는 빈 배열이다(없는 내용을 만들지 않게 프롬프트에서 강제).
- **검색 범위**: `scope` 생략 시 "내가 접근 가능한 프로젝트 전체"(dashboard/문서검색과 같은 규칙). `scope.project_ids`에 접근 권한 없는 프로젝트를 넣으면 `403`, 없는 프로젝트면 `404`. 검색 대상은 **각 문서의 현재 버전 + 활성 임베딩 모델의 벡터**뿐이다 — 구버전 청크(교정 apply로 밀려난 v1 등)와 임베딩이 없는 문서는 결과에 없다.
- **활성 임베딩 모델이 없으면 `503`** — 질문을 문서와 같은 벡터 공간으로 만들 수 없다. 빈 결과(`200`)와 구분해야 하는 상태라 조용히 넘기지 않는다.
- **대화(멀티턴)**: `conversation_id`를 주면 이어 붙이고 생략하면 새로 만든다. 이전 `RAG_HISTORY_TURNS`(기본 4)턴을 답변 생성 프롬프트에 넣는다. 이어지는 질문은 첫 질문의 `scope`를 그대로 물려받는다. 남의 `conversation_id`는 `403`.
- **질문 재작성 (2026-08-25)**: 대화 이력이 있는 후속 질문은 검색 전에 독립 질문으로 재작성된다("그럼 그건 언제부터 적용돼?" → "정기 배포일을 수요일로 2주에 한 번 진행하는 방식은 언제부터 적용돼?") — 검색(임베딩+FTS)에만 쓰이고 답변 생성 프롬프트의 `question`은 원문 그대로다. 재작성 결과는 `/rag/query`의 `search_question`(원문과 같으면 `null`), `/rag/query/stream`의 `meta.search_question`으로 노출된다. `RAG_QUERY_REWRITE_ENABLED=false`로 끌 수 있다(끄면 원문으로 그대로 검색).
- **문맥 확장**: 검색된 청크와 같은 `heading_path`(같은 조/절)의 나머지 청크를 `RAG_EXPAND_MAX_EXTRA`(기본 6)개까지 근거에 덧붙인다. 표처럼 한 조가 여러 청크로 쪼개진 경우를 위한 장치이고, `/search` 응답에서는 `expanded: true`로 구분된다.
- **하이브리드 검색 (2026-08-25)**: 벡터 검색 + PostgreSQL 전문(FTS) 검색을 RRF(Reciprocal Rank Fusion)로 합친다(`RAG_HYBRID_ENABLED`, 기본 켜짐). FTS는 질문을 접두어(`:*`) 매칭으로 쿼리해 한국어 조사가 붙어도("배포는" → "배포:*") 걸린다. **`citations[].score`는 하이브리드 모드에서 RRF 융합 점수이지 코사인 유사도가 아니다** — 절대값이 아니라 같은 응답 안에서의 상대 순위로 봐야 한다. `min_score`(`/search`의 요청 파라미터)는 벡터 후보 단계에만 적용되는 코사인 하한이고 FTS 후보는 그 값과 무관하게 뽑힌다.
- `GET /rag/history`·`dashboard.stats.ragToday`(§2)는 이제 실제 값이다 — `rag_messages`에서 `role='user'`인 행을 센다.
- **SSE 스트리밍 (2026-08-25)**: `/rag/query/stream`은 `/rag/query`와 요청 바디가 동일하고, 응답만 `text/event-stream`이다. 각 이벤트는 `event: <type>\ndata: <json>\n\n` 형식.
  - `meta` `{conversation_id, embedding_model, retrieved_count}` — 스트림 맨 처음, 검색이 끝난 시점.
  - `delta` `{text}` — 답변 조각. 순서대로 이어 붙이면 최종 답변 본문이 된다.
  - `citations` `{citations}` — `/rag/query`의 `citations[]`와 같은 형식. 답변 본문의 `[n]` 표기에서 뽑으므로 마지막 `delta` 다음, 한 번만 온다.
  - `done` `{conversation_id, message_id, provider, retrieved_count, latency_ms}` — 마지막 이벤트. 여기까지 왔으면 대화/메시지가 DB에 저장 완료된 것.
  - `error` `{detail}` — LLM 호출 자체가 실패했을 때(네트워크 오류 등). 이 이벤트 뒤로는 스트림이 끝나고 `citations`/`done`은 오지 않으며, 아무것도 저장되지 않는다.
  - 검색/권한 오류(`404`/`403`)와 활성 임베딩 모델·LLM 설정 누락(`503`)은 스트림을 열기 전에 걸러 **평범한 HTTP 에러**로 응답한다 — SSE가 한 번 시작되면 상태코드를 더 바꿀 수 없어서다. LLM 호출 자체의 실패만 `error` 이벤트로 온다는 뜻.

### 6-1. 퀴즈 — 문제은행/문제집/AI 생성 (2026-08-25)

2026-08-19 초안(RAG 기반 자동 생성 우선 설계)을 실제 프론트 UI(`frontend/src/components/quiz/`) 기준으로 대체. CRUD 5개 테이블(`quiz_questions`/`quiz_books`/`quiz_book_questions`/`quiz_sessions`/`quiz_answers`, 마이그레이션 `b6bc43463e34`) + AI 생성 작업 테이블(`quiz_generation_jobs`, `97198d524333`). 상세는 [docs/STATUS.md](STATUS.md) 참고.

문제은행:

| Method | Endpoint | 결과 | 상태 |
|---|---|---|---|
| GET | `/api/v1/projects/{project_id}/quiz-questions` | 목록(`document_id`/`difficulty`/`type`/`review_status`/`q` 필터) | ✅ |
| POST | `/api/v1/projects/{project_id}/quiz-questions` | 문제 직접 작성(`generation_type=MANUAL`, `review_status=DRAFT`) | ✅ |
| PATCH | `/api/v1/quiz-questions/{question_id}` | 문제 수정 | ✅ |
| DELETE | `/api/v1/quiz-questions/{question_id}` | 문제은행에서 완전 삭제(소속 문제집 연결도 cascade 해제) | ✅ |
| POST | `/api/v1/projects/{project_id}/quiz-questions/generate` | AI 문제 생성 시작 → **202** + 생성 작업 | ✅ |
| GET | `/api/v1/quiz-generation-jobs/{job_id}` | 생성 상태 폴링(본인 잡만, 남의 잡은 404) | ✅ |

**AI 문제 생성은 검색이 아니라 커버리지 방식이다.** 요청은 `{document_ids[], count, types[], difficulty?, exclude_duplicates, quiz_book_id?}` — 질의(topic)를 받지 않는다. 지정 문서의 청크를 `chunk_index` 순서대로 전부 훑어 연속 배치로 나눠 출제하므로 문서 앞·중간·뒤가 모두 다뤄지고, **임베딩이 없는 문서(`CHUNKED` 상태)에서도 동작한다**(RAG 검색에는 안 보이는 문서들 — `rag_top_k` 밖 내용이 아예 출제되지 않는 문제도 함께 피한다). `count`는 문서별 청크 수에 비례 배분되고, 청크가 있는 문서는 최소 1문항을 받는다.

생성된 문항은 `generation_type=AI`, `review_status=DRAFT`로 문제은행에 들어간다(검수 게이트는 없다 — DRAFT도 문제집에 편입 가능). `quiz_book_id`를 주면 생성 후 그 문제집에 순서대로 자동 편입되며, 그 문제집에 응시 이력이 있으면 `409`(아래 잠금 정책과 동일 규칙). 대상 문서에 청크가 없으면(파싱/청킹 전) 시작 시점에 `409`.

폴링 응답(`QuizGenerationJobRead`)의 `created_count`는 **배치마다 커밋되므로 RUNNING 중에도 올라간다** — 진행률로 쓸 수 있다. `status=COMPLETED`인데 `error`에 값이 있으면 **부분 성공** 안내다(요청 수보다 적게 만들어진 이유: 중복/형식 오류로 제외, 일부 배치 실패). 완료·실패 시 알림함 + Web Push로 알림이 간다.

문제집:

| Method | Endpoint | 결과 | 상태 |
|---|---|---|---|
| GET | `/api/v1/projects/{project_id}/quizzes` | 문제집 목록 | ✅ |
| POST | `/api/v1/projects/{project_id}/quizzes` | 문제집 생성 | ✅ |
| GET / PATCH / DELETE | `/api/v1/quizzes/{quiz_book_id}` | 상세 / 설정 수정 / 삭제 | ✅ |
| POST | `/api/v1/quizzes/{quiz_book_id}/duplicate` | 설정+문제 구성 복제(세션 없는 사본) | ✅ |
| GET / POST | `/api/v1/quizzes/{quiz_book_id}/questions` | 소속 문제 목록(순서대로) / 문제은행에서 편입 | ✅ |
| DELETE | `/api/v1/quizzes/{quiz_book_id}/questions/{question_id}` | 문제집에서 연결만 해제(문제은행엔 남음) | ✅ |

**응시 이력(세션)이 하나라도 있는 문제집은 위 PATCH/문제 추가·삭제와 AI 생성 자동 편입이 `409`로 막힌다** — 모든 응시자가 동일 버전을 보게 하기 위함. 수정하려면 `/duplicate`로 복제 후 사본을 편집한다. 프론트가 버튼을 미리 잠글 수 있도록 `QuizBookRead.has_sessions`(불리언)를 목록/상세 응답에 함께 내려준다 — `true`면 편집 버튼을 비활성화하고 "복제해서 편집"을 유도할 것. 단 조회 시점과 수정 시점 사이에 누군가 응시를 시작할 수 있으므로 409 처리도 남겨 둬야 한다. `DELETE /quizzes/{id}`(문제집 삭제)는 잠금과 무관하게 항상 허용된다(세션/답변도 cascade 삭제).

응시:

| Method | Endpoint | 결과 | 상태 |
|---|---|---|---|
| POST | `/api/v1/quizzes/{quiz_book_id}/sessions` | 응시 시작 `{mode: study\|exam}` → 정답 없는 문제 목록 + `session_id` | ✅ |
| POST | `/api/v1/quizzes/{quiz_book_id}/sessions/{session_id}/answers` | study 모드 즉시 채점 | ✅ |
| POST | `/api/v1/quizzes/{quiz_book_id}/sessions/{session_id}/submit` | 최종 제출 → 점수/정답수 집계 | ✅ |
| GET | `/api/v1/quizzes/{quiz_book_id}/sessions` | 문제집별 응시 이력(전체 사용자, 요약) | ✅ |
| GET | `/api/v1/quizzes/{quiz_book_id}/sessions/{session_id}` | 세션 상세(오답노트, 문항별 답변 포함) | ✅ |
| GET | `/api/v1/projects/{project_id}/quiz-sessions` | 내 응시 이력(문제집 전체 통틀어) | ✅ |

`quiz_answers`는 채점에 필요한 문제 내용(본문/보기/정답/해설)을 **제출 시점에 스냅샷**으로 저장한다 — 문제은행에서 문항이 나중에 수정/삭제돼도 과거 오답노트가 깨지지 않는다. `reveal_answers=false`인 문제집은 세션 결과에서도 `correct_answer`/`explanation`이 `null`로 내려간다. `shuffle_options`는 정답 index 매핑 문제를 피하려 **백엔드가 옵션 순서를 섞지 않는다** — 프론트가 표시만 섞고 항상 원본 인덱스로 제출한다(`shuffle_questions`, 즉 문항 순서만 서버가 섞는다).

---

## 7. Embedding Lab (관리자) — MVP2 (2026-08-25 백엔드 구현, 프론트 미연동)

모든 엔드포인트 `require_admin`(role="admin") 필요, 아니면 `403`. 응답은 프로젝트 전체 컨벤션대로 **snake_case** — `../frontend/src/lib/types.ts`의 `EmbeddingModel`/`BenchmarkRun` 타입은 이 백엔드가 생기기 전에 camelCase로 먼저 그려둔 목업이라 아직 안 맞다(후속 프론트 작업 필요).

| Method | Endpoint | 설명 | 상태 |
|---|---|---|---|
| GET | `/api/v1/embedding/models` | 등록된 임베딩 모델 목록 | ✅ |
| POST | `/api/v1/embedding/models/{id}/activate` | 이 모델을 활성 모델로 전환(청킹 설정은 불변) | ✅ |
| POST | `/api/v1/embedding/models/{id}/reembed` | 청킹은 그대로 두고 이 모델로 전체 `document_chunks` 재임베딩 → 202 `{job_id}` | ✅ |
| GET | `/api/v1/embedding/datasets` | 벤치마크 데이터셋 목록(질문 개수 포함) | ✅ |
| POST | `/api/v1/embedding/datasets` | 데이터셋 생성 — `{name, description?, project_id?, questions:[{question, category?, ground_truth:[{document_id, expected_snippet, page_start?}]}]}` | ✅ |
| GET | `/api/v1/embedding/benchmarks` | 벤치마크 run 목록(모델별 결과 포함) | ✅ |
| POST | `/api/v1/embedding/benchmarks/run` | `{dataset_id, model_ids[], chunking_config?:{strategy?, chunk_max_chars?, chunk_min_chars?, chunk_overlap_chars?, sentence_window?, breakpoint_percentile?}}` → 202 `{run_id}` | ✅ |
| GET | `/api/v1/embedding/benchmarks/{run_id}` | run 상세(진행 상황 폴링) | ✅ |
| POST | `/api/v1/embedding/benchmarks/{run_id}/apply` | `{model_id}` — 검증된 (청킹설정, 모델) 조합을 운영에 적용 → `{chunking_config_id, embedding_model_id}` | ✅ |
| POST | `/api/v1/embedding/migrate` | `{document_version_ids[], force_semantic?}` — 관리자가 고른 기존 버전을 재청킹+임베딩 → 202 `{job_id}` | ✅ |
| GET | `/api/v1/embedding/jobs/{job_id}` | reembed/migrate 작업 진행률 폴링 | ✅ |

- **벤치마크는 ephemeral** — 운영 `document_chunks`/`chunk_embeddings`는 절대 건드리지 않는다. `runner.py`가 대상 문서를 (모델×청킹설정 조합마다) 인메모리로 청킹+임베딩해서 Recall@1/3/5·MRR·NDCG·latency·throughput·RAM을 계산하고 버린다. 정답(`ground_truth`)은 `section_id` FK가 아니라 `{document_id, expected_snippet}` 스니펫이다 — 재파싱마다 섹션이 통째로 교체되므로 FK는 금방 깨지고, 스니펫이면 청킹 설정이 바뀌어도 "검색된 청크에 이 문구가 포함되는지"로 계속 채점 가능하다.
- **`apply`는 기존 문서를 건드리지 않는다** — `chunking_configs`/`embedding_models`의 활성값만 바꿔 "앞으로 신규 업로드/수동 마이그레이션이 참조할 기본값"을 정할 뿐이다. 기존에 이미 처리된 문서(특히 heading 없이 flat하게 청킹된 과거 문서)를 새 설정으로 다시 돌리려면 `/migrate`로 관리자가 대상 `document_version_id`를 직접 골라야 한다(자동 일괄 처리 없음). `force_semantic=true`면 heading 유무 자동 판정을 무시하고 semantic 청킹을 강제한다.
- **`chunking_configs`**는 `embedding_models.is_active`와 대칭인 별도 테이블 — 모델 활성화(`/activate`)가 청킹 설정까지 같이 바꾸는 부작용을 막기 위해 분리했다.
- **`chunk_min_chars`(2026-08-25 추가, 기본 300)** — structure 청킹의 **하한**. 같은 heading 아래 인접 본문 섹션을 이 길이까지 뭉친다(0이면 뭉치지 않음 = 구버전 동작). 이걸 넣기 전엔 섹션 1개 = 청크 1개라 회의록 불릿 한 줄(9자)이 검색 단위가 됐다 — 배경·실측치는 DESIGN.md §18 "Chunking 하한", 적용 범위는 [docs/STATUS.md](STATUS.md). `min_chars` 키가 없는 과거 run 스냅샷을 `apply`하면 그 run이 뭉치지 않고 채점됐다는 뜻이므로 `0`으로 되살린다.
- `POST /api/v1/rag/query`(§6)가 여기서 만든 `chunk_embeddings`(실 벡터)를 소비한다 — **2026-08-25 구현 완료.** Embedding Lab은 그 검색 인프라(모델·청킹 설정·재임베딩)를 준비하는 쪽이다.

---

## 8. PPT 지원 — 2026-08-19 로드맵 백로그 (⏳)

현재 PPTX는 업로드는 되지만(확장자 화이트리스트에 포함) 파서가 없어 `FAILED`로 끝난다. 목표는 다른 포맷과 동등하게 조회/교정 적용까지 지원하는 것 — 새 엔드포인트는 없고 기존 4절(문서)·5절(AI 모듈) 엔드포인트가 PPTX에도 그대로 동작하게 만드는 것이 범위.

- `app/services/parsing/`에 `PPTXParser` 추가(슬라이드=heading, 텍스트박스=body 매핑) → `document_sections`/`document_chunks`가 다른 포맷과 동일 스키마로 채워짐.
- `correction_service.py`에 PPTX 분기 추가(python-pptx 기반 텍스트 치환) → 교정 적용(`POST .../apply`)도 PPTX에서 동작.
- `requirements.txt`에 `python-pptx` 추가 필요(아직 없음).

---

## 9. 알림 (`/api/v1/notifications`) — 2026-08-20 구현

**결론**: `activity_logs` 위에 읽음 레이어를 얹는 대신 **수신자별 `notifications` 테이블을 따로** 뒀다 — `activity_logs`는 "프로젝트에서 무슨 일이 있었나"(감사 로그, 프로젝트 스코프)이고 알림은 "이 사용자가 봐야 할 것"(수신자 스코프 + 읽음 상태)이라 조회 축이 다르고, 활동 한 건이 여러 수신자에게 가거나(초대) 아무에게도 안 가는 경우가 섞이기 때문. 전달 채널은 **인앱(이 테이블) + Web Push(구독한 브라우저에만)** 둘 다이며 `NotificationService.notify()` 한 곳에서 동시에 처리한다(이메일은 미도입).

| Method | Endpoint | 결과 | 상태 |
|---|---|---|---|
| GET | `/api/v1/notifications?only_unread=&limit=&offset=` | 내 알림 목록(최신순) + 전체 미읽음 수 | ✅ (2026-08-20) |
| GET | `/api/v1/notifications/unread-count` | 미읽음 수만(뱃지 폴링용) | ✅ (2026-08-20) |
| POST | `/api/v1/notifications/{id}/read` | 읽음 처리(멱등), 응답은 갱신된 알림 | ✅ (2026-08-20) |
| POST | `/api/v1/notifications/read-all` | 전체 읽음 처리 | ✅ (2026-08-20) |
| GET | `/api/v1/push/public-key` | VAPID 공개키(브라우저 `PushManager.subscribe()`용) | ✅ |
| POST | `/api/v1/push/subscribe` | 브라우저 push 구독 등록(같은 endpoint 재구독은 upsert) | ✅ |
| DELETE | `/api/v1/push/subscribe` | 구독 해제 | ✅ |

- `NotificationRead` = `id, type, title, body, url, meta, read_at, created_at`. `read_at=null`이면 안 읽음. `url`은 클릭 시 이동할 **프론트** 경로(예: `/projects/12`, `/documents/34`), `meta`는 종류별 부가 정보(`project_id`/`document_id`/`analysis_id` 등).
- 목록 응답은 `{items, unread}` — `unread`는 `only_unread`/`limit`과 무관한 전체 미읽음 수라 벨 아이콘 뱃지에 그대로 쓴다.
- 남의 알림 id로 읽음 처리하면 존재를 감추기 위해 **403이 아니라 404**.
- `type` 값(계약): `project.invite`, `project.invite_accepted`, `project.invite_declined`, `project.member_removed`, `analysis.complete`, `analysis.fail`. 초대 취소는 알림을 보내지 않는다(수락 전이므로).
- **Web Push payload(서비스워커 계약)**: `{"title": string, "body": string, "url": string|null, "type": string|null}` 4개 키 고정. `type`은 `NotificationRead.type`과 같은 값이라 서비스워커가 `showNotification(..., {tag})`으로 같은 종류를 묶거나 아이콘을 바꾸는 데 쓸 수 있다. VAPID 키가 서버에 없으면 push는 조용히 건너뛰고 인앱 알림만 쌓인다(`GET /push/public-key`가 빈 문자열을 돌려주므로 프론트는 토글을 비활성화할 수 있다).
- 알림 생성은 **본 업무를 깨뜨리지 않는다** — 저장/푸시 실패는 로깅만 하고 초대·분석 자체는 성공한다(`ActivityService.record()`와 같은 원칙).
- 기존 오탈자 검증 완료/실패 push도 이제 이 서비스를 거쳐 **알림함에 기록이 남는다**(이전에는 push만 쏘고 흔적이 없었음).

---

## 10. 문서 정리 / 즐겨찾기 / 메모 / Notion 연동 — 2026-08-19 로드맵 백로그 (⏳, 엔드포인트 미확정)

- **즐겨찾기**: `user_id, document_id` 조인 테이블로 충분, Document Core 스키마 변경 불필요.
- **메모**: 문서 단위 vs 섹션 단위 결정 필요 — 섹션 단위면 `document_notes(section_id?, document_id, user_id, content)` 신규 테이블.
- **Notion 연동**: 외부 유입 소스(§ DESIGN.md §26 OneDrive와 같은 성격)로 볼지, 우리 문서를 Notion으로 내보내는 export로 볼지 방향이 정반대 — 착수 전 결정 필요.

| Method | Endpoint | 결과 | 상태 |
|---|---|---|---|
| POST | `/api/v1/documents/{doc_id}/favorite` | 즐겨찾기 토글 | ⏳ |
| GET | `/api/v1/favorites` | 내 즐겨찾기 목록 | ⏳ |
| GET/POST | `/api/v1/documents/{doc_id}/notes` | 메모 조회/작성 | ⏳ |

---

## 11. MCP 서버 — 2026-08-19 로드맵 백로그 (⏳, REST 엔드포인트 아님)

Document Core(검색·섹션·교정 결과 등)를 이 백엔드 밖의 다른 서비스/에이전트가 표준 프로토콜(MCP)로 재사용하도록 tool로 노출하는 것이 목적. **선행 조건**: 노출할 실제 기능(특히 RAG 검색)이 먼저 구현돼 있어야 함 → MVP2 이후, 그리고 실제 외부 소비자 요구가 확인된 시점에 착수. 우리 자신의 RAG/챗봇이 우리 DB를 쓰는 것 자체는 OpenAI function calling으로 충분해 MCP가 불필요하다 — MCP는 외부 팀의 에이전트가 커스텀 연동 코드 없이 붙어야 하는 실제 수요가 생겼을 때만 값어치가 있다(상세 설계 방향은 DESIGN.md §27-3).

후보 tool(방향성 메모, 미확정):
- `search_chunks(query, project_id)` — pgvector 벡터 검색 (RAGService 완성 후)
- `get_document_sections(document_id)` — 파싱된 섹션 트리 조회
- `get_proofread_findings(document_id)` — 최근 교정 결과 조회
- `list_documents(project_id, filters)` — 문서 메타데이터 조회

---

## 12. 공통 / 기타

| Method | Endpoint | 용도 | 상태 |
|---|---|---|---|
| GET | `/api/v1/health` | 헬스체크(DB 핑 포함) | ✅ |
| GET | `/api/v1/search?q=` | Topbar 전역 빠른검색(문서·프로젝트 키워드) | ⏳ |

- 백엔드 OpenAPI: `/openapi.json` (+ Swagger `/docs`) 자동 제공 → 프론트 타입 대조에 활용.
