from fastapi import APIRouter, Depends

from app.modules.spell_check.schemas import SpellCheckRequest, SpellCheckResponse
from app.modules.spell_check.service import SpellCheckService, get_spell_check_service

router = APIRouter(prefix="/spell-check", tags=["spell-check"])


@router.post("", response_model=SpellCheckResponse)
def check_spelling(
    request: SpellCheckRequest,
    service: SpellCheckService = Depends(get_spell_check_service),
) -> SpellCheckResponse:
    return service.check(request.text)
