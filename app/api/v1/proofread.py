from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
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


@router.post(
    "/documents/{document_id}/proofread",
    response_model=ProofreadResult,
    status_code=status.HTTP_202_ACCEPTED,
)
def proofread_document(
    document_id: int,
    background_tasks: BackgroundTasks,
    service: ProofreadService = Depends(get_proofread_service),
    current_user: User = Depends(get_current_user),
) -> ProofreadResult:
    """교정을 시작하고 202 + RUNNING 레코드를 반환한다.

    실제 GPT 배치 호출은 백그라운드에서 수행되며, 프론트는
    GET /documents/{id}/analyses/{analysis_id} 로 status 를 폴링한다.
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


@router.get("/documents/{document_id}/analyses", response_model=list[AnalysisSummary])
def list_analyses(
    document_id: int,
    type: str | None = Query(default=None),
    service: ProofreadService = Depends(get_proofread_service),
    _: User = Depends(get_current_user),
) -> list[AnalysisSummary]:
    try:
        return service.list_analyses(document_id, analysis_type=type)
    except DocumentNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Document {document_id} not found"
        ) from None


@router.get(
    "/documents/{document_id}/analyses/{analysis_id}",
    response_model=ProofreadResult,
)
def get_analysis(
    document_id: int,
    analysis_id: int,
    service: ProofreadService = Depends(get_proofread_service),
    _: User = Depends(get_current_user),
) -> ProofreadResult:
    try:
        return service.get_analysis(document_id, analysis_id)
    except AnalysisNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Analysis {analysis_id} not found"
        ) from None
