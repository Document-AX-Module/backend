from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta, timezone

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories.activity_repository import ActivityLogRepository
from app.repositories.dashboard_repository import DashboardRepository
from app.repositories.project_repository import ProjectRepository
from app.schemas.dashboard import (
    ActivityItem,
    DashboardStats,
    DashboardSummary,
    DocumentTypeCount,
    PipelineStage,
)
from app.schemas.document import ProcessingStatus
from app.services.activity_service import describe
from app.services.project_service import ProjectNotFoundError

# 카운트가 0 이어도 항상 내려보내는 파이프라인 단계 순서(프론트 DocStatus 와 동일).
PIPELINE_ORDER = tuple(s.value for s in ProcessingStatus)

# stats.processing("처리 중")으로 집계할 상태 — 큐 대기(UPLOADED)와 종착 상태는 제외.
IN_FLIGHT_STATUSES = frozenset({"PARSING", "CHUNKING", "EMBEDDING"})

WEEK_DAYS = 7
UNKNOWN_TYPE_LABEL = "기타"
UNKNOWN_ACTOR = "알 수 없음"


class DashboardService:
    def __init__(
        self,
        repository: DashboardRepository,
        activity_logs: ActivityLogRepository,
        projects: ProjectRepository,
    ) -> None:
        self.repository = repository
        self.activity_logs = activity_logs
        self.projects = projects

    def _ensure_project(self, project_id: int | None) -> None:
        """없는 project_id 는 404 로 끊는다.

        조용히 빈 집계를 돌려주면 프론트가 오타 난 id 를 "데이터 없음"으로 오인한다.
        `/documents/recent` 와 동작을 맞춘 것.
        """
        if project_id is not None and self.projects.get(project_id) is None:
            raise ProjectNotFoundError(project_id)

    # ── GET /dashboard/summary ──

    def get_summary(self, *, project_id: int | None = None) -> DashboardSummary:
        self._ensure_project(project_id)
        pipeline = dict(self.repository.pipeline_counts(project_id))
        return DashboardSummary(
            stats=DashboardStats(
                projects=self.repository.count_projects(project_id),
                documents=self.repository.count_documents(project_id),
                processing=sum(
                    count
                    for status, count in pipeline.items()
                    if status in IN_FLIGHT_STATUSES
                ),
                # RAG 는 아직 미구현(MVP2/Phase 9). 집계할 소스가 없어 0 고정.
                rag_today=0,
            ),
            weekly_processing=self._weekly_processing(project_id),
            document_types=[
                DocumentTypeCount(label=doc_type or UNKNOWN_TYPE_LABEL, value=count)
                for doc_type, count in self.repository.document_type_counts(project_id)
            ],
            pipeline=self._pipeline_stages(pipeline),
        )

    def _weekly_processing(self, project_id: int | None) -> list[int]:
        """최근 7일(오늘 포함) 업로드 건수를 과거 → 오늘 순으로."""
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=WEEK_DAYS - 1)
        counts = self.repository.daily_version_counts(
            datetime.combine(start, time.min, tzinfo=timezone.utc), project_id
        )
        return [counts.get(start + timedelta(days=i), 0) for i in range(WEEK_DAYS)]

    @staticmethod
    def _pipeline_stages(counts: dict[str, int]) -> list[PipelineStage]:
        stages = [
            PipelineStage(status=status, count=counts.get(status, 0))
            for status in PIPELINE_ORDER
        ]
        # 알 수 없는 상태가 생기면 조용히 버리지 않고 뒤에 붙인다.
        stages.extend(
            PipelineStage(status=status, count=count)
            for status, count in counts.items()
            if status not in PIPELINE_ORDER
        )
        return stages

    # ── GET /activity ──

    def get_activity(
        self,
        *,
        limit: int = 10,
        project_id: int | None = None,
        action: str | None = None,
    ) -> list[ActivityItem]:
        """activity_logs 를 최신순으로 읽어 표시용으로 풀어 준다.

        저장은 액션 코드(`document.upload` 등)로 하고, 사람이 읽을 문구와 배지 kind 는
        읽을 때 `activity_service.describe()` 로 만든다 — 문구가 바뀌어도 과거 로그가
        안 깨지게 하기 위함.
        """
        self._ensure_project(project_id)
        logs = self.activity_logs.list_recent(
            limit=limit, project_id=project_id, action=action
        )

        # 대상이 아직 살아 있는지 한 번에 확인한다. 삭제 액션만 걸러내는 걸로는 부족하다 —
        # 업로드 로그도 그 문서가 나중에 지워졌으면 링크가 404 가 되기 때문.
        ids_by_type: dict[str, set[int]] = defaultdict(set)
        for log in logs:
            if log.target_type and log.target_id is not None:
                ids_by_type[log.target_type].add(log.target_id)
        alive = self.activity_logs.existing_targets(ids_by_type)

        items = []
        for log in logs:
            phrase, kind = describe(log)
            items.append(
                ActivityItem(
                    actor=log.actor_label or UNKNOWN_ACTOR,
                    action=phrase,
                    target=log.target_label or "",
                    kind=kind,
                    at=log.created_at,
                    target_type=log.target_type,
                    target_id=log.target_id,
                    target_alive=(log.target_type, log.target_id) in alive,
                )
            )
        return items


def get_dashboard_service(db: Session = Depends(get_db)) -> DashboardService:
    return DashboardService(
        DashboardRepository(db), ActivityLogRepository(db), ProjectRepository(db)
    )
