"""수동 휴게/마감 처리 엔드포인트 (GAP-03 수동 모드).

자동 시간 감지 없이 관리자가 명시적으로 호출.
- POST /pre-break-sweep : 휴게 전 미시작 READY 태스크 일괄 취소
- POST /cutoff-boost    : 주문 마감 임박 → HIGH 이상 긴급 웨이브 즉시 생성
"""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.core.dependencies import get_session
from app.core.logging_config import get_logger
from app.models.task import ReplenishConfirmedTask
from app.models.wave import Wave
from app.services.audit_service import write_audit_log
from app.services.state_machine import transition_task

router = APIRouter()
logger = get_logger("schedule")


@router.post("/pre-break-sweep")
def pre_break_sweep(
    actor: str = Query(default="관리자"),
    wave_id: int | None = Query(default=None, description="지정하면 해당 웨이브만, 생략 시 모든 CONFIRMED 웨이브"),
    session: Session = Depends(get_session),
) -> Any:
    """휴게 전 미시작(READY) 태스크를 일괄 취소 (GAP-03 수동 트리거).

    - QUEUED / SENT 상태(이미 현장 투입)는 건드리지 않음
    - 취소 사유: 'PRE_BREAK_SWEEP'으로 기록
    """
    q = select(ReplenishConfirmedTask).where(
        ReplenishConfirmedTask.task_status == "READY"
    )
    if wave_id is not None:
        q = q.where(ReplenishConfirmedTask.wave_id == wave_id)
    else:
        # 전체 CONFIRMED 웨이브 대상
        active_wave_ids = [
            w.wave_id
            for w in session.exec(
                select(Wave).where(Wave.wave_status.in_(["CONFIRMED", "SENT"]))
            ).all()
        ]
        if not active_wave_ids:
            return {"cancelled": 0, "skipped_in_progress": 0, "message": "활성 웨이브 없음"}
        q = q.where(ReplenishConfirmedTask.wave_id.in_(active_wave_ids))

    tasks = session.exec(q).all()

    cancelled = 0
    skipped = 0
    for task in tasks:
        if task.task_status != "READY":
            skipped += 1
            continue
        transition_task(
            task.task_id,
            "CANCELLED",
            actor=actor,
            cancel_reason="PRE_BREAK_SWEEP",
            session=session,
        )
        cancelled += 1

    write_audit_log(
        entity_type="schedule",
        entity_id=wave_id or 0,
        action="pre_break_sweep",
        actor=actor,
        after={"cancelled": cancelled, "skipped_in_progress": skipped},
        session=session,
    )
    session.commit()

    logger.info("휴게 전 sweep 완료", cancelled=cancelled, skipped=skipped, wave_id=wave_id)
    return {
        "cancelled": cancelled,
        "skipped_in_progress": skipped,
        "message": f"READY {cancelled}건 취소 완료 (진행 중 {skipped}건 보존)",
    }


@router.post("/cutoff-boost")
def cutoff_boost(
    center_cd: str = Query(default="GGH1"),
    min_risk_level: str = Query(default="HIGH", pattern="^(CRITICAL|HIGH)$"),
    actor: str = Query(default="관리자"),
    session: Session = Depends(get_session),
) -> Any:
    """주문 마감 임박 시 HIGH 이상 긴급 웨이브 즉시 생성 (GAP-03 수동 트리거).

    기존 /waves/urgent-from-dashboard 와 동일한 흐름이지만
    min_risk_level=HIGH를 기본값으로 하는 단순 shortcut.
    """
    from app.models.task import ReplenishCandidate
    from app.services.wave_builder import run_algorithm
    from app.services.audit_service import write_audit_log as _audit

    wave = Wave(
        wave_name=f"마감웨이브_{datetime.now().strftime('%m%d_%H%M')}",
        wave_type="URGENT",
        wave_status="DRAFT",
        target_sku_count=60,
        created_by=actor,
    )
    session.add(wave)
    session.commit()
    session.refresh(wave)

    _audit(
        entity_type="wave", entity_id=wave.wave_id,
        action="created", actor=actor,
        after={"wave_name": wave.wave_name, "trigger": "cutoff_boost"},
        session=session,
    )
    session.commit()

    run_algorithm(center_cd, wave.wave_id, session)

    risk_keep = {"CRITICAL"} if min_risk_level == "CRITICAL" else {"CRITICAL", "HIGH"}
    candidates = session.exec(
        select(ReplenishCandidate).where(ReplenishCandidate.wave_id == wave.wave_id)
    ).all()

    kept = 0
    for c in candidates:
        if c.risk_level not in risk_keep:
            session.delete(c)
        else:
            c.candidate_status = "APPROVED"
            kept += 1
    session.commit()

    if kept == 0:
        wave.wave_status = "CANCELLED"
        wave.cancelled_at = datetime.utcnow()
        session.commit()
        return {
            "wave_id": wave.wave_id,
            "candidates": 0,
            "confirmed": False,
            "message": f"{min_risk_level} 이상 후보 없음 — 웨이브 취소됨",
        }

    # 자동 확정
    from app.api.waves import _confirm_wave_internal
    result = _confirm_wave_internal(wave.wave_id, session, confirmed_by=actor)

    logger.info("마감 부스트 웨이브 생성", wave_id=wave.wave_id, tasks=result["tasks_created"])
    return {
        "wave_id": wave.wave_id,
        "candidates": kept,
        "confirmed": True,
        "tasks_created": result["tasks_created"],
        "message": f"{min_risk_level} 이상 {kept}개 후보 → {result['tasks_created']}건 태스크 확정",
    }
