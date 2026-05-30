import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.dependencies import get_session
from app.core.exceptions import RDEException
from app.core.logging_config import get_logger
from app.models.task import ReplenishCandidate, ReplenishConfirmedTask, ReplenishTaskLocation
from app.models.wave import Wave
from app.models.worker import Worker
from app.models.zone import ZoneConfig
from app.services.audit_service import write_audit_log
from app.services.wave_builder import run_algorithm
from app.services.csv_parser import extract_zone_prefix
from app.services.print_service import generate_print_html
from app.services.state_machine import InvalidTransitionError, transition_candidate
from app.services.verification_service import detect_done_mismatches
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


class DistributeRequest(BaseModel):
    section_size: int | None = None  # None → 활성 작업자 수 기준; >0 → 섹션당 최대 수


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


@router.post("/{wave_id}/distribute")
def distribute_wave_tasks(
    wave_id: int,
    body: DistributeRequest = Body(default=DistributeRequest()),
    session: Session = Depends(get_session),
) -> Any:
    """READY 태스크를 활성 작업자에게 균등 배분하여 section_seq / list_seq / worker_id 설정.

    - section_size 미지정: 활성 작업자(is_active=True) 수만큼 섹션 분할
    - section_size 지정: 섹션당 최대 N개로 균등 분할
    - 활성 작업자 없고 section_size 미지정: 기본 6개씩 섹션 분할
    - GAP-07: JUNIOR 작업자에게는 소량(total_qty 낮은) 태스크를 우선 배정
    """
    wave = session.get(Wave, wave_id)
    if not wave:
        raise RDEException(code="WAVE_NOT_FOUND", message="웨이브를 찾을 수 없습니다.", status_code=404)

    tasks = session.exec(
        select(ReplenishConfirmedTask)
        .where(
            ReplenishConfirmedTask.wave_id == wave_id,
            ReplenishConfirmedTask.task_status == "READY",
        )
        .order_by(ReplenishConfirmedTask.list_section, ReplenishConfirmedTask.task_id)
    ).all()

    if not tasks:
        return {"assigned": 0, "sections": {}}

    # (slack_channel, worker_type) 그룹별 태스크 분류
    groups: dict[str, list] = defaultdict(list)
    for t in tasks:
        groups[f"{t.slack_channel}|{t.worker_type}"].append(t)

    # 활성 작업자를 work_type 기준으로 분류 (GAP-07: 숙련자/JUNIOR 분리)
    active_workers = session.exec(
        select(Worker).where(Worker.is_active == True)  # noqa: E712
    ).all()
    experts_by_type: dict[str, list[Worker]] = defaultdict(list)
    juniors_by_type: dict[str, list[Worker]] = defaultdict(list)
    for w in active_workers:
        if w.skill_level == "JUNIOR":
            juniors_by_type[w.work_type].append(w)
        else:
            experts_by_type[w.work_type].append(w)

    assigned = 0
    sections_summary: dict[str, int] = {}

    for group_key, task_list in groups.items():
        channel, wtype = group_key.split("|", 1)
        experts: list[Worker] = experts_by_type.get(wtype, [])
        juniors: list[Worker] = juniors_by_type.get(wtype, [])

        # GAP-07: JUNIOR에게는 소량·단순 태스크를 우선 배정.
        # total_qty 오름차순 정렬 → 앞쪽(소량)을 JUNIOR가, 뒤쪽(대량)을 숙련자가 가져가도록
        # 작업자 순서를 [juniors..., experts...]로 구성.
        ordered_tasks = sorted(task_list, key=lambda t: t.total_qty)
        ordered_workers: list[Worker] = juniors + experts

        # 섹션 수 결정
        if body.section_size and body.section_size > 0:
            n_sec = max(1, (len(ordered_tasks) + body.section_size - 1) // body.section_size)
        elif ordered_workers:
            n_sec = len(ordered_workers)
        else:
            n_sec = max(1, (len(ordered_tasks) + 5) // 6)  # 기본 6개씩

        block = max(1, (len(ordered_tasks) + n_sec - 1) // n_sec)

        for i, task in enumerate(ordered_tasks):
            sec_idx = i // block          # 0-based
            task.section_seq = sec_idx + 1
            task.list_seq = (i % block) + 1
            if sec_idx < len(ordered_workers):
                task.worker_id = ordered_workers[sec_idx].worker_id
            session.add(task)

        sections_summary[f"{channel}_{wtype}"] = min(n_sec, (len(ordered_tasks) + block - 1) // block)
        assigned += len(ordered_tasks)

    write_audit_log(
        entity_type="wave",
        entity_id=wave_id,
        action="distributed",
        actor="관리자",
        after={"assigned": assigned, "sections": sections_summary},
        session=session,
    )
    session.commit()
    logger.info("웨이브 태스크 배분", wave_id=wave_id, assigned=assigned, sections=sections_summary)
    return {"assigned": assigned, "sections": sections_summary}


@router.get("/{wave_id}/print", response_class=HTMLResponse)
def print_wave(wave_id: int, session: Session = Depends(get_session)) -> str:
    """서버 다운 대비 인쇄용 웨이브 리스트 HTML 반환 (GAP-05).

    브라우저에서 열어 🖨️ 인쇄 버튼으로 출력하거나 PDF 저장 가능.
    섹션 배분이 완료된 경우 작업자별 섹션으로 분류됨.
    """
    return generate_print_html(wave_id, session)


@router.get("/{wave_id}/verify-done")
def verify_done_tasks(
    wave_id: int,
    center_cd: str = Query(default="GGH1"),
    session: Session = Depends(get_session),
) -> Any:
    """완료 이중검증 (GAP-04b): DONE 처리됐으나 피킹재고가 여전히 0인 태스크 탐지.

    재고 CSV 재업로드 후 호출하면, 보충 완료로 표시됐지만 실제로 피킹지번에
    재고가 반영되지 않은 불일치 건을 반환한다.
    """
    wave = session.get(Wave, wave_id)
    if not wave:
        raise RDEException(code="WAVE_NOT_FOUND", message="웨이브를 찾을 수 없습니다.", status_code=404)
    mismatches = detect_done_mismatches(center_cd, session, wave_id=wave_id)
    return {"wave_id": wave_id, "mismatch_count": len(mismatches), "mismatches": mismatches}


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


