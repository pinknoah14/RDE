from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.core.dependencies import get_session
from app.models.zone import UnknownZoneFlag
from app.models.sku import SkuPickingHistory

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
