import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.dependencies import get_session
from app.core.exceptions import RDEException
from app.core.logging_config import get_logger
from app.models.task import ReplenishCandidate, ReplenishConfirmedTask, ReplenishTaskLocation
from app.models.wave import Wave
from app.models.zone import ZoneConfig
from app.services.audit_service import write_audit_log
from app.services.wave_builder import run_algorithm
from app.services.csv_parser import extract_zone_prefix
from app.services.state_machine import InvalidTransitionError, transition_candidate
from app.services.wave_builder import calculate_prestock_cutoff

router = APIRouter()
logger = get_logger("wave")


class WaveCreateRequest(BaseModel):
    wave_name: str | None = None
    wave_type: str = "REGULAR"   # REGULAR | URGENT | PRESTOCK
    center_cd: str = "GGH1"
    max_candidates: int | None = None
    urgent_only: bool = False
    min_risk_score: int | None = None
    zone_filter: list[str] | None = None
    target_days: float | None = None


class CandidatePatch(BaseModel):
    modified_qty: int | None = None
    list_section: str | None = None  # "MAIN" | "SUB"


class AssignRequest(BaseModel):
    worker_id: int


class UrgentWaveRequest(BaseModel):
    sku_ids: list[str] | None = None
    center_cd: str = "GGH1"
    auto_confirm: bool = True
    auto_send: bool = False
    min_risk_level: str = "HIGH"   # CRITICAL | HIGH 이상


@router.post("")
def create_wave(body: WaveCreateRequest, session: Session = Depends(get_session)) -> Any:
    max_candidates = body.max_candidates
    cutoff_info = None
    if body.wave_type == "PRESTOCK" and max_candidates is None:
        cutoff_info = calculate_prestock_cutoff(session)
        max_candidates = cutoff_info["max_sku"]

    wave = Wave(
        wave_name=body.wave_name or f"웨이브_{datetime.now().strftime('%m%d_%H%M')}",
        wave_type=body.wave_type,
        wave_status="DRAFT",
        target_sku_count=max_candidates or 40,
        created_by="관리자",
    )
    session.add(wave)
    session.commit()
    session.refresh(wave)
    logger.info("웨이브 생성", wave_id=wave.wave_id, wave_type=body.wave_type, max_candidates=max_candidates)

    write_audit_log(
        entity_type="wave",
        entity_id=wave.wave_id,
        action="created",
        actor="관리자",
        after={"wave_name": wave.wave_name, "wave_type": body.wave_type},
        session=session,
    )
    session.commit()

    algo = run_algorithm(body.center_cd, wave.wave_id, session)

    # PRESTOCK: 컷오프 초과분 자동 삭제 (risk_score 하위)
    if body.wave_type == "PRESTOCK" and max_candidates:
        all_cands = session.exec(
            select(ReplenishCandidate)
            .where(ReplenishCandidate.wave_id == wave.wave_id)
            .order_by(ReplenishCandidate.risk_score.desc())
        ).all()
        for c in all_cands[max_candidates:]:
            session.delete(c)
        session.commit()

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
        "wave_type": wave.wave_type,
        "max_candidates": max_candidates,
        "prestock_cutoff": cutoff_info,
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


@router.get("/cutoff/prestock")
def get_prestock_cutoff(session: Session = Depends(get_session)) -> Any:
    """선보충 동적 컷오프 산출값 조회 (웨이브 생성 전 미리보기용)."""
    return calculate_prestock_cutoff(session)


@router.get("")
def list_waves(session: Session = Depends(get_session)) -> Any:
    return session.exec(select(Wave).order_by(Wave.created_at.desc()).limit(50)).all()


@router.get("/{wave_id}")
def get_wave(wave_id: int, session: Session = Depends(get_session)) -> Any:
    wave = session.get(Wave, wave_id)
    if not wave:
        raise RDEException(
            code="WAVE_NOT_FOUND",
            message="웨이브를 찾을 수 없습니다.",
            detail=f"wave_id={wave_id}",
            status_code=404,
        )
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
    rows = session.exec(q.order_by(ReplenishCandidate.risk_score.desc())).all()
    result = []
    for c in rows:
        d = c.model_dump()
        try:
            d["matched_bins"] = json.loads(c.matched_bins_json or "[]")
        except json.JSONDecodeError as e:
            logger.warning("matched_bins JSON 파싱 실패", candidate_id=c.candidate_id, error=str(e))
            d["matched_bins"] = []
        result.append(d)
    return result


@router.post("/{wave_id}/candidates/{candidate_id}/approve")
def approve_candidate(wave_id: int, candidate_id: int, session: Session = Depends(get_session)) -> Any:
    try:
        return transition_candidate(candidate_id, "APPROVED", actor="관리자", session=session)
    except InvalidTransitionError as e:
        raise RDEException(code="INVALID_TRANSITION", message=str(e), status_code=400)


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
        raise RDEException(code="INVALID_TRANSITION", message=str(e), status_code=400)


@router.patch("/{wave_id}/candidates/{candidate_id}")
def update_candidate(
    wave_id: int,
    candidate_id: int,
    body: CandidatePatch,
    session: Session = Depends(get_session),
) -> Any:
    candidate = session.get(ReplenishCandidate, candidate_id)
    if not candidate:
        raise RDEException(code="CANDIDATE_NOT_FOUND", message="후보를 찾을 수 없습니다.", detail=f"candidate_id={candidate_id}", status_code=404)

    if body.list_section is not None:
        if body.list_section not in ("MAIN", "SUB"):
            raise RDEException(code="INVALID_SECTION", message="list_section은 MAIN 또는 SUB", status_code=400)
        before_section = candidate.list_section
        candidate.list_section = body.list_section
        for task in session.exec(
            select(ReplenishConfirmedTask).where(ReplenishConfirmedTask.candidate_id == candidate_id)
        ).all():
            task.list_section = body.list_section
        write_audit_log(
            entity_type="candidate",
            entity_id=candidate_id,
            action="section_change",
            actor="관리자",
            before={"list_section": before_section},
            after={"list_section": body.list_section},
            session=session,
        )

    if body.modified_qty is not None:
        try:
            return transition_candidate(
                candidate_id, "MODIFIED", actor="관리자",
                modified_qty=body.modified_qty, session=session
            )
        except InvalidTransitionError as e:
            raise RDEException(code="INVALID_TRANSITION", message=str(e), status_code=400)

    candidate.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(candidate)
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
        raise RDEException(code="CANDIDATE_NOT_FOUND", message="후보를 찾을 수 없습니다.", detail=f"candidate_id={candidate_id}", status_code=404)
    candidate.worker_id = body.worker_id
    candidate.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(candidate)
    return candidate


def _confirm_wave_internal(
    wave_id: int,
    session: Session,
    confirmed_by: str = "관리자",
) -> dict:
    """웨이브 확정 내부 로직 — 긴급 웨이브 등에서 재사용."""
    wave = session.get(Wave, wave_id)
    if not wave:
        raise RDEException(
            code="WAVE_NOT_FOUND",
            message="웨이브를 찾을 수 없습니다.",
            detail=f"wave_id={wave_id}",
            status_code=404,
        )

    candidates = session.exec(
        select(ReplenishCandidate).where(
            ReplenishCandidate.wave_id == wave_id,
            ReplenishCandidate.candidate_status.in_(["APPROVED", "MODIFIED"]),
        )
    ).all()
    if not candidates:
        raise RDEException(
            code="NO_APPROVED_CANDIDATES",
            message="승인된 후보가 없습니다.",
            detail=f"wave_id={wave_id}",
            status_code=400,
        )

    zone_cfg = {z.zone_prefix: z for z in session.exec(select(ZoneConfig)).all()}

    tasks_created = 0
    for c in candidates:
        qty = c.modified_qty if c.modified_qty else c.recommended_qty
        zc = zone_cfg.get(c.zone)
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
        session.flush()

        bins = json.loads(c.matched_bins_json or "[]")
        for seq, b in enumerate(bins, 1):
            rep_zone_pfx = extract_zone_prefix(b["replenish_bin"]) or None
            rep_zc = zone_cfg.get(rep_zone_pfx) if rep_zone_pfx else None
            session.add(ReplenishTaskLocation(
                task_id=task.task_id,
                seq=seq,
                replenish_bin=b["replenish_bin"],
                replenish_zone=rep_zc.zone_name if rep_zc else None,
                replenish_zone_prefix=rep_zone_pfx,
                allocated_qty=b["allocated_qty"],
                sales_deadline_days=b.get("deadline_days"),
                receipt_date=b.get("receipt_date"),
                proximity_score=b.get("proximity_score"),
                location_status="PENDING",
            ))

        tasks_created += 1

    wave.wave_status = "CONFIRMED"
    wave.confirmed_at = datetime.utcnow()
    write_audit_log(
        entity_type="wave",
        entity_id=wave_id,
        action="confirmed",
        actor=confirmed_by,
        after={"tasks_created": tasks_created},
        session=session,
    )
    session.commit()
    logger.info("웨이브 확정", wave_id=wave_id, tasks_created=tasks_created)
    return {"wave_id": wave_id, "tasks_created": tasks_created}


@router.post("/{wave_id}/confirm")
def confirm_wave(
    wave_id: int,
    confirmed_by: str = Query(default="관리자"),
    session: Session = Depends(get_session),
) -> Any:
    return _confirm_wave_internal(wave_id, session, confirmed_by=confirmed_by)


@router.post("/urgent-from-dashboard", status_code=201)
def create_urgent_wave_from_dashboard(
    body: UrgentWaveRequest,
    session: Session = Depends(get_session),
) -> Any:
    """대시보드 미할당/CRITICAL SKU → 즉시 긴급 웨이브 생성.

    흐름:
      1. URGENT 웨이브 생성
      2. run_algorithm으로 후보 부여
      3. risk_level/sku_ids 필터 후 나머지 삭제
      4. auto_confirm=True 면 APPROVED 처리 + 확정
    """
    wave = Wave(
        wave_name=f"긴급웨이브_{datetime.now().strftime('%m%d_%H%M')}",
        wave_type="URGENT",
        wave_status="DRAFT",
        target_sku_count=40,
        created_by="관리자",
    )
    session.add(wave)
    session.commit()
    session.refresh(wave)
    logger.info("긴급 웨이브 생성", wave_id=wave.wave_id)

    write_audit_log(
        entity_type="wave",
        entity_id=wave.wave_id,
        action="created",
        actor="관리자",
        after={"wave_name": wave.wave_name, "wave_type": "URGENT"},
        session=session,
    )
    session.commit()

    algo = run_algorithm(body.center_cd, wave.wave_id, session)

    risk_keep = {"CRITICAL"} if body.min_risk_level == "CRITICAL" else {"CRITICAL", "HIGH"}
    candidates = session.exec(
        select(ReplenishCandidate).where(ReplenishCandidate.wave_id == wave.wave_id)
    ).all()

    kept_count = 0
    for c in candidates:
        if c.risk_level not in risk_keep:
            session.delete(c)
            continue
        if body.sku_ids and c.sku_id not in body.sku_ids:
            session.delete(c)
            continue
        if body.auto_confirm:
            c.candidate_status = "APPROVED"
        kept_count += 1
    session.commit()

    confirmed = False
    tasks_created = 0
    if body.auto_confirm and kept_count > 0:
        result = _confirm_wave_internal(wave.wave_id, session)
        tasks_created = result["tasks_created"]
        confirmed = True

    session.refresh(wave)

    return {
        "wave_id": wave.wave_id,
        "wave_name": wave.wave_name,
        "candidates": kept_count,
        "confirmed": confirmed,
        "tasks_created": tasks_created,
        "algorithm": {
            "total": algo.total_candidates,
            "critical": algo.critical_count,
            "high": algo.high_count,
        },
    }


