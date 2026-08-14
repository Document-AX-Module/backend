from app.modules.spell_check.schemas import SpellCheckResponse


class SpellCheckService:
    """오타 검증 모듈의 서비스 인터페이스.

    실제 맞춤법 검사 로직(규칙 기반 / LLM 기반)은 여기에 연결한다.
    """

    def check(self, text: str) -> SpellCheckResponse:
        return SpellCheckResponse(
            original_text=text,
            corrected_text=text,
            corrections=[],
        )


def get_spell_check_service() -> SpellCheckService:
    return SpellCheckService()
