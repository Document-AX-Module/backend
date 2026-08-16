from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Path,
    Query,
    status,
)

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.proofread import AnalysisSummary, ProofreadResult
from app.services.document_service import DocumentNotFoundError, VersionNotFoundError
from app.services.proofread_service import (
    AnalysisNotFoundError,
    DocumentNotReadyError,
    ProofreadService,
    build_result,
    get_proofread_service,
    run_proofread_task,
)

router = APIRouter(tags=["ai-proofread"])

DocumentIdPath = Annotated[int, Path(description="문서 id", examples=[1])]
AnalysisIdPath = Annotated[
    int,
    Path(description="분석 id — 교정 시작 응답의 `id`", examples=[1]),
]

AUTH_RESPONSES = {401: {"description": "토큰 없음/만료/무효"}}


@router.post(
    "/documents/{document_id}/proofread",
    response_model=ProofreadResult,
    status_code=status.HTTP_202_ACCEPTED,
    summary="오탈자 검증 시작 (비동기)",
    response_description="`status=\"RUNNING\"`, `findings=[]` 인 분석 레코드(202)",
    responses={
        **AUTH_RESPONSES,
        404: {"description": "문서가 없거나 저장된 버전이 없음"},
        409: {"description": "아직 파싱이 끝나지 않아 검사할 섹션이 없음"},
    },
)
def proofread_document(
    document_id: DocumentIdPath,
    background_tasks: BackgroundTasks,
    service: ProofreadService = Depends(get_proofread_service),
    current_user: User = Depends(get_current_user),
) -> ProofreadResult:
    """교정을 시작하고 **즉시 202 + RUNNING 레코드**를 반환한다.

    실제 GPT 호출은 백그라운드에서 진행되므로 이 응답의 `findings` 는 항상 비어 있다.
    응답의 `id` 를 들고 `GET /documents/{document_id}/analyses/{analysis_id}` 를 폴링해
    `status` 가 `COMPLETED`(또는 `FAILED`)가 될 때까지 기다려야 결과를 볼 수 있다.

    현재 버전의 파싱된 섹션을 묶어 한 번의 프롬프트로 보낸다(배치). 그래서 섹션이 많아도
    호출 횟수가 선형으로 늘지 않는다. 문서가 크면 여러 배치로 나뉜다.

    `OPENAI_API_KEY` 가 있으면 실제 GPT 를, 없으면 규칙 기반 mock provider 를 쓴다
    (어느 쪽인지는 결과의 `provider` 필드로 확인).
    """
    try:
        record = service.create_pending(document_id, created_by=str(current_user.id))
    except DocumentNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Document {document_id} not found"
        ) from None
    except VersionNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No stored version for this document"
        ) from None
    except DocumentNotReadyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None

    background_tasks.add_task(run_proofread_task, record.id)
    return build_result(record, [], 0)


@router.get(
    "/documents/{document_id}/analyses",
    response_model=list[AnalysisSummary],
    summary="AI 분석 이력",
    response_description="최신순 메타 목록(결과 본문은 빠져 있다)",
    responses={**AUTH_RESPONSES, 404: {"description": "문서 없음"}},
)
def list_analyses(
    document_id: DocumentIdPath,
    type: Annotated[
        str | None,
        Query(
            description=(
                "분석 종류로 거른다. 지금 실제로 쌓이는 값은 `proofread` 뿐이고 "
                "`classify`/`validate`/`related` 는 예약된 값이다."
            ),
            examples=["proofread"],
        ),
    ] = None,
    service: ProofreadService = Depends(get_proofread_service),
    _: User = Depends(get_current_user),
) -> list[AnalysisSummary]:
    """이 문서에 대해 실행된 AI 분석 목록을 최신순으로 돌려준다.

    `findings` 같은 결과 본문은 빠진 **메타 정보만** 온다 — 상세는 개별 조회로.
    모든 AI 모듈이 같은 `ai_analysis_results` 테이블을 쓰므로 이 엔드포인트는
    교정 외 모듈이 붙어도 그대로 쓰인다.
    """
    try:
        return service.list_analyses(document_id, analysis_type=type)
    except DocumentNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Document {document_id} not found"
        ) from None


@router.get(
    "/documents/{document_id}/analyses/{analysis_id}",
    response_model=ProofreadResult,
    summary="분석 결과 상세 (폴링용)",
    response_description="`status` + 완료 시 `findings`",
    responses={**AUTH_RESPONSES, 404: {"description": "해당 분석 없음"}},
)
def get_analysis(
    document_id: DocumentIdPath,
    analysis_id: AnalysisIdPath,
    service: ProofreadService = Depends(get_proofread_service),
    _: User = Depends(get_current_user),
) -> ProofreadResult:
    """분석 결과를 조회한다. 교정 시작 후 이걸 폴링해 완료를 감지한다.

    - `status="RUNNING"` — 아직 처리 중. `findings` 는 비어 있다
    - `status="COMPLETED"` — `findings` 에 지적 사항이 담긴다. 문제가 없으면 빈 배열
    - `status="FAILED"` — `error` 에 사유가 담긴다

    각 finding 은 `original`(원문) → `suggestion`(수정안)에 `reason`(이유),
    `category`(spelling·spacing·grammar·expression), 그리고 본문에서 위치를 찾을 수 있게
    `section_id` 와 페이지 범위를 함께 준다.
    """
    try:
        return service.get_analysis(document_id, analysis_id)
    except AnalysisNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Analysis {analysis_id} not found"
        ) from None
