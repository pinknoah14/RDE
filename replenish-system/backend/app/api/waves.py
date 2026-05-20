from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.dependencies import get_session
from app.models.task import ReplenishCandidate, ReplenishConfirmedTask, ReplenishTaskLocation
from app.models.wave import Wave
from app.models.zone import ZoneConfig
from app.services.algorithm import AlgorithmResult, run_algorithm
from app.services.slack_service import delete_wave_messages, send_wave_messages
from app.services.state_machine import InvalidTransitionError, transition_candidate, transition_task

router = APIRouter()


class WaveCreateRequest(BaseModel):
    wave_name: str | None = None
    wave_type: str = "REGULAR"
    center_cd: str = "GGH1"
    max_candidates: int | None = None
    urgent_only: bool = False
    min_risk_score: int | None = None
    zone_filter: list[str] | None = None
    target_days: float | None = None


class CandidatePatch(BaseModel):
    modified_qty: int | None = None


class AssignRequest(BaseModel):
    worker_id: int


@router.post("")
def create_wave(body: WaveCreateRequest, session: Session = Depends(get_session)) -> Any:
    wave = Wave(
        wave_name=body.wave_name or f"웨이브_{datetime.now().strftime('%m%d_%H%M')}",
        wave_type=body.wave_type,
        wave_status="DRAFT",
        target_sku_count=body.max_candidates or 40,
        created_by="관리자",
    )
    session.add(wave)
    session.commit()
    session.refresh(wave)

    algo = run_algorithm(body.center_cd, wave.wave_id, session)

    if body.min_risk_score is not None:
        candidates = session.exec(
            select(ReplenishCandidate).where(
                ReplenishCandidate.wave_id == wave.wave_id,
                ReplenishCandidate.risk_score < body.min_risk_score,
            )
        ).all()
        for c in candidates:
            session.delete(c)
        session.commit()

    return {
        "wave_id": wave.wave_id,
        "wave_name": wave.wave_name,
        "algorithm": {
            "total_candidates": algo.total_candidates,
            "critical": algo.critical_count,
            "high": algo.high_count,
            "medium": algo.medium_count,
            "low": algo.low_count,
            "no_replen_skus": algo.no_replen_skus,
            "execution_ms": algo.execution_ms,
        },
    }


@router.get("")
def list_waves(session: Session = Depends(get_session)) -> Any:
    return session.exec(select(Wave).order_by(Wave.created_at.desc()).limit(50)).all()


@router.get("/{wave_id}")
def get_wave(wave_id: int, session: Session = Depends(get_session)) -> Any:
    wave = session.get(Wave, wave_id)
    if not wave:
        raise HTTPException(status_code=404, detail="웨이브 없음")
    return wave


@router.get("/{wave_id}/candidates")
def list_candidates(
    wave_id: int,
    status: str | None = Query(default=None),
    min_score: float | None = Query(default=None),
    session: Session = Depends(get_session),
) -> Any:
    q = select(ReplenishCandidate).where(ReplenishCandidate.wave_id == wave_id)
    if status:
        q = q.where(ReplenishCandidate.candidate_status == status)
    if min_score is not None:
        q = q.where(ReplenishCandidate.risk_score >= min_score)
    return session.exec(q.order_by(ReplenishCandidate.risk_score.desc())).all()


@router.post("/{wave_id}/candidates/{candidate_id}/approve")
def approve_candidate(wave_id: int, candidate_id: int, session: Session = Depends(get_session)) -> Any:
    try:
        return transition_candidate(candidate_id, "APPROVED", actor="관리자", session=session)
    except InvalidTransitionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{wave_id}/candidates/{candidate_id}/reject")
def reject_candidate(
    wave_id: int,
    candidate_id: int,
    reason: str = Query(default=""),
    session: Session = Depends(get_session),
) -> Any:
    try:
        return transition_candidate(candidate_id, "REJECTED", actor="관리자", rejected_reason=reason, session=session)
    except InvalidTransitionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{wave_id}/candidates/{candidate_id}")
def update_candidate(
    wave_id: int,
    candidate_id: int,
    body: CandidatePatch,
    session: Session = Depends(get_session),
) -> Any:
    if body.modified_qty is not None:
        try:
            return transition_candidate(
                candidate_id, "MODIFIED", actor="관리자",
                modified_qty=body.modified_qty, session=session
            )
        except InvalidTransitionError as e:
            raise HTTPException(status_code=400, detail=str(e))
    candidate = session.get(ReplenishCandidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="후보 없음")
    return candidate


@router.post("/{wave_id}/candidates/{candidate_id}/assign")
def assign_candidate(
    wave_id: int,
    candidate_id: int,
    body: AssignRequest,
    session: Session = Depends(get_session),
) -> Any:
    candidate = session.get(ReplenishCandidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="후보 없음")
    candidate.updated_at = datetime.utcnow()
    session.commit()
    return {"candidate_id": candidate_id, "worker_id": body.worker_id}


@router.post("/{wave_id}/confirm")
def confirm_wave(
    wave_id: int,
    confirmed_by: str = Query(default="관리자"),
    session: Session = Depends(get_session),
) -> Any:
    wave = session.get(Wave, wave_id)
    if not wave:
        raise HTTPException(status_code=404, detail="웨이브 없음")

    candidates = session.exec(
        select(ReplenishCandidate).where(
            ReplenishCandidate.wave_id == wave_id,
            ReplenishCandidate.candidate_status.in_(["APPROVED", "MODIFIED"]),
        )
    ).all()
    if not candidates:
        raise HTTPException(status_code=400, detail="승인된 후보 없음")

    tasks_created = 0
    for c in candidates:
        qty = c.modified_qty if c.modified_qty else c.recommended_qty
        zc = session.exec(
            select(ZoneConfig).where(ZoneConfig.zone_prefix == c.zone)
        ).first()
        worker_type = zc.access_type if zc else "FORKLIFT"

        task = ReplenishConfirmedTask(
            candidate_id=c.candidate_id,
            wave_id=wave_id,
            sku_id=c.sku_id,
            sku_name=c.sku_name,
            picking_bin=c.picking_bin,
            zone=c.zone,
            slack_channel=c.slack_channel,
            list_section=c.list_section,
            worker_type=worker_type,
            total_qty=qty,
            confirm_type="AUTO",
            confirmed_by=confirmed_by,
            task_status="READY",
        )
        session.add(task)
        tasks_created += 1

    wave.wave_status = "CONFIRMED"
    wave.confirmed_at = datetime.utcnow()
    session.commit()
    return {"wave_id": wave_id, "tasks_created": tasks_created}


@router.post("/{wave_id}/send")
def send_wave(wave_id: int, session: Session = Depends(get_session)) -> Any:
    wave = session.get(Wave, wave_id)
    if not wave:
        raise HTTPException(status_code=404, detail="웨이브 없음")
    result = send_wave_messages(wave_id, session)
    wave.wave_status = "SENT"
    wave.sent_at = datetime.utcnow()
    session.commit()
    return result


@router.delete("/{wave_id}/messages")
def delete_messages(wave_id: int, session: Session = Depends(get_session)) -> Any:
    return delete_wave_messages(wave_id, session)


@router.post("/{wave_id}/resend")
def resend_wave(
    wave_id: int,
    target_channel_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> Any:
    return send_wave_messages(wave_id, session)


@router.get("/{wave_id}/tasks")
def list_tasks(wave_id: int, session: Session = Depends(get_session)) -> Any:
    return session.exec(
        select(ReplenishConfirmedTask).where(ReplenishConfirmedTask.wave_id == wave_id)
    ).all()


@router.post("/{wave_id}/tasks/{task_id}/transition")
def transition_task_endpoint(
    wave_id: int,
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
