import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlmodel import Session, select

from app.core.config import get_config, get_config_list
from app.models.inventory import ReplenishBinSnapshot
from app.models.sku import SkuPickingHistory, SkuSalesSummary
from app.models.task import ReplenishCandidate, ReplenishConfirmedTask
from app.models.upload import UploadSession
from app.models.zone import FloorAccessPoint, ScatteredAisleAnchor, ZoneConfig
from app.services.csv_parser import extract_zone_prefix, parse_bin_id


# ---------------------------------------------------------------------------
# Natural sort key (for bin_id ordering)
# ---------------------------------------------------------------------------

def nat_sort_key(s: str) -> list:
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", s or "")]


# ---------------------------------------------------------------------------
# Physical coordinate functions (v1.7)
# ---------------------------------------------------------------------------

def get_bin_coordinates(
    bin_id: str,
    zone_cfg: dict,
    aisle_anchors: dict,
) -> dict | None:
    parsed = parse_bin_id(bin_id)
    if parsed is None:
        return None
    zone = parsed["zone"]
    aisle = parsed["aisle"]
    bay = parsed["bay"]
    cfg = zone_cfg.get(zone)
    if cfg is None:
        return None
    if cfg.is_scattered:
        anchor = aisle_anchors.get((zone, aisle))
        if anchor is None:
            return None
        return {"x": anchor.anchor_x + bay * cfg.bay_gap, "y": anchor.anchor_y, "floor": anchor.floor}
    if cfg.origin_x is None:
        return None
    ox, oy = cfg.origin_x, cfg.origin_y
    if cfg.aisle_direction == "y":
        x, y = ox + bay * cfg.bay_gap, oy + aisle * cfg.aisle_gap
    else:
        x, y = ox + aisle * cfg.aisle_gap, oy + bay * cfg.bay_gap
    return {"x": x, "y": y, "floor": cfg.floor}


def travel_cost(
    coord_a: dict,
    coord_b: dict,
    access_points: list[dict],
    floor_change_penalty: float = 60.0,
) -> float:
    if coord_a["floor"] == coord_b["floor"]:
        return math.sqrt((coord_b["x"] - coord_a["x"]) ** 2 + (coord_b["y"] - coord_a["y"]) ** 2)
    flat = math.sqrt((coord_b["x"] - coord_a["x"]) ** 2 + (coord_b["y"] - coord_a["y"]) ** 2)
    if not access_points:
        return flat + floor_change_penalty
    best = float("inf")
    for ap in access_points:
        cost = (
            math.sqrt((ap["x"] - coord_a["x"]) ** 2 + (ap["y"] - coord_a["y"]) ** 2)
            + floor_change_penalty
            + math.sqrt((coord_b["x"] - ap["x"]) ** 2 + (coord_b["y"] - ap["y"]) ** 2)
        )
        best = min(best, cost)
    return best


def proximity_score(
    cost: float,
    thresh_near: float = 10.0,
    thresh_mid: float = 30.0,
    thresh_far: float = 70.0,
) -> int:
    if cost <= thresh_near:
        return 4
    if cost <= thresh_mid:
        return 3
    if cost <= thresh_far:
        return 2
    return 1


def get_proximity_score_for_bins(
    picking_bin: str,
    replen_bin: str,
    zone_cfg: dict,
    aisle_anchors: dict,
    access_points: list[dict],
    config: dict,
) -> int:
    coord_a = get_bin_coordinates(picking_bin, zone_cfg, aisle_anchors)
    coord_b = get_bin_coordinates(replen_bin, zone_cfg, aisle_anchors)
    if coord_a is None or coord_b is None:
        pa = parse_bin_id(picking_bin)
        pb = parse_bin_id(replen_bin)
        zone_a = pa["zone"] if pa else ""
        zone_b = pb["zone"] if pb else ""
        return 2 if zone_a == zone_b else 1
    cost = travel_cost(
        coord_a, coord_b, access_points,
        float(config.get("floor_change_penalty", 60)),
    )
    return proximity_score(
        cost,
        float(config.get("proximity_score_threshold_near", 10)),
        float(config.get("proximity_score_threshold_mid", 30)),
        float(config.get("proximity_score_threshold_far", 70)),
    )


# ---------------------------------------------------------------------------
# Risk scoring
# ---------------------------------------------------------------------------

def calculate_base_score(
    eta_hours: float,
    boundary_hours: list[int],
    boundary_values: list[int],
) -> int:
    for i, h in enumerate(boundary_hours):
        if eta_hours <= h:
            return boundary_values[i]
    return boundary_values[-1]


def risk_level_from_score(score: int) -> str:
    if score >= 85:
        return "CRITICAL"
    if score >= 65:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Replenishment quantity calculation
# ---------------------------------------------------------------------------

def calculate_replen_qty(
    picking_avail: int,
    adjusted_daily: float,
    replen_total_avail: int,
    unit_size: int,
    config: dict,
) -> int:
    target_days = float(config.get("target_days_default", 1.5))
    min_boxes = int(config.get("wave_default_min_boxes", 2))
    unit_size = max(unit_size, 1)

    if adjusted_daily <= 0:
        basis_a = unit_size * min_boxes
    else:
        target_stock = adjusted_daily * target_days
        needed = target_stock - picking_avail
        if needed <= 0:
            basis_a = unit_size * min_boxes
        else:
            basis_a = math.ceil(needed / unit_size) * unit_size

    basis_b = unit_size * min_boxes
    recommended = max(basis_a, basis_b)
    return max(0, min(recommended, replen_total_avail))


# ---------------------------------------------------------------------------
# FEFO bin matching
# ---------------------------------------------------------------------------

def match_replen_bins(
    picking_bin: str,
    replen_bins: list,
    needed_qty: int,
    zone_cfg: dict,
    aisle_anchors: dict,
    access_points: list[dict],
    config: dict,
    max_bins: int = 3,
) -> list[dict]:
    if not replen_bins or needed_qty <= 0:
        return []

    def sort_key(rb):
        p = get_proximity_score_for_bins(
            picking_bin, rb.replenish_bin, zone_cfg, aisle_anchors, access_points, config
        )
        can_fill = 1 if (rb.avail_qty or 0) >= needed_qty else 0
        receipt = rb.receipt_date or "9999-99-99"
        return (
            rb.deadline_days if rb.deadline_days is not None else 9999,
            -p,
            -can_fill,
            -(rb.avail_qty or 0),
            receipt,
            *nat_sort_key(rb.replenish_bin),
        )

    result, remaining = [], needed_qty
    for rb in sorted(replen_bins, key=sort_key):
        if remaining <= 0 or len(result) >= max_bins:
            break
        alloc = min(rb.avail_qty or 0, remaining)
        if alloc <= 0:
            continue
        result.append({
            "replenish_bin": rb.replenish_bin,
            "allocated_qty": alloc,
            "deadline_days": rb.deadline_days,
            "receipt_date": rb.receipt_date,
        })
        remaining -= alloc
    return result


# ---------------------------------------------------------------------------
# AlgorithmResult dataclass
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Main algorithm entry point
# ---------------------------------------------------------------------------

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
        eta_val = None if eta_hours == float("inf") else eta_hours

        if existing:
            existing.risk_score = float(score)
            existing.risk_level = level
            existing.eta_hours = eta_val
            existing.avg_daily_sales = adjusted_daily
            existing.recommended_qty = recommended_qty
            existing.reason_flags = flags_json
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
                recommended_qty=recommended_qty,
                reason_flags=flags_json,
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
