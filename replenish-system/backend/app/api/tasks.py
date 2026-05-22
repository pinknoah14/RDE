from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.core.dependencies import get_session
from app.models.task import ReplenishConfirmedTask, ReplenishTaskLocation
from app.services.state_machine import InvalidTransitionError, transition_task

router = APIRouter()


@router.get("")
def list_tasks(
    wave_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> Any:
    q = select(ReplenishConfirmedTask)
    if wave_id is not None:
        q = q.where(ReplenishConfirmedTask.wave_id == wave_id)
    if status:
        q = q.where(ReplenishConfirmedTask.task_status == status)
    return session.exec(q.order_by(ReplenishConfirmedTask.created_at)).all()


@router.get("/{task_id}")
def get_task(task_id: int, session: Session = Depends(get_session)) -> Any:
    task = session.get(ReplenishConfirmedTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="태스크 없음")
    locations = session.exec(
        select(ReplenishTaskLocation)
        .where(ReplenishTaskLocation.task_id == task_id)
        .order_by(ReplenishTaskLocation.seq)
    ).all()
    return {**task.model_dump(), "locations": [loc.model_dump() for loc in locations]}


@router.post("/{task_id}/transition")
def transition_task_endpoint(
    task_id: int,
    new_status: str = Query(...),
    actor: str = Query(default="관리자"),
    block_reason: str | None = Query(default=None),
    shortage_qty: int | None = Query(default=None),
    cancel_reason: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> Any:
    try:
        return transition_task(
            task_id, new_status, actor=actor,
            block_reason=block_reason, shortage_qty=shortage_qty,
            cancel_reason=cancel_reason, session=session,
        )
    except InvalidTransitionError as e:
        raise HTTPException(status_code=400, detail=str(e))
