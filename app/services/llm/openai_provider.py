from __future__ import annotations

import json

import httpx

from app.services.llm.base import LLMConfigError, LLMError, ProofreadFinding
from app.services.llm.prompts import PROOFREAD_BATCH_SYSTEM, PROOFREAD_SYSTEM


class OpenAILLMProvider:
    """OpenAI Chat Completions 로 교정하는 provider (httpx 직접 호출)."""

    def __init__(
        self,
        api_key: str | None,
        *,
        model: str,
        base_url: str,
        timeout: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self.name = f"openai:{model}"

    def proofread(self, text: str) -> list[ProofreadFinding]:
        content = self._chat(PROOFREAD_SYSTEM, text)
        data = self._load_json(content)
        raw = data.get("findings", []) if isinstance(data, dict) else []
        return self._to_findings(raw)

    def proofread_batch(self, texts: list[str]) -> list[list[ProofreadFinding]]:
        if not texts:
            return []
        # [번호] 마커로 여러 조각을 한 요청에 담는다.
        user = "\n\n".join(f"[{i}] {t}" for i, t in enumerate(texts))
        content = self._chat(PROOFREAD_BATCH_SYSTEM, user)
        data = self._load_json(content)

        out: list[list[ProofreadFinding]] = [[] for _ in texts]
        results = data.get("results", []) if isinstance(data, dict) else []
        for item in results:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            if not isinstance(idx, int) or not (0 <= idx < len(texts)):
                continue
            out[idx] = self._to_findings(item.get("findings", []))
        return out

    # ── 내부 ──

    def _chat(self, system: str, user: str) -> str:
        if not self._api_key:
            raise LLMConfigError("OPENAI_API_KEY 가 설정되지 않았습니다")
        payload = {
            "model": self._model,
            # 최신 모델(gpt-5.x 계열)은 temperature 커스텀을 막고 기본값(1)만 허용하므로
            # 아예 보내지 않는다. (구모델은 어차피 기본값 1과 동일하게 동작)
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            resp = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"OpenAI 응답 오류 {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            raise LLMError(f"OpenAI 호출 실패: {exc}") from exc

    @staticmethod
    def _load_json(content: str) -> dict:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"OpenAI 응답 JSON 파싱 실패: {exc}") from exc
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _to_findings(raw) -> list[ProofreadFinding]:
        findings: list[ProofreadFinding] = []
        if not isinstance(raw, list):
            return findings
        for item in raw:
            if not isinstance(item, dict):
                continue
            original = str(item.get("original", "")).strip()
            if not original:
                continue
            findings.append(
                ProofreadFinding(
                    original=original,
                    suggestion=str(item.get("suggestion", "")).strip(),
                    reason=str(item.get("reason", "")).strip(),
                    category=str(item.get("category", "spelling")).strip() or "spelling",
                )
            )
        return findings
