from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import Session, func, select

from app.core.dependencies import get_session
from app.models.sku import SkuPickingHistory
from app.models.task import ReplenishCandidate, ReplenishConfirmedTask
from app.models.wave import Wave
from app.models.worker import Worker
from app.models.zone import UnknownZoneFlag

router = APIRouter()


@router.get("/unknown-zones")
def dashboard_unknown_zones(session: Session = Depends(get_session)):
    return session.exec(
        select(UnknownZoneFlag)
        .where(UnknownZoneFlag.is_resolved == False)  # noqa: E712
        .order_by(UnknownZoneFlag.seen_count.desc())
    ).all()


@router.get("/multi-bin-skus")
def dashboard_multi_bin_skus(session: Session = Depends(get_session)):
    return session.exec(
        select(SkuPickingHistory)
        .where(SkuPickingHistory.has_multi_bin == True)  # noqa: E712
    ).all()


@router.get("")
def dashboard_summary(session: Session = Depends(get_session)) -> Any:
    """대시보드 통합 요약. 위험도 분포는 최신 웨이브 후보 기준."""
    risk_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    critical_skus: list[dict] = []

    latest_wave = session.exec(
        select(Wave).order_by(Wave.created_at.desc())
    ).first()

    if latest_wave:
        candidates = session.exec(
            select(ReplenishCandidate).where(
                ReplenishCandidate.wave_id == latest_wave.wave_id
            )
        ).all()
        for c in candidates:
            if c.risk_level in risk_counts:
                risk_counts[c.risk_level] += 1

        criticals = sorted(
            (c for c in candidates if c.risk_level == "CRITICAL"),
            key=lambda c: c.risk_score,
            reverse=True,
        )[:10]
        critical_skus = [
            {
                "sku_id": c.sku_id,
                "sku_name": c.sku_name,
                "risk_score": c.risk_score,
                "eta_hours": c.eta_hours,
            }
            for c in criticals
        ]

    total_workers = session.exec(select(func.count()).select_from(Worker)).one()
    active_workers = session.exec(
        select(func.count()).select_from(Worker).where(Worker.is_active == True)  # noqa: E712
    ).one()

    new_skus = session.exec(
        select(func.count()).select_from(SkuPickingHistory)
        .where(SkuPickingHistory.is_new_sku == True)  # noqa: E712
    ).one()
    stale_bins = session.exec(
        select(func.count()).select_from(SkuPickingHistory)
        .where(SkuPickingHistory.confidence == "STALE")
    ).one()

    unknown_zones = [z.zone_prefix for z in dashboard_unknown_zones(session)]
    multi_bin_skus = len(dashboard_multi_bin_skus(session))

    unclaimed_tasks = session.exec(
        select(func.count()).select_from(ReplenishConfirmedTask).where(
            ReplenishConfirmedTask.claimed_by.is_(None),
            ReplenishConfirmedTask.task_status.not_in(["DONE", "CANCELLED"]),
        )
    ).one()

    return {
        "risk_counts": risk_counts,
        "critical_skus": critical_skus,
        "active_workers": active_workers,
        "total_workers": total_workers,
        "new_skus": new_skus,
        "stale_bins": stale_bins,
        "unknown_zones": unknown_zones,
        "unclaimed_tasks": unclaimed_tasks,
        "multi_bin_skus": multi_bin_skus,
    }
