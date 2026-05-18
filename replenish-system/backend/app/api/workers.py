from datetime import datetime
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.dependencies import get_session
from app.models.worker import Worker
from app.services.audit_service import write_audit_log

router = APIRouter()


class WorkerCreate(BaseModel):
    worker_name: str
    worker_type: str        # FORKLIFT / WALKING
    zone_access: str        # JSON 배열 문자열
    max_tasks: int = 6
    slack_id: str | None = None


class WorkerUpdate(BaseModel):
    worker_name: str | None = None
    zone_access: str | None = None
    max_tasks: int | None = None
    slack_id: str | None = None
    is_active: bool | None = None
    is_sub_worker: bool | None = None


@router.get("")
def list_workers(session: Session = Depends(get_session)) -> list[Any]:
    workers = session.exec(select(Worker).order_by(Worker.worker_id)).all()
    return workers


@router.post("")
def create_worker(body: WorkerCreate, session: Session = Depends(get_session)) -> Any:
    worker = Worker(
        worker_name=body.worker_name,
        worker_type=body.worker_type,
        zone_access=body.zone_access,
        max_tasks=body.max_tasks,
        slack_id=body.slack_id,
    )
    session.add(worker)
    session.commit()
    session.refresh(worker)
    return worker


@router.patch("/{worker_id}")
def update_worker(
    worker_id: int,
    body: WorkerUpdate,
    session: Session = Depends(get_session),
) -> Any:
    worker = session.get(Worker, worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="작업자 없음")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(worker, field, value)
    worker.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(worker)
    return worker


@router.post("/daily-reset")
def daily_reset(session: Session = Depends(get_session)) -> dict:
    workers = session.exec(select(Worker)).all()
    for w in workers:
        w.is_active = False
        w.is_sub_worker = False
        w.updated_at = datetime.utcnow()
    write_audit_log("worker", 0, "daily_reset", "system", session=session)
    session.commit()
    return {"reset_count": len(workers)}
