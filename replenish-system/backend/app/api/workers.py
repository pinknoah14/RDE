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
    worker_type: str        # FORKLIFT / WALKING (보유 장비)
    zone_access: str        # JSON 배열 문자열
    max_tasks: int = 6
    slack_id: str | None = None
    skill_level: str = "NORMAL"   # EXPERT / NORMAL / JUNIOR
    work_type: str | None = None  # 미입력 시 worker_type 사용


class WorkerUpdate(BaseModel):
    worker_name: str | None = None
    zone_access: str | None = None
    max_tasks: int | None = None
    slack_id: str | None = None
    is_active: bool | None = None
    is_sub_worker: bool | None = None
    skill_level: str | None = None
    work_type: str | None = None


class WorkTypeUpdate(BaseModel):
    work_type: str   # FORKLIFT | WALKING


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
        skill_level=body.skill_level,
        work_type=body.work_type or body.worker_type,
    )
    session.add(worker)
    session.commit()
    session.refresh(worker)
    return worker


@router.patch("/{worker_id}/work-type")
def update_work_type(
    worker_id: int,
    body: WorkTypeUpdate,
    session: Session = Depends(get_session),
) -> Any:
    if body.work_type not in ("FORKLIFT", "WALKING"):
        raise HTTPException(status_code=400, detail="work_type은 FORKLIFT 또는 WALKING")
    worker = session.get(Worker, worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="작업자 없음")
    worker.work_type = body.work_type
    worker.updated_at = datetime.utcnow()
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
