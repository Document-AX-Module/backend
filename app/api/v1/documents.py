from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.document import (
    ChunkRead,
    DocumentRead,
    DocumentUploadResponse,
    DocumentVersionRead,
    ProcessingStatus,
    RecentDocumentRead,
    SectionRead,
    VersionStatusResponse,
)
from app.services.activity_service import (
    Action,
    ActivityService,
    get_activity_service,
)
from app.services.document_service import (
    DocumentNotFoundError,
    DocumentService,
    DocumentValidationError,
    VersionNotFoundError,
    get_document_service,
    status_progress,
)
from app.services.parsing_service import run_parse_task
from app.services.project_service import ProjectNotFoundError

router = APIRouter(tags=["documents"])

ProjectIdPath = Annotated[int, Path(description="프로젝트 id", examples=[1])]
DocumentIdPath = Annotated[int, Path(description="문서 id", examples=[1])]
VersionIdPath = Annotated[
    int,
    Path(
        description="문서 버전 id (문서 id 가 아니다 — `current_version.id` 를 쓸 것)",
        examples=[1],
    ),
]

AUTH_RESPONSES = {401: {"description": "토큰 없음/만료/무효"}}


def _not_found(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)


@router.get(
    "/projects/{project_id}/documents",
    response_model=list[DocumentRead],
    summary="문서 목록",
    response_description="최근 등록순 문서 목록(작성자·현재 버전 포함)",
    responses={**AUTH_RESPONSES, 404: {"description": "프로젝트 없음"}},
)
def list_documents(
    project_id: ProjectIdPath,
    folder_id: Annotated[
        int | None,
        Query(description="지정 시 해당 폴더 직속 문서만. 생략하면 프로젝트 전체(하위 폴더 포함)"),
    ] = None,
    status_filter: Annotated[
        ProcessingStatus | None,
        Query(
            alias="status",
            description=(
                "**현재 버전의 처리 상태**로 거른다(문서 생명주기 `documents.status` 가 아님). "
                "현재 버전이 없는 문서는 제외된다."
            ),
        ),
    ] = None,
    type: Annotated[
        str | None,
        Query(description="`document_type` 정확 일치. 예: `pdf`, `RULE`", examples=["pdf"]),
    ] = None,
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> list[DocumentRead]:
    """프로젝트의 문서를 조회한다. 세 필터는 AND 로 결합된다.

    각 항목에는 `created_by`(사용자 id)를 풀어 쓴 `author`(이름·이메일)와
    `current_version`(파일명·크기·처리 상태)이 함께 채워져 나온다.
    """
    try:
        return service.list_document_reads(
            project_id,
            folder_id=folder_id,
            status=status_filter.value if status_filter else None,
            document_type=type,
        )
    except ProjectNotFoundError:
        raise _not_found(f"Project {project_id} not found") from None


@router.post(
    "/projects/{project_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="문서 업로드 (멀티파트)",
    response_description="생성된 문서 + v1 버전(202). 파싱은 아직 진행 중이다",
    responses={
        **AUTH_RESPONSES,
        400: {"description": "지원하지 않는 확장자, 용량 초과, 또는 folder_id 가 잘못됨"},
        404: {"description": "프로젝트 없음"},
    },
)
async def upload_document(
    project_id: ProjectIdPath,
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File(description="업로드할 원본 파일")],
    folder_id: Annotated[
        int | None, Form(description="넣을 폴더 id. 생략하면 프로젝트 최상위")
    ] = None,
    name: Annotated[
        str | None, Form(description="표시용 문서 이름. 생략하면 업로드 파일명을 쓴다")
    ] = None,
    description: Annotated[str | None, Form(description="문서 설명(선택)")] = None,
    service: DocumentService = Depends(get_document_service),
    activity: ActivityService = Depends(get_activity_service),
    current_user: User = Depends(get_current_user),
) -> DocumentUploadResponse:
    """파일을 저장하고 문서 + v1 버전을 만든 뒤, **즉시 202 를 반환한다.**

    응답을 보낸 뒤 백그라운드에서 파싱 → 청킹이 이어진다
    (`UPLOADED → PARSING → PARSED → CHUNKING → CHUNKED`). 따라서 응답의
    `version.processing_status` 는 거의 항상 `UPLOADED` 이고, 완료 여부는
    `GET /versions/{version_id}/status` 를 폴링해서 확인해야 한다.

    파서가 있는 형식은 **txt · md · docx · pdf · hwpx** 다. 그 외(hwp/xlsx/pptx)는
    업로드 자체는 되지만 파싱 단계에서 `FAILED` 가 된다.
    스캔 PDF 는 네이티브 텍스트가 없으면 Tesseract OCR(kor+eng)로 넘어간다.
    """
    data = await file.read()
    try:
        document, version = service.upload_document(
            project_id,
            folder_id=folder_id,
            name=name,
            description=description,
            filename=file.filename or "unnamed",
            content_type=file.content_type,
            data=data,
            created_by=str(current_user.id),
        )
    except ProjectNotFoundError:
        raise _not_found(f"Project {project_id} not found") from None
    except DocumentValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None

    activity.record(
        Action.DOCUMENT_UPLOAD,
        project_id=project_id,
        target_type="document",
        target_id=document.id,
        target_label=version.original_file_name,
        meta={"version_no": version.version_no, "file_size": version.file_size},
    )

    background_tasks.add_task(run_parse_task, version.id)
    return DocumentUploadResponse(document=document, version=version)


# ⚠️ 이 라우트는 반드시 "/documents/{document_id}" 보다 **먼저** 등록돼야 한다.
# 순서가 뒤집히면 "recent" 가 document_id 로 매칭돼 int 파싱에 실패한다(422).
@router.get(
    "/documents/recent",
    response_model=list[RecentDocumentRead],
    summary="최근 문서 (프로젝트 교차)",
    response_description="등록 최신순. 프로젝트 이름과 현재 처리 상태가 함께 온다",
    responses={**AUTH_RESPONSES, 404: {"description": "project_id 로 지정한 프로젝트 없음"}},
)
def list_recent_documents(
    limit: Annotated[int, Query(ge=1, le=50, description="가져올 최대 건수")] = 5,
    project_id: Annotated[
        int | None,
        Query(description="지정 시 해당 프로젝트만. 생략하면 전체 프로젝트를 가로질러 조회"),
    ] = None,
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> list[RecentDocumentRead]:
    """대시보드 "최근 문서" 카드용 — 전체 프로젝트에서 최근 등록된 문서를 모아 준다.

    `/projects/{project_id}/documents` 와 달리 **프로젝트를 가로지른다.** 카드에서
    프로젝트 경로와 상태 배지를 바로 그릴 수 있도록 `project_name` 과
    현재 버전의 `processing_status` 를 펼쳐서 담는다(추가 요청 불필요).

    `author` 나 버전 상세는 담지 않는다 — 필요하면 `/documents/{document_id}` 를 볼 것.
    """
    try:
        return service.list_recent_documents(limit=limit, project_id=project_id)
    except ProjectNotFoundError:
        raise _not_found(f"Project {project_id} not found") from None


@router.get(
    "/documents/{document_id}",
    response_model=DocumentRead,
    summary="문서 상세",
    response_description="문서 + `author` + `current_version`",
    responses={**AUTH_RESPONSES, 404: {"description": "문서 없음"}},
)
def get_document(
    document_id: DocumentIdPath,
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> DocumentRead:
    """문서 하나를 조회한다. 목록과 동일하게 작성자·현재 버전이 채워진다.

    섹션/청크 같은 본문 내용은 포함되지 않는다 — `current_version.id` 로
    `/versions/{version_id}/sections` 를 따로 부를 것.
    """
    try:
        return service.get_document_read(document_id)
    except DocumentNotFoundError:
        raise _not_found(f"Document {document_id} not found") from None


@router.get(
    "/documents/{document_id}/versions",
    response_model=list[DocumentVersionRead],
    summary="버전 목록",
    response_description="version_no 내림차순(최신 우선)",
    responses={**AUTH_RESPONSES, 404: {"description": "문서 없음"}},
)
def list_versions(
    document_id: DocumentIdPath,
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> list[DocumentVersionRead]:
    """문서의 모든 버전을 최신순으로 돌려준다.

    새 버전(v2+) 업로드가 아직 미구현이라 **현재는 항상 1건**이다.
    """
    try:
        return service.list_versions(document_id)
    except DocumentNotFoundError:
        raise _not_found(f"Document {document_id} not found") from None


@router.get(
    "/documents/{document_id}/download",
    summary="원본 파일 다운로드",
    response_description="업로드 당시 파일명·MIME 으로 내려주는 원본 바이트",
    responses={
        **AUTH_RESPONSES,
        404: {"description": "문서가 없거나 저장된 파일이 없음"},
    },
)
def download_document(
    document_id: DocumentIdPath,
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> FileResponse:
    """**현재 버전**의 원본 파일을 그대로 내려준다. 파싱 결과가 아니라 업로드한 바이트다."""
    try:
        version, path = service.resolve_download(document_id)
    except DocumentNotFoundError:
        raise _not_found(f"Document {document_id} not found") from None
    except VersionNotFoundError:
        raise _not_found("No stored file for this document") from None
    return FileResponse(
        path,
        filename=version.original_file_name,
        media_type=version.mime_type or "application/octet-stream",
    )


@router.get(
    "/versions/{version_id}",
    response_model=DocumentVersionRead,
    summary="버전 상세",
    responses={**AUTH_RESPONSES, 404: {"description": "버전 없음"}},
)
def get_version(
    version_id: VersionIdPath,
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> DocumentVersionRead:
    """버전 하나의 메타데이터(파일명·MIME·크기·sha256 체크섬·처리 상태)를 조회한다."""
    try:
        return service.get_version(version_id)
    except VersionNotFoundError:
        raise _not_found(f"Version {version_id} not found") from None


@router.get(
    "/versions/{version_id}/status",
    response_model=VersionStatusResponse,
    summary="처리 상태 폴링",
    response_description="`{version_id, status, progress}` — progress 는 0~100",
    responses={**AUTH_RESPONSES, 404: {"description": "버전 없음"}},
)
def get_version_status(
    version_id: VersionIdPath,
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> VersionStatusResponse:
    """업로드 후 파싱·청킹 진행 상황을 확인한다. 업로드 화면이 주기적으로 호출하는 용도.

    `progress` 는 상태에 매긴 고정 백분율이라 단계 안에서 연속적으로 오르지 않는다
    (UPLOADED 5 · PARSING 25 · PARSED 45 · CHUNKING 65 · CHUNKED 75 ·
    EMBEDDING 85 · READY 100 · FAILED 0).

    임베딩이 미구현이라 **정상 종료는 `CHUNKED`(75%)** 다. 100% 를 기다리면 무한 폴링이 된다.
    실패 사유는 이 응답에 담기지 않는다.
    """
    try:
        version = service.get_version(version_id)
    except VersionNotFoundError:
        raise _not_found(f"Version {version_id} not found") from None
    return VersionStatusResponse(
        version_id=version.id,
        status=version.processing_status,
        progress=status_progress(version.processing_status),
    )


@router.get(
    "/versions/{version_id}/sections",
    response_model=list[SectionRead],
    summary="파싱된 섹션 목록",
    response_description="`order_no` 오름차순(문서 등장 순서)",
    responses={**AUTH_RESPONSES, 404: {"description": "버전 없음"}},
)
def list_sections(
    version_id: VersionIdPath,
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> list[SectionRead]:
    """파서가 만든 섹션을 문서 순서대로 돌려준다. 파싱 전이면 빈 배열이다.

    **필드 규칙** — 형식(PDF/DOCX/HWPX/TXT)과 무관하게 동일하다:

    - `section_type="heading"` 이면 `title` 에만 값이 있고 `content` 는 비어 있다(중복 표시 방지)
    - 본문·표는 반대로 `content` 에만 값이 있다
    - `level` 은 제목 깊이 1~9, 본문·표는 100 — 접이식 트리를 그릴 때 쓴다
    - `parent_section_id` 로 계층이 이어지고, `meta` 에는 페이지·bbox 같은 원본 좌표가 들어간다
    """
    try:
        return service.list_sections(version_id)
    except VersionNotFoundError:
        raise _not_found(f"Version {version_id} not found") from None


@router.get(
    "/versions/{version_id}/chunks",
    response_model=list[ChunkRead],
    summary="청크 목록",
    response_description="`chunk_index` 오름차순",
    responses={**AUTH_RESPONSES, 404: {"description": "버전 없음"}},
)
def list_chunks(
    version_id: VersionIdPath,
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> list[ChunkRead]:
    """임베딩·검색의 최소 단위인 청크를 돌려준다. 청킹 전이면 빈 배열이다.

    구조 기반으로 자른다 — 제목 섹션 자체는 청크가 되지 않고, 대신 각 청크의
    `metadata.heading_path` 에 상위 제목 경로가 붙는다. 섹션이 설정된 최대 길이를
    넘을 때만 문장 단위로 쪼개며 앞뒤를 조금 겹친다.

    아직 임베딩 단계가 없어 청크는 만들어져 있어도 벡터는 비어 있다.
    """
    try:
        return service.list_chunks(version_id)
    except VersionNotFoundError:
        raise _not_found(f"Version {version_id} not found") from None


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="문서 삭제",
    response_description="본문 없음(204)",
    responses={**AUTH_RESPONSES, 404: {"description": "문서 없음"}},
)
def delete_document(
    document_id: DocumentIdPath,
    service: DocumentService = Depends(get_document_service),
    activity: ActivityService = Depends(get_activity_service),
    _: User = Depends(get_current_user),
) -> None:
    """문서를 삭제한다. 되돌릴 수 없다.

    모든 버전·섹션·청크·AI 분석 결과가 CASCADE 로 함께 지워지고,
    저장소의 원본 파일도 정리된다.
    """
    try:
        # 지우고 나면 읽을 수 없으므로 로그용 정보를 먼저 확보한다.
        doc = service.get_document(document_id)
        project_id, name = doc.project_id, doc.name
        service.delete_document(document_id)
        activity.record(
            Action.DOCUMENT_DELETE,
            project_id=project_id,
            target_type="document",
            target_id=document_id,
            target_label=name,
        )
    except DocumentNotFoundError:
        raise _not_found(f"Document {document_id} not found") from None
