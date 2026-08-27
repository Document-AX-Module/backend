"""OpenAI 조직 Admin API 클라이언트"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx

from app.core.config import Settings


class OpenAICostsUnavailable(Exception):
    """Admin 키 미설정 또는 Admin API 호출 실패 — 로컬 추정치로 폴백해야 한다는 신호."""


@dataclass
class DailyCost:
    day: date
    amount_usd: float


@dataclass
class DailyUsage:
    day: date
    model: str
    calls: int
    input_tokens: int
    output_tokens: int


def fetch_daily_costs_usd(settings: Settings, *, start: date, end: date) -> list[DailyCost]:
    if not settings.openai_admin_api_key:
        raise OpenAICostsUnavailable("OPENAI_ADMIN_API_KEY 가 설정되지 않았습니다")

    start_time = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    end_time = int(datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    base = settings.openai_base_url.rstrip("/")

    try:
        results: list[DailyCost] = []
        page: str | None = None
        while True:
            params: dict[str, object] = {
                "start_time": start_time,
                "end_time": end_time,
                "bucket_width": "1d",
                "limit": 31,
            }
            if page:
                params["page"] = page
            resp = httpx.get(
                f"{base}/organization/costs",
                headers={"Authorization": f"Bearer {settings.openai_admin_api_key}"},
                params=params,
                timeout=15.0,
            )
            resp.raise_for_status()
            body = resp.json()
            for bucket in body.get("data", []):
                bucket_day = datetime.fromtimestamp(
                    bucket["start_time"], tz=timezone.utc
                ).date()
                amount = sum(
                    item.get("amount", {}).get("value", 0.0)
                    for item in bucket.get("results", [])
                )
                results.append(DailyCost(day=bucket_day, amount_usd=amount))
            if not body.get("has_more"):
                break
            page = body.get("next_page")
            if not page:
                break
        return results
    except httpx.HTTPError as exc:
        raise OpenAICostsUnavailable(f"OpenAI Costs API 호출 실패: {exc}") from exc


def fetch_daily_usage(settings: Settings, *, start: date, end: date) -> list[DailyUsage]:
    """일별×모델별 실제 호출 수/입력토큰/출력토큰. (실패 시 캐시된 값이 없으므로 즉시 raise)"""
    if not settings.openai_admin_api_key:
        raise OpenAICostsUnavailable("OPENAI_ADMIN_API_KEY 가 설정되지 않았습니다")

    start_time = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    end_time = int(datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    base = settings.openai_base_url.rstrip("/")

    try:
        results: list[DailyUsage] = []
        page: str | None = None
        while True:
            params: dict[str, object] = {
                "start_time": start_time,
                "end_time": end_time,
                "bucket_width": "1d",
                "limit": 31,
                "group_by": ["model"],
            }
            if page:
                params["page"] = page
            resp = httpx.get(
                f"{base}/organization/usage/completions",
                headers={"Authorization": f"Bearer {settings.openai_admin_api_key}"},
                params=params,
                timeout=15.0,
            )
            resp.raise_for_status()
            body = resp.json()
            for bucket in body.get("data", []):
                bucket_day = datetime.fromtimestamp(
                    bucket["start_time"], tz=timezone.utc
                ).date()
                for item in bucket.get("results", []):
                    results.append(
                        DailyUsage(
                            day=bucket_day,
                            model=item.get("model") or "unknown",
                            calls=item.get("num_model_requests", 0),
                            input_tokens=item.get("input_tokens", 0),
                            output_tokens=item.get("output_tokens", 0),
                        )
                    )
            if not body.get("has_more"):
                break
            page = body.get("next_page")
            if not page:
                break
        return results
    except httpx.HTTPError as exc:
        raise OpenAICostsUnavailable(f"OpenAI Usage API 호출 실패: {exc}") from exc
