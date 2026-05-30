"""완료 이중검증 (GAP-04b).

보충 완료(DONE)로 표시됐으나 다음 재고 CSV에서 피킹재고가 여전히 0인
태스크를 불일치로 탐지한다. 작업자가 완료 처리했지만 실제 보충이 피킹
지번에 반영되지 않은 상황을 관리자가 조기에 발견하도록 돕는다.
"""
from sqlmodel import Session, select

from app.models.sku import SkuPickingHistory
from app.models.task import ReplenishConfirmedTask


def detect_done_mismatches(
    center_cd: str, session: Session, wave_id: int | None = None
) -> list[dict]:
    """DONE 처리됐으나 피킹재고(last_seen_qty)가 여전히 0 이하인 태스크 목록.

    - wave_id 지정 시 해당 웨이브만, 미지정 시 센터 전체 DONE 태스크 대상
    - 피킹 이력이 없거나 last_seen_qty<=0 이면 불일치로 간주
    """
    query = select(ReplenishConfirmedTask).where(
        ReplenishConfirmedTask.task_status == "DONE",
    )
    if wave_id is not None:
        query = query.where(ReplenishConfirmedTask.wave_id == wave_id)
    done_tasks = session.exec(query).all()

    if not done_tasks:
        return []

    picking_qty: dict[str, int | None] = {
        h.sku_id: h.last_seen_qty
        for h in session.exec(
            select(SkuPickingHistory).where(
                SkuPickingHistory.center_cd == center_cd,
            )
        ).all()
    }

    mismatches: list[dict] = []
    for t in done_tasks:
        qty = picking_qty.get(t.sku_id)
        if qty is None or qty <= 0:
            mismatches.append({
                "task_id": t.task_id,
                "wave_id": t.wave_id,
                "sku_id": t.sku_id,
                "sku_name": t.sku_name,
                "picking_bin": t.picking_bin,
                "current_picking_qty": qty or 0,
                "done_at": t.done_at.isoformat() if t.done_at else None,
            })
    return mismatches
