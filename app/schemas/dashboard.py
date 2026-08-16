from datetime import datetime

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class _CamelModel(BaseModel):
    """대시보드 응답 전용 베이스.

    다른 스키마는 snake_case 지만, 대시보드는 프론트가 이미 확정해 둔 계약
    (`frontend/src/lib/types.ts` 의 DashboardSummary/ActivityItem)이 camelCase 라
    직렬화 시에만 camelCase 로 내보낸다. 프론트를 고치지 않고 붙일 수 있게 하기 위함.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class DashboardStats(_CamelModel):
    projects: int
    documents: int
    processing: int
    rag_today: int


class DocumentTypeCount(_CamelModel):
    label: str
    value: int


class PipelineStage(_CamelModel):
    # 프론트 DocStatus 와 동일한 값(UPLOADED/PARSING/.../FAILED). 라벨링은 프론트 담당.
    status: str
    count: int


class DashboardSummary(_CamelModel):
    stats: DashboardStats
    # 최근 7일치 처리 건수, 과거 → 오늘 순(마지막 원소가 오늘).
    weekly_processing: list[int]
    document_types: list[DocumentTypeCount]
    pipeline: list[PipelineStage]


class ActivityItem(_CamelModel):
    actor: str
    action: str
    target: str
    kind: str
    at: datetime
    # 활동 대상. 링크를 걸 때 쓴다 — targetType 으로 경로를 고르고 targetId 를 끼운다.
    # "document" | "project" | "folder", 로그인처럼 대상이 없으면 둘 다 null.
    #
    # ⚠️ target_id 는 테이블별 시퀀스라 **target_type 없이 쓰면 안 된다** —
    # project id 4 로 /documents/4 를 열면 404 도 안 나고 엉뚱한 문서가 열린다.
    #
    # 주의: 이 섹션은 camelCase 직렬화라 응답 키는 `targetType`/`targetId`/`targetAlive` 다.
    target_type: str | None = None
    target_id: int | None = None
    # 대상 행이 **응답 생성 시점에** 아직 존재하는지. 로그는 FK 가 없어 대상보다 오래
    # 살아남으므로(삭제된 문서를 가리키는 업로드 로그 등), 링크를 걸기 전 이걸 확인한다.
    # 대상이 없는 활동(로그인)은 false.
    target_alive: bool = False
