from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.dashboard import ActivityItem, DashboardSummary
from app.services.dashboard_service import DashboardService, get_dashboard_service
from app.services.project_service import ProjectNotFoundError

# /dashboard/summary 와 /activity 는 경로가 갈리므로 라우터 prefix 를 두지 않는다.
router = APIRouter(tags=["dashboard"])

ProjectScopeQuery = Annotated[
    int | None,
    Query(
        description="지정 시 해당 프로젝트만 집계. 생략하면 전체. 없는 id 면 404",
        examples=[1],
    ),
]

AUTH_RESPONSES = {401: {"description": "토큰 없음/만료/무효"}}
SCOPE_RESPONSES = {
    **AUTH_RESPONSES,
    404: {"description": "project_id 로 지정한 프로젝트 없음"},
}


def _not_found(project_id: int | None) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"Project {project_id} not found"
    )


@router.get(
    "/dashboard/summary",
    response_model=DashboardSummary,
    summary="대시보드 집계",
    response_description="스탯 4종 + 주간 처리량 + 문서 유형 분포 + 파이프라인 카운트",
    responses=SCOPE_RESPONSES,
)
def get_dashboard_summary(
    project_id: ProjectScopeQuery = None,
    service: DashboardService = Depends(get_dashboard_service),
    _: User = Depends(get_current_user),
) -> DashboardSummary:
    """홈 대시보드가 한 번에 필요로 하는 숫자를 모아 준다.

    **이 응답만 camelCase** 다(`ragToday`, `weeklyProcessing`, `documentTypes`) —
    프론트가 먼저 확정해 둔 계약에 맞춘 것으로, 다른 API 는 snake_case 그대로다.

    필드별 의미:

    - `stats.processing` — 진행 중인 문서 수. `PARSING`/`CHUNKING`/`EMBEDDING` 만 센다
      (큐 대기 상태인 `UPLOADED` 와 종착 상태는 제외)
    - `stats.ragToday` — **항상 0.** RAG 가 미구현(MVP2)이라 셀 소스가 없다
    - `weeklyProcessing` — 오늘 포함 최근 7일 업로드 건수를 과거 → 오늘 순으로 담은 길이 7 배열.
      요일 고정이 아니므로 축 라벨은 클라이언트가 오늘 날짜로 계산해야 한다. 날짜 경계는 UTC
    - `documentTypes` — `document_type` 별 문서 수(많은 순). 타입이 없으면 `기타` 로 묶인다
    - `pipeline` — 처리 상태별 문서 수. 카운트가 0 이어도 8개 상태를 **항상 고정 순서로**
      내려주므로 프로그레스 바 칸이 흔들리지 않는다. 집계 기준은 문서 생명주기
      (`documents.status`)가 아니라 **현재 버전의 처리 상태**다

    `project_id` 에 없는 id 를 주면 빈 집계가 아니라 **404** 다 — `/documents/recent`,
    `/activity` 와 동작을 맞춘 것. 오타 난 id 가 "데이터 없음"으로 보이면 안 되기 때문.
    """
    try:
        return service.get_summary(project_id=project_id)
    except ProjectNotFoundError:
        raise _not_found(project_id) from None


@router.get(
    "/activity",
    response_model=list[ActivityItem],
    summary="최근 활동 피드",
    response_description="최신순 활동 목록",
    responses=SCOPE_RESPONSES,
)
def list_activity(
    limit: Annotated[
        int, Query(ge=1, le=100, description="가져올 최대 건수")
    ] = 10,
    project_id: ProjectScopeQuery = None,
    action: Annotated[
        str | None,
        Query(
            description=(
                "액션 코드로 정확히 거른다. `auth.login` · `project.create|update|delete` · "
                "`folder.create|update|delete` · `document.upload|delete` · "
                "`analysis.start|complete|fail`"
            ),
            examples=["document.upload"],
        ),
    ] = None,
    service: DashboardService = Depends(get_dashboard_service),
    _: User = Depends(get_current_user),
) -> list[ActivityItem]:
    """누가·언제·무엇을 했는지 `activity_logs` 에서 최신순으로 읽어 온다.

    DB 에는 액션 **코드**만 쌓고, 사람이 읽는 문구(`action`)와 배지 색 구분용
    `kind` 는 응답을 만들 때 붙인다. 그래서 문구를 바꿔도 과거 로그는 안 깨진다.

    `kind` 값은 `업로드`/`삭제`/`프로젝트`/`폴더`/`로그인`/`오타`/`규정`/`분류`/`추천` 중 하나다.
    `at` 은 ISO8601 시각 — "10분 전" 같은 상대 표기는 클라이언트가 만든다.
    `target` 은 **기록 당시의 이름**이라, 그 뒤 대상이 삭제·개명돼도 그대로 남는다.

    **링크를 거는 규칙** — 반드시 세 조건을 모두 본다:

    ```
    targetType === "document" && targetId != null && targetAlive
    ```

    - `targetType` 없이 `targetId` 만 쓰면 안 된다. id 가 테이블별 시퀀스라 project id 4 로
      `/documents/4` 를 열면 **404 도 안 나고 엉뚱한 문서가 열린다.**
    - `targetAlive` 없이 "삭제 액션만 제외"로는 부족하다. 로그는 FK 가 없어 대상보다 오래
      살아남으므로, 업로드 로그도 그 문서가 나중에 지워졌으면 404 가 된다.
    - `targetAlive` 는 **응답 생성 시점의 스냅샷**이다. 사용자가 몇 초 뒤 클릭하는 사이
      지워질 수 있으니 클라이언트는 여전히 404 를 처리해야 한다.

    로그 테이블 도입 이전(2026-08-16)의 활동은 남아 있지 않다. RAG 질의도 아직 없다.
    """
    try:
        return service.get_activity(limit=limit, project_id=project_id, action=action)
    except ProjectNotFoundError:
        raise _not_found(project_id) from None
