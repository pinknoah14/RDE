import math
import re

from app.services.csv_parser import parse_bin_id


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


from app.services.wave_builder import AlgorithmResult, run_algorithm  # noqa: F401
