from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="헬스체크",
    response_description="`status` 는 항상 ok, `db` 는 DB 핑 결과(ok/error)",
)
def health_check(db: Session = Depends(get_db)) -> dict[str, str]:
    """서버와 DB 연결 상태를 확인한다. 인증이 필요 없다.

    DB 에 `SELECT 1` 을 날려보고, 실패해도 200 을 유지한 채 `db: "error"` 로만 알린다
    (컨테이너 오케스트레이터가 앱 자체는 살아 있다고 판단할 수 있게).
    """
    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"

    return {"status": "ok", "db": db_status}
