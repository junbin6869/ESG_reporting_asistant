from fastapi import APIRouter

from app.schemas import DashboardSummary
from app.services.summary_service import get_dashboard_summary

router = APIRouter(prefix="/summaries", tags=["summaries"])


@router.get("", response_model=DashboardSummary)
def get_summary(period_start: str, period_end: str) -> DashboardSummary:
    return get_dashboard_summary(period_start=period_start, period_end=period_end)
