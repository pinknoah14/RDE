import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, date as date_type

from sqlmodel import Session, select

from app.core.config import get_config, get_config_list
from app.models.inventory import ReplenishBinSnapshot
from app.models.sku import SkuPickingHistory, SkuSalesSummary, DailySalesHistory
from app.models.task import ReplenishCandidate, ReplenishConfirmedTask
from app.models.upload import UploadSession
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


def run_algorithm(center_cd: str, wave_id: int, session: Session) -> AlgorithmResult:
    start = datetime.utcnow()

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

    result.no_replen_skus = no_replen
    result.new_skus = list(new_skus_seen)
    result.multi_bin_skus = list(multi_bin_seen)
    result.execution_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
    return result
