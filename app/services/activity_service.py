from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.activity_log import ActivityLog
from app.models.user import User
from app.repositories.activity_repository import ActivityLogRepository

logger = logging.getLogger(__name__)

TARGET_LABEL_MAX = 500


class Action:
    """활동 코드 목록.

    DB 에는 표시 문구가 아니라 **이 코드**를 저장한다. 문구를 바꿔도 과거 로그가
    안 깨지고, 코드로 필터링할 수 있기 때문이다. 새 코드를 추가하면
    `ACTION_LABELS` 에도 반드시 등록할 것.
    """

    LOGIN = "auth.login"

    PROJECT_CREATE = "project.create"
    PROJECT_UPDATE = "project.update"
    PROJECT_DELETE = "project.delete"

    FOLDER_CREATE = "folder.create"
    FOLDER_UPDATE = "folder.update"
    FOLDER_DELETE = "folder.delete"

    DOCUMENT_UPLOAD = "document.upload"
    DOCUMENT_DELETE = "document.delete"

    ANALYSIS_START = "analysis.start"
    ANALYSIS_COMPLETE = "analysis.complete"
    ANALYSIS_FAIL = "analysis.fail"


# 액션 코드 → (표시 문구, 프론트 배지 kind)
ACTION_LABELS: dict[str, tuple[str, str]] = {
    Action.LOGIN: ("로그인", "로그인"),
    Action.PROJECT_CREATE: ("프로젝트 생성", "프로젝트"),
    Action.PROJECT_UPDATE: ("프로젝트 수정", "프로젝트"),
    Action.PROJECT_DELETE: ("프로젝트 삭제", "삭제"),
    Action.FOLDER_CREATE: ("폴더 생성", "폴더"),
    Action.FOLDER_UPDATE: ("폴더 수정/이동", "폴더"),
    Action.FOLDER_DELETE: ("폴더 삭제", "삭제"),
    Action.DOCUMENT_UPLOAD: ("문서 업로드", "업로드"),
    Action.DOCUMENT_DELETE: ("문서 삭제", "삭제"),
}

# AI 분석은 analysis_type 에 따라 문구가 갈린다 → (모듈 이름, 배지 kind)
ANALYSIS_LABELS: dict[str, tuple[str, str]] = {
    "proofread": ("오탈자 검증", "오타"),
    "classify": ("문서 분류", "분류"),
    "validate": ("규정 검증", "규정"),
    "related": ("관련 문서 추천", "추천"),
}

_ANALYSIS_SUFFIX = {
    Action.ANALYSIS_START: "시작",
    Action.ANALYSIS_COMPLETE: "완료",
    Action.ANALYSIS_FAIL: "실패",
}


def describe(log: ActivityLog) -> tuple[str, str]:
    """활동 로그 한 건을 (표시 문구, 배지 kind)로 풀어 준다."""
    if log.action in _ANALYSIS_SUFFIX:
        analysis_type = (log.meta or {}).get("analysis_type", "")
        name, kind = ANALYSIS_LABELS.get(analysis_type, (analysis_type or "AI 분석", "분석"))
        return f"{name} {_ANALYSIS_SUFFIX[log.action]}", kind
    # 등록되지 않은 코드도 피드에서 조용히 사라지지 않게 코드 자체를 노출한다.
    return ACTION_LABELS.get(log.action, (log.action, "기타"))


class ActivityService:
    """감사 로그 기록기.

    **기록은 항상 best-effort** — 로그를 남기다 실패해도 본 작업(업로드·삭제 등)을
    깨뜨리지 않는다. 반대로 본 작업이 커밋된 뒤에 호출해야 하며, 그래야 롤백된
    작업의 로그가 남는 일도 없다.
    """

    def __init__(
        self, repository: ActivityLogRepository, actor: User | None = None
    ) -> None:
        self.repository = repository
        self.actor = actor

    def record(
        self,
        action: str,
        *,
        actor: User | None = None,
        actor_id: int | None = None,
        actor_label: str | None = None,
        project_id: int | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        target_label: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """활동 한 건을 남긴다. 예외를 밖으로 던지지 않는다.

        행위자는 `actor`(User) → 명시적 `actor_id`/`actor_label` → 생성 시 주입된
        actor 순으로 결정된다. 백그라운드 작업처럼 요청 사용자가 없는 곳에서는
        `actor_id` 를 직접 넘긴다.
        """
        who = actor or self.actor
        if who is not None:
            actor_id = who.id
            actor_label = who.name or who.email

        try:
            self.repository.add(
                ActivityLog(
                    actor_id=actor_id,
                    actor_label=_truncate(actor_label, 255),
                    action=action,
                    project_id=project_id,
                    target_type=target_type,
                    target_id=target_id,
                    target_label=_truncate(target_label, TARGET_LABEL_MAX),
                    meta=meta,
                )
            )
        except Exception:
            # 세션이 오염된 채로 남으면 뒤이은 요청까지 말려들므로 되돌려 놓는다.
            logger.warning("활동 로그 기록 실패: action=%s", action, exc_info=True)
            try:
                self.repository.db.rollback()
            except Exception:
                logger.exception("활동 로그 롤백 실패")


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[: limit - 1] + "…"


def get_activity_service(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ActivityService:
    """인증된 라우트용 DI. 로그인 사용자가 행위자로 자동 세팅된다."""
    return ActivityService(ActivityLogRepository(db), actor=current_user)


def activity_service_for(db: Session, actor: User | None = None) -> ActivityService:
    """DI 밖(백그라운드 작업·OAuth 콜백)에서 세션을 직접 들고 만들 때."""
    return ActivityService(ActivityLogRepository(db), actor=actor)
