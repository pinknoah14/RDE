import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, date as date_type

from sqlmodel import Session, select

from app.core.config import get_config, get_config_list
from app.core.logging_config import get_logger
from app.models.inventory import ReplenishBinSnapshot
from app.models.sku import SkuPickingHistory, SkuSalesSummary, DailySalesHistory
from app.models.task import ReplenishCandidate, ReplenishConfirmedTask
from app.models.upload import UploadSession
from app.models.worker import Worker
from app.models.zone import FloorAccessPoint, ScatteredAisleAnchor, ZoneConfig
from app.services.csv_parser import extract_zone_prefix
from app.services.algorithm import (
    nat_sort_key,
    get_bin_coordinates,
    travel_cost,
    proximity_score,
    get_proximity_score_for_bins,
    calculate_base_score,
    risk_level_from_score,
    calculate_replen_qty,
    match_replen_bins,
)


@dataclass
class AlgorithmResult:
    total_candidates: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    no_replen_skus: list[str] = field(default_factory=list)
    new_skus: list[str] = field(default_factory=list)
    multi_bin_skus: list[str] = field(default_factory=list)
    execution_ms: int = 0


logger = get_logger("algorithm")


def run_algorithm(center_cd: str, wave_id: int, session: Session) -> AlgorithmResult:
    start = datetime.utcnow()
    logger.info("알고리즘 실행 시작", center_cd=center_cd, wave_id=wave_id)

    zone_cfg = {z.zone_prefix: z for z in session.exec(select(ZoneConfig)).all()}
    aisle_anchors = {
        (a.zone_prefix, a.aisle_no): a
        for a in session.exec(select(ScatteredAisleAnchor)).all()
    }
    access_pts = [
        {"x": ap.x, "y": ap.y}
        for ap in session.exec(
            select(FloorAccessPoint).where(FloorAccessPoint.is_active == True)  # noqa: E712
        ).all()
    ]

    cfg_keys = [
        "operating_hours_per_day", "expiry_warning_days", "expiry_critical_days",
        "weight_expiry", "weight_expiry_critical", "weight_unassigned",
        "weight_new_sku", "weight_event_active", "weight_replenishing_now",
        "weight_prev_blocked", "target_days_default", "wave_default_min_boxes",
        "max_replen_bins", "floor_change_penalty",
        "proximity_score_threshold_near", "proximity_score_threshold_mid",
        "proximity_score_threshold_far",
    ]
    config: dict[str, str] = {}
    for k in cfg_keys:
        try:
            config[k] = get_config(k, session)
        except KeyError:
            pass

    boundary_hours = get_config_list("score_boundary_hours", session, cast=int)
    boundary_values = get_config_list("score_boundary_values", session, cast=int)
    op_hours = int(config.get("operating_hours_per_day", 16))
    max_bins = int(config.get("max_replen_bins", 3))

    picking_histories = session.exec(
        select(SkuPickingHistory).where(
            SkuPickingHistory.center_cd == center_cd,
            SkuPickingHistory.picking_bin.is_not(None),
        )
    ).all()

    latest_upload = session.exec(
        select(UploadSession)
        .where(
            UploadSession.center_cd == center_cd,
            UploadSession.upload_type == "INVENTORY",
        )
        .order_by(UploadSession.uploaded_at.desc())
    ).first()

    replenish_by_sku: dict[str, list] = {}
    if latest_upload:
        for rb in session.exec(
            select(ReplenishBinSnapshot).where(
                ReplenishBinSnapshot.upload_session_id == latest_upload.upload_id,
            )
        ).all():
            if rb.deadline_days is None or rb.deadline_days > 0:
                replenish_by_sku.setdefault(rb.sku_id, []).append(rb)

    result = AlgorithmResult()
    no_replen: list[str] = []
    new_skus_seen: set[str] = set()
    multi_bin_seen: set[str] = set()

    for history in picking_histories:
        sku_id = history.sku_id
        picking_avail = history.last_seen_qty or 0

        sales = session.exec(
            select(SkuSalesSummary).where(
                SkuSalesSummary.sku_id == sku_id,
                SkuSalesSummary.center_cd == center_cd,
            )
        ).first()

        adjusted_daily = (sales.adjusted_daily if sales else 0.0) or 0.0
        eta_hours = (picking_avail / adjusted_daily) * op_hours if adjusted_daily > 0 else float("inf")

        base_score = calculate_base_score(eta_hours, boundary_hours, boundary_values)
        score = base_score
        flags: list[str] = []

        replen_bins = replenish_by_sku.get(sku_id, [])
        min_deadline = min((rb.deadline_days for rb in replen_bins if rb.deadline_days is not None), default=None)
        if min_deadline is not None:
            if min_deadline <= int(config.get("expiry_critical_days", 7)):
                score += int(config.get("weight_expiry_critical", 20))
                flags.append("유통기한위급")
            elif min_deadline <= int(config.get("expiry_warning_days", 30)):
                score += int(config.get("weight_expiry", 10))
                flags.append("유통기한주의")

        if sales and sales.stockout_flag:
            score += int(config.get("weight_unassigned", 15))
            flags.append("미할당")

        if history.is_new_sku:
            score += int(config.get("weight_new_sku", 5))
            flags.append("신규SKU")
            new_skus_seen.add(sku_id)

        if sales and sales.event_flag:
            score += int(config.get("weight_event_active", 10))
            flags.append("이벤트진행")

        prev_blocked = session.exec(
            select(ReplenishConfirmedTask).where(
                ReplenishConfirmedTask.sku_id == sku_id,
                ReplenishConfirmedTask.task_status == "BLOCKED",
            )
        ).first()
        if prev_blocked:
            score += int(config.get("weight_prev_blocked", 5))
            flags.append("BLOCKED이력")

        if history.has_multi_bin:
            multi_bin_seen.add(sku_id)

        score = max(0, min(100, score))
        level = risk_level_from_score(score)

        replen_total = sum(rb.avail_qty or 0 for rb in replen_bins)
        unit_size = replen_bins[0].unit_size if replen_bins else 1
        recommended_qty = calculate_replen_qty(picking_avail, adjusted_daily, replen_total, unit_size, config)

        if recommended_qty <= 0:
            no_replen.append(sku_id)
            continue

        matched = match_replen_bins(
            history.picking_bin, replen_bins, recommended_qty,
            zone_cfg, aisle_anchors, access_pts, config, max_bins,
        )
        if not matched:
            no_replen.append(sku_id)
            continue

        for m in matched:
            m["proximity_score"] = get_proximity_score_for_bins(
                history.picking_bin, m["replenish_bin"],
                zone_cfg, aisle_anchors, access_pts, config,
            )

        zone_pfx = extract_zone_prefix(history.picking_bin or "")
        zc = zone_cfg.get(zone_pfx)
        slack_channel = zc.slack_channel if zc else ""
        list_section = zc.list_section if zc else "MAIN"

        existing = session.exec(
            select(ReplenishCandidate).where(
                ReplenishCandidate.wave_id == wave_id,
                ReplenishCandidate.sku_id == sku_id,
            )
        ).first()

        flags_json = json.dumps(flags, ensure_ascii=False)
        matched_bins_json = json.dumps(matched, ensure_ascii=False)
        eta_val = None if eta_hours == float("inf") else eta_hours

        today_row = session.exec(
            select(DailySalesHistory).where(
                DailySalesHistory.sku_id == sku_id,
                DailySalesHistory.center_cd == center_cd,
                DailySalesHistory.sales_date == date_type.today(),
            )
        ).first()
        today_sales = today_row.sales_qty if today_row else 0

        if existing:
            existing.risk_score = float(score)
            existing.risk_level = level
            existing.eta_hours = eta_val
            existing.avg_daily_sales = adjusted_daily
            existing.today_sales = today_sales
            existing.recommended_qty = recommended_qty
            existing.reason_flags = flags_json
            existing.matched_bins_json = matched_bins_json
            existing.updated_at = datetime.utcnow()
        else:
            session.add(ReplenishCandidate(
                wave_id=wave_id,
                sku_id=sku_id,
                sku_name=(sales.sku_name or sku_id) if sales else sku_id,
                picking_bin=history.picking_bin,
                picking_confidence=history.confidence,
                zone=zone_pfx,
                slack_channel=slack_channel,
                list_section=list_section,
                risk_score=float(score),
                risk_level=level,
                eta_hours=eta_val,
                avg_daily_sales=adjusted_daily,
                today_sales=today_sales,
                recommended_qty=recommended_qty,
                reason_flags=flags_json,
                matched_bins_json=matched_bins_json,
            ))

        result.total_candidates += 1
        if level == "CRITICAL":
            result.critical_count += 1
        elif level == "HIGH":
            result.high_count += 1
        elif level == "MEDIUM":
            result.medium_count += 1
        else:
            result.low_count += 1

    session.commit()

    # 후처리: 배치 태그 부여 (혼적 파렛트 묶음)
    try:
        min_group = int(get_config("batch_tag_min_group", session) or 2)
    except KeyError:
        min_group = 2
    apply_batch_tags_to_wave(wave_id, session, min_group=min_group)

    result.no_replen_skus = no_replen
    result.new_skus = list(new_skus_seen)
    result.multi_bin_skus = list(multi_bin_seen)
    result.execution_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
    logger.info(
        "알고리즘 실행 완료",
        wave_id=wave_id,
        total=result.total_candidates,
        critical=result.critical_count,
        high=result.high_count,
        execution_ms=result.execution_ms,
    )
    return result


def assign_batch_tags(candidates: list[dict], min_group: int = 2) -> list[dict]:
    """
    동일 1순위 보충지번을 공유하는 SKU 그룹에 배치 태그 부여.

    규칙:
    - matched_bins[0].replenish_bin 기준으로 그룹화
    - min_group 이상 공유할 때만 태그 부여 (단독은 NULL)
    - FEFO 정렬(matched_bins) 절대 변경 금지 — 행에 태그만 추가
    - batch_seq는 risk_score 내림차순 기준
    """
    bin_groups: dict[str, list] = defaultdict(list)

    for c in candidates:
        bins = c.get("matched_bins") or []
        if not bins:
            continue
        primary_bin = bins[0].get("replenish_bin")
        if primary_bin:
            bin_groups[primary_bin].append(c)

    for replen_bin, group in bin_groups.items():
        if len(group) < min_group:
            continue
        sorted_group = sorted(group, key=lambda x: x.get("risk_score", 0), reverse=True)
        for seq, c in enumerate(sorted_group, 1):
            c["batch_tag"] = replen_bin
            c["batch_seq"] = seq

    return candidates


def apply_batch_tags_to_wave(wave_id: int, session: Session, min_group: int = 2) -> int:
    """웨이브 후보 전체에 배치 태그 부여 후 DB 업데이트. 반환: 태그 부여된 후보 수."""
    candidates = session.exec(
        select(ReplenishCandidate).where(ReplenishCandidate.wave_id == wave_id)
    ).all()

    # candidate_id → ORM 객체 맵
    cand_map = {c.candidate_id: c for c in candidates}

    # 기존 태그 초기화 (재실행 시 누적 방지)
    for c in candidates:
        c.batch_tag = None
        c.batch_seq = None

    # dict 변환 (matched_bins 파싱)
    dicts = []
    for c in candidates:
        try:
            bins = json.loads(c.matched_bins_json or "[]")
        except json.JSONDecodeError as e:
            logger.warning("matched_bins JSON 파싱 실패", candidate_id=c.candidate_id, error=str(e))
            bins = []
        dicts.append({
            "candidate_id": c.candidate_id,
            "risk_score": c.risk_score,
            "matched_bins": bins,
        })

    tagged_dicts = assign_batch_tags(dicts, min_group=min_group)

    tagged_count = 0
    for d in tagged_dicts:
        if d.get("batch_tag"):
            cand = cand_map.get(d["candidate_id"])
            if cand:
                cand.batch_tag = d["batch_tag"]
                cand.batch_seq = d["batch_seq"]
                tagged_count += 1

    session.commit()
    return tagged_count


def calculate_prestock_cutoff(session: Session) -> dict:
    """
    선보충 웨이브 최대 처리 SKU 수 동적 산출.
    active_workers(work_type=FORKLIFT) × uph × (minutes / 60)
    """
    try:
        uph = int(get_config("prestock_uph", session) or 12)
    except KeyError:
        uph = 12
    try:
        minutes = int(get_config("prestock_minutes", session) or 100)
    except KeyError:
        minutes = 100

    active_workers = session.exec(
        select(Worker).where(
            Worker.is_active == True,  # noqa: E712
            Worker.work_type == "FORKLIFT",
        )
    ).all()
    active_count = len(active_workers)

    if active_count == 0:
        max_sku = 40  # 기본 폴백
    else:
        max_sku = max(1, int(active_count * uph * (minutes / 60)))

    return {
        "active_workers": active_count,
        "uph": uph,
        "minutes": minutes,
        "max_sku": max_sku,
    }
