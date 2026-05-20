import pytest
from sqlmodel import select

from app.services.algorithm import (
    AlgorithmResult,
    calculate_base_score,
    calculate_replen_qty,
    get_bin_coordinates,
    get_proximity_score_for_bins,
    match_replen_bins,
    proximity_score,
    risk_level_from_score,
    run_algorithm,
    travel_cost,
)
from app.models.zone import ZoneConfig, ScatteredAisleAnchor, FloorAccessPoint
from app.models.sku import SkuPickingHistory, SkuSalesSummary
from app.models.inventory import ReplenishBinSnapshot
from app.models.upload import UploadSession
from app.models.wave import Wave


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_zone(session, prefix="RA", origin_x=0.0, origin_y=0.0, floor=0, is_scattered=False, gap=3.0, bay_gap=1.5):
    from sqlmodel import select
    existing = session.exec(select(ZoneConfig).where(ZoneConfig.zone_prefix == prefix)).first()
    if existing:
        return existing
    z = ZoneConfig(
        zone_prefix=prefix, zone_name=f"{prefix}존",
        slack_channel=f"#{prefix.lower()}", access_type="FORKLIFT", list_section="MAIN",
        floor=floor, is_scattered=is_scattered,
        origin_x=origin_x, origin_y=origin_y,
        aisle_direction="y", aisle_gap=gap, bay_gap=bay_gap,
    )
    session.add(z)
    session.commit()
    return z


def make_picking_history(session, sku_id="SKU001", picking_bin="15RA0101001", avail_qty=100):
    h = SkuPickingHistory(
        sku_id=sku_id, center_cd="GGH1",
        picking_bin=picking_bin, zone="RA",
        last_seen_qty=avail_qty, confidence="HIGH",
    )
    session.add(h)
    session.commit()
    return h


def make_replenish_snapshot(session, upload_id, sku_id="SKU001", replenish_bin="15RA0201001",
                            avail_qty=200, deadline_days=30, unit_size=12):
    s = ReplenishBinSnapshot(
        upload_session_id=upload_id, center_cd="GGH1",
        sku_id=sku_id, replenish_bin=replenish_bin,
        avail_qty=avail_qty, unit_size=unit_size, deadline_days=deadline_days,
    )
    session.add(s)
    session.commit()
    return s


def make_upload_session(session, center_cd="GGH1"):
    from datetime import datetime
    u = UploadSession(
        upload_type="INVENTORY", file_name="test.csv",
        uploaded_by="테스트", uploaded_at=datetime.utcnow(),
        center_cd=center_cd,
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def make_wave(session, center_cd="GGH1"):
    w = Wave(wave_name="테스트웨이브", wave_type="REGULAR", wave_status="DRAFT",
             target_sku_count=10, created_by="테스트")
    session.add(w)
    session.commit()
    session.refresh(w)
    return w


# ---------------------------------------------------------------------------
# Base score
# ---------------------------------------------------------------------------

class TestCalculateBaseScore:
    HOURS = [0, 1, 2, 4, 6, 8]
    VALUES = [100, 90, 75, 55, 35, 15, 0]

    def test_zero_hours(self):
        assert calculate_base_score(0, self.HOURS, self.VALUES) == 100

    def test_within_boundary(self):
        assert calculate_base_score(1.5, self.HOURS, self.VALUES) == 75

    def test_beyond_all_boundaries(self):
        assert calculate_base_score(100, self.HOURS, self.VALUES) == 0

    def test_exact_boundary(self):
        assert calculate_base_score(4, self.HOURS, self.VALUES) == 55


class TestRiskLevel:
    def test_critical(self):
        assert risk_level_from_score(90) == "CRITICAL"

    def test_high(self):
        assert risk_level_from_score(65) == "HIGH"

    def test_medium(self):
        assert risk_level_from_score(40) == "MEDIUM"

    def test_low(self):
        assert risk_level_from_score(39) == "LOW"


# ---------------------------------------------------------------------------
# Replenishment quantity
# ---------------------------------------------------------------------------

class TestCalculateReplenQty:
    CONFIG = {"target_days_default": "1.5", "wave_default_min_boxes": "2"}

    def test_basic(self):
        qty = calculate_replen_qty(0, 10.0, 300, 12, self.CONFIG)
        # target = 10 * 1.5 = 15, needed = 15-0 = 15, ceil(15/12)*12 = 24
        # min_boxes basis = 2*12 = 24; max(24,24)=24; min(24,300)=24
        assert qty == 24

    def test_capped_by_available(self):
        qty = calculate_replen_qty(0, 100.0, 10, 12, self.CONFIG)
        assert qty == 10

    def test_zero_sales_uses_min_boxes(self):
        qty = calculate_replen_qty(0, 0.0, 100, 12, self.CONFIG)
        assert qty == 24

    def test_sufficient_stock_uses_min_boxes(self):
        # picking_avail far exceeds target → still applies min_boxes
        qty = calculate_replen_qty(9999, 1.0, 200, 12, self.CONFIG)
        assert qty == 24


# ---------------------------------------------------------------------------
# Physical coordinate
# ---------------------------------------------------------------------------

class TestGetBinCoordinates:
    def _zone_cfg(self, origin_x=0.0, origin_y=0.0, floor=0, is_scattered=False):
        from unittest.mock import MagicMock
        z = MagicMock()
        z.is_scattered = is_scattered
        z.origin_x = origin_x
        z.origin_y = origin_y
        z.aisle_direction = "y"
        z.aisle_gap = 3.0
        z.bay_gap = 1.5
        z.floor = floor
        return z

    def test_continuous_zone(self):
        zone_cfg = {"RA": self._zone_cfg(origin_x=0.0, origin_y=0.0)}
        # 15RA0101001: aisle=1, bay=1
        coord = get_bin_coordinates("15RA0101001", zone_cfg, {})
        assert coord is not None
        assert coord["x"] == pytest.approx(1 * 1.5)   # bay=1 * bay_gap=1.5
        assert coord["y"] == pytest.approx(1 * 3.0)   # aisle=1 * aisle_gap=3.0
        assert coord["floor"] == 0

    def test_invalid_bin_returns_none(self):
        zone_cfg = {"RA": self._zone_cfg()}
        assert get_bin_coordinates("PKMOVE01", zone_cfg, {}) is None

    def test_unregistered_zone_returns_none(self):
        assert get_bin_coordinates("15ZZ0101001", {}, {}) is None

    def test_no_origin_returns_none(self):
        z = self._zone_cfg()
        z.origin_x = None
        assert get_bin_coordinates("15RA0101001", {"RA": z}, {}) is None

    def test_scattered_zone(self):
        from unittest.mock import MagicMock
        z = MagicMock()
        z.is_scattered = True
        z.bay_gap = 1.5
        zone_cfg = {"PW": z}
        anchor = MagicMock()
        anchor.anchor_x = 10.0
        anchor.anchor_y = 20.0
        anchor.floor = 1
        aisle_anchors = {("PW", 3): anchor}
        # 15PW0301001: aisle=3, bay=1
        coord = get_bin_coordinates("15PW0301001", zone_cfg, aisle_anchors)
        assert coord is not None
        assert coord["x"] == pytest.approx(10.0 + 1 * 1.5)
        assert coord["floor"] == 1


class TestTravelCost:
    def test_same_floor(self):
        a = {"x": 0, "y": 0, "floor": 0}
        b = {"x": 3, "y": 4, "floor": 0}
        assert travel_cost(a, b, []) == pytest.approx(5.0)

    def test_different_floor_no_access_points(self):
        a = {"x": 0, "y": 0, "floor": 0}
        b = {"x": 3, "y": 4, "floor": 1}
        cost = travel_cost(a, b, [], floor_change_penalty=60.0)
        assert cost == pytest.approx(5.0 + 60.0)

    def test_different_floor_with_access_point(self):
        a = {"x": 0, "y": 0, "floor": 0}
        b = {"x": 0, "y": 0, "floor": 1}
        stairs = [{"x": 5, "y": 0}]
        cost = travel_cost(a, b, stairs, floor_change_penalty=60.0)
        # dist(a→stairs) = 5, penalty=60, dist(stairs→b) = 5 → total = 70
        assert cost == pytest.approx(70.0)


class TestProximityScore:
    def test_near(self):
        assert proximity_score(5.0) == 4

    def test_mid(self):
        assert proximity_score(20.0) == 3

    def test_far(self):
        assert proximity_score(50.0) == 2

    def test_very_far(self):
        assert proximity_score(100.0) == 1


class TestGetProximityScoreForBins:
    def test_fallback_same_zone(self):
        # No coordinates → zone code fallback
        score = get_proximity_score_for_bins("15RA0101001", "15RA0201001", {}, {}, [], {})
        assert score == 2

    def test_fallback_different_zone(self):
        score = get_proximity_score_for_bins("15RA0101001", "15RB0201001", {}, {}, [], {})
        assert score == 1


# ---------------------------------------------------------------------------
# FEFO bin matching
# ---------------------------------------------------------------------------

class TestMatchReplenBins:
    def _make_rb(self, bin_id, avail_qty, deadline_days, unit_size=12):
        from unittest.mock import MagicMock
        rb = MagicMock()
        rb.replenish_bin = bin_id
        rb.avail_qty = avail_qty
        rb.deadline_days = deadline_days
        rb.unit_size = unit_size
        rb.receipt_date = None
        return rb

    def test_fefo_ordering(self):
        bins = [
            self._make_rb("15RA0201001", 200, deadline_days=50),
            self._make_rb("15RA0202001", 200, deadline_days=10),
        ]
        result = match_replen_bins("15RA0101001", bins, 24, {}, {}, [], {})
        assert result[0]["replenish_bin"] == "15RA0202001"  # smaller deadline first

    def test_single_bin_fills_completely(self):
        bins = [self._make_rb("15RA0201001", 200, deadline_days=30)]
        result = match_replen_bins("15RA0101001", bins, 24, {}, {}, [], {})
        assert len(result) == 1
        assert result[0]["allocated_qty"] == 24

    def test_multi_bin_allocation(self):
        bins = [
            self._make_rb("15RA0201001", 10, deadline_days=10),
            self._make_rb("15RA0202001", 20, deadline_days=20),
        ]
        result = match_replen_bins("15RA0101001", bins, 25, {}, {}, [], {})
        total = sum(r["allocated_qty"] for r in result)
        assert total == 25

    def test_empty_bins_returns_empty(self):
        assert match_replen_bins("15RA0101001", [], 24, {}, {}, [], {}) == []

    def test_max_bins_respected(self):
        bins = [self._make_rb(f"15RA02{i:02d}001", 5, deadline_days=i) for i in range(1, 10)]
        result = match_replen_bins("15RA0101001", bins, 99, {}, {}, [], {}, max_bins=3)
        assert len(result) <= 3


# ---------------------------------------------------------------------------
# run_algorithm integration test
# ---------------------------------------------------------------------------

class TestRunAlgorithm:
    def test_no_data_returns_empty(self, session):
        wave = make_wave(session)
        result = run_algorithm("GGH1", wave.wave_id, session)
        assert result.total_candidates == 0

    def test_creates_candidates(self, session):
        make_zone(session, prefix="RA", origin_x=0, origin_y=0)
        upload = make_upload_session(session)
        make_picking_history(session, sku_id="SKU001", picking_bin="15RA0101001", avail_qty=0)
        make_replenish_snapshot(session, upload.upload_id, sku_id="SKU001",
                                replenish_bin="15RA0201001", avail_qty=100, deadline_days=30)

        sales = SkuSalesSummary(
            sku_id="SKU001", center_cd="GGH1",
            base_daily_avg=10.0, recent_daily_avg=10.0,
            trend_coef=1.0, adjusted_daily=10.0,
        )
        session.add(sales)
        session.commit()

        wave = make_wave(session)
        result = run_algorithm("GGH1", wave.wave_id, session)
        assert result.total_candidates >= 1

    def test_expired_replenish_excluded(self, session):
        make_zone(session, prefix="RA", origin_x=0, origin_y=0)
        upload = make_upload_session(session)
        make_picking_history(session, sku_id="SKU_EXP", picking_bin="15RA0101001", avail_qty=0)
        # deadline_days=0 → should be excluded from snapshot in upload flow
        # but we filter in run_algorithm too
        make_replenish_snapshot(session, upload.upload_id, sku_id="SKU_EXP",
                                replenish_bin="15RA0201001", avail_qty=100, deadline_days=0)

        wave = make_wave(session)
        result = run_algorithm("GGH1", wave.wave_id, session)
        assert "SKU_EXP" in result.no_replen_skus or result.total_candidates == 0
