"""
Phase 2 검증 Step 3: 핵심 로직 시나리오 테스트
(설계서 검증 프롬프트 §3-1 ~ §3-7)
"""
import math
import pytest
from unittest.mock import MagicMock
from datetime import datetime

from app.services.csv_parser import parse_bin_id
from app.services.algorithm import (
    travel_cost,
    proximity_score,
    get_proximity_score_for_bins,
    match_replen_bins,
    run_algorithm,
)
from app.models.config import SystemConfig
from app.models.zone import ZoneConfig
from app.models.sku import SkuPickingHistory, SkuSalesSummary
from app.models.inventory import ReplenishBinSnapshot
from app.models.upload import UploadSession
from app.models.wave import Wave


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rb(bin_id, avail_qty, deadline_days, unit_size=12):
    rb = MagicMock()
    rb.replenish_bin = bin_id
    rb.avail_qty = avail_qty
    rb.deadline_days = deadline_days
    rb.unit_size = unit_size
    rb.receipt_date = None
    return rb


def _setup_wave_with_sku(session, sku_id="SKU_W", avail_qty=0,
                          replen_qty=100, deadline_days=30):
    from sqlmodel import select
    existing = session.exec(select(ZoneConfig).where(ZoneConfig.zone_prefix == "RA")).first()
    if not existing:
        session.add(ZoneConfig(
            zone_prefix="RA", zone_name="RA존", slack_channel="#ra",
            access_type="FORKLIFT", list_section="MAIN",
            origin_x=0.0, origin_y=0.0,
        ))
    u = UploadSession(
        upload_type="INVENTORY", file_name="test.csv",
        uploaded_by="테스트", uploaded_at=datetime.utcnow(), center_cd="GGH1",
    )
    session.add(u)
    session.commit()
    session.refresh(u)

    session.add(SkuPickingHistory(
        sku_id=sku_id, center_cd="GGH1",
        picking_bin="15RA0101001", zone="RA",
        last_seen_qty=avail_qty, confidence="HIGH",
    ))
    session.add(ReplenishBinSnapshot(
        upload_session_id=u.upload_id, center_cd="GGH1",
        sku_id=sku_id, replenish_bin="15RA0201001",
        avail_qty=replen_qty, unit_size=12, deadline_days=deadline_days,
    ))
    session.add(SkuSalesSummary(
        sku_id=sku_id, center_cd="GGH1",
        base_daily_avg=10.0, recent_daily_avg=10.0,
        trend_coef=1.0, adjusted_daily=10.0,
    ))
    session.commit()

    w = Wave(wave_name="검증웨이브", wave_type="REGULAR", wave_status="DRAFT",
             target_sku_count=10, created_by="테스트")
    session.add(w)
    session.commit()
    session.refresh(w)
    return w


# ---------------------------------------------------------------------------
# 3-1. parse_bin_id() 파싱 정확도
# ---------------------------------------------------------------------------

class TestParseBinIdFull:
    def test_standard_bin_full(self):
        r = parse_bin_id("15RA1402201")
        assert r["zone"] == "RA"
        assert r["aisle"] == 14
        assert r["bay"] == 2
        assert r["level"] == 201

    def test_pw_zone_not_subzone(self):
        r = parse_bin_id("15PW0301001")
        assert r["zone"] == "PW"    # NOT "PW03"
        assert r["aisle"] == 3
        assert r["bay"] == 1

    def test_hold_bin_pkmove_is_none(self):
        assert parse_bin_id("PKMOVE01") is None

    def test_hold_bin_stopwaste_is_none(self):
        assert parse_bin_id("STOPWASTEH") is None

    def test_empty_string_is_none(self):
        assert parse_bin_id("") is None

    def test_none_is_none(self):
        assert parse_bin_id(None) is None


# ---------------------------------------------------------------------------
# 3-2. travel_cost() 이동 비용 계산
# ---------------------------------------------------------------------------

class TestTravelCostScenarios:
    def test_same_floor_345(self):
        a = {"x": 0.0, "y": 0.0, "floor": 0}
        b = {"x": 3.0, "y": 4.0, "floor": 0}
        assert abs(travel_cost(a, b, [], 60.0) - 5.0) < 0.01

    def test_different_floor_no_access_zero_xy(self):
        a = {"x": 0.0, "y": 0.0, "floor": 0}
        b = {"x": 0.0, "y": 0.0, "floor": 1}
        assert abs(travel_cost(a, b, [], 60.0) - 60.0) < 0.01

    def test_different_floor_with_access_via_midpoint(self):
        a = {"x": 0.0, "y": 0.0, "floor": 0}
        b = {"x": 20.0, "y": 0.0, "floor": 1}
        access_points = [{"x": 10.0, "y": 0.0}]
        cost = travel_cost(a, b, access_points, 60.0)
        assert abs(cost - 80.0) < 0.01  # 10 + 60 + 10

    def test_picks_closest_staircase(self):
        a = {"x": 0.0, "y": 0.0, "floor": 0}
        b = {"x": 100.0, "y": 0.0, "floor": 1}
        ap_near = {"x": 5.0, "y": 0.0}
        ap_far  = {"x": 95.0, "y": 0.0}
        cost_near = travel_cost(a, b, [ap_near], 60.0)
        cost_far  = travel_cost(a, b, [ap_far],  60.0)
        cost_both = travel_cost(a, b, [ap_near, ap_far], 60.0)
        assert cost_both == min(cost_near, cost_far)


# ---------------------------------------------------------------------------
# 3-3. proximity_score() 임계값 경계값
# ---------------------------------------------------------------------------

class TestProximityScoreBoundaries:
    def test_zero_meters(self):
        assert proximity_score(0.0, 10, 30, 70) == 4

    def test_exactly_near_threshold(self):
        assert proximity_score(10.0, 10, 30, 70) == 4

    def test_just_above_near_threshold(self):
        assert proximity_score(10.1, 10, 30, 70) == 3

    def test_exactly_mid_threshold(self):
        assert proximity_score(30.0, 10, 30, 70) == 3

    def test_just_above_mid_threshold(self):
        assert proximity_score(30.1, 10, 30, 70) == 2

    def test_exactly_far_threshold(self):
        assert proximity_score(70.0, 10, 30, 70) == 2

    def test_just_above_far_threshold(self):
        assert proximity_score(70.1, 10, 30, 70) == 1

    def test_extreme_distance_is_1_not_0(self):
        assert proximity_score(999.0, 10, 30, 70) == 1


# ---------------------------------------------------------------------------
# 3-4. get_proximity_score_for_bins() 폴백 동작
# ---------------------------------------------------------------------------

class TestProximityScoreFallback:
    def test_fallback_same_zone_no_coords(self):
        score = get_proximity_score_for_bins(
            "15RA0101001", "15RA0501003",
            zone_cfg={}, aisle_anchors={}, access_points=[], config={}
        )
        assert score == 2

    def test_fallback_different_zone_no_coords(self):
        score = get_proximity_score_for_bins(
            "15RA0101001", "15RB0501003",
            zone_cfg={}, aisle_anchors={}, access_points=[], config={}
        )
        assert score == 1

    def test_scattered_zone_partial_anchor(self):
        z = MagicMock()
        z.is_scattered = True
        z.bay_gap = 1.5
        zone_cfg = {"PW": z}
        aisle_anchors = {
            ("PW", 1): MagicMock(anchor_x=10.0, anchor_y=10.0, floor=0),
            # 통로 3은 미설정
        }
        score_unset = get_proximity_score_for_bins(
            "15RA0101001", "15PW0301001",
            zone_cfg=zone_cfg, aisle_anchors=aisle_anchors,
            access_points=[], config={}
        )
        assert score_unset in [1, 2]  # 폴백, 오류 없음

    def test_hold_bin_as_picking_bin_falls_back(self):
        score = get_proximity_score_for_bins(
            "PKMOVE01", "15RA0101001",
            zone_cfg={}, aisle_anchors={}, access_points=[], config={}
        )
        assert score in [1, 2]

    def test_mixed_floor_lowers_score(self):
        def _z(floor):
            m = MagicMock()
            m.is_scattered = False
            m.floor = floor
            m.origin_x = 0.0
            m.origin_y = 0.0
            m.aisle_direction = "y"
            m.aisle_gap = 3.0
            m.bay_gap = 1.5
            return m

        zone_cfg = {"RA": _z(0), "SF": _z(1)}
        score = get_proximity_score_for_bins(
            "15RA0101001", "15SF0101001",
            zone_cfg=zone_cfg, aisle_anchors={},
            access_points=[{"x": 5.0, "y": 5.0}],
            config={"floor_change_penalty": "60"},
        )
        assert score <= 2  # 층 이동이 있으므로 score 낮음


# ---------------------------------------------------------------------------
# 3-5. FEFO + proximity_score 복합 정렬 (match_replen_bins 경유)
# ---------------------------------------------------------------------------

class TestFefoProximitySort:
    def test_fefo_beats_proximity(self):
        """FEFO가 proximity_score보다 항상 우선"""
        bins = [
            _make_rb("15RA0201001", 100, deadline_days=100),  # 가깝지만 유통기한 김
            _make_rb("15RB1001001", 100, deadline_days=10),   # 멀지만 유통기한 임박
        ]
        result = match_replen_bins("15RA0101001", bins, 24, {}, {}, [], {})
        assert result[0]["replenish_bin"] == "15RB1001001"  # 임박 먼저

    def test_same_deadline_proximity_wins(self):
        """마감일 동일 시 proximity 높은 것 우선"""
        z = MagicMock()
        z.is_scattered = False
        z.floor = 0
        z.origin_x = 0.0
        z.origin_y = 0.0
        z.aisle_direction = "y"
        z.aisle_gap = 3.0
        z.bay_gap = 1.5
        zone_cfg = {"RA": z}

        bins = [
            _make_rb("15RA0201001", 100, deadline_days=30),  # 통로2 (가까움)
            _make_rb("15RA1001001", 100, deadline_days=30),  # 통로10 (멀리)
        ]
        result = match_replen_bins("15RA0101001", bins, 24, zone_cfg, {}, [], {})
        # 마감일 동일 → proximity 높은 (가까운 통로2) 먼저
        assert result[0]["replenish_bin"] == "15RA0201001"


# ---------------------------------------------------------------------------
# 3-6. 판매마감일수 <= 0 필터 (csv_parser classify_inventory 경유)
# ---------------------------------------------------------------------------

class TestExpiredReplenishFilter:
    def _make_df(self, deadlines: list):
        import polars as pl
        n = len(deadlines)
        return pl.DataFrame({
            "지번":        [f"15RA0{i+1}01001" for i in range(n)],
            "상품코드":    ["A"] * n,
            "SKU명":       ["A상품"] * n,
            "센터":        ["GGH1"] * n,
            "피킹가능":    ["피킹불가"] * n,
            "가용수량":    [10] * n,
            "단위수량":    [12] * n,
            "판매마감일수": deadlines,
            "입고일자":    [None] * n,
        })

    def test_zero_deadline_excluded(self, session):
        import polars as pl
        from app.services.csv_parser import classify_inventory
        df = self._make_df([0, 30])
        result = classify_inventory(df, session)
        replenish_df = result["replenish"]
        assert 0 not in replenish_df["판매마감일수"].to_list()
        assert 30 in replenish_df["판매마감일수"].to_list()

    def test_negative_deadline_excluded(self, session):
        import polars as pl
        from app.services.csv_parser import classify_inventory
        df = self._make_df([-1, 15])
        result = classify_inventory(df, session)
        replenish_df = result["replenish"]
        assert -1 not in replenish_df["판매마감일수"].to_list()
        assert 15 in replenish_df["판매마감일수"].to_list()


# ---------------------------------------------------------------------------
# 3-7. 위험도 스코어 가중치 (run_algorithm + system_config 직접 수정)
# ---------------------------------------------------------------------------

class TestRiskScoreWeights:
    def test_expiry_critical_weight_applied(self, session):
        """expiry_critical_days 이내 마감 → weight_expiry_critical 가산"""
        _setup_wave_with_sku(session, sku_id="SKU_EC", deadline_days=3)

        w = Wave(wave_name="w", wave_type="REGULAR", wave_status="DRAFT",
                 target_sku_count=10, created_by="테스트")
        session.add(w)
        session.commit()
        session.refresh(w)

        result = run_algorithm("GGH1", w.wave_id, session)
        assert result.total_candidates >= 1
        # 마감 3일 → expiry_critical_days(7) 이내 → CRITICAL or HIGH
        assert result.critical_count + result.high_count >= 1

    def test_weight_change_affects_score(self, session):
        """weight_expiry_critical 변경 시 risk_level이 바뀌는지"""
        from sqlmodel import select as sql_select

        _setup_wave_with_sku(session, sku_id="SKU_WC", deadline_days=3)

        # weight_expiry_critical = 0으로 변경
        cfg = session.exec(
            sql_select(SystemConfig).where(SystemConfig.config_key == "weight_expiry_critical")
        ).first()
        original = cfg.config_value
        cfg.config_value = "0"
        session.commit()

        from app.core.config import invalidate_cache
        invalidate_cache()

        w1 = Wave(wave_name="w1", wave_type="REGULAR", wave_status="DRAFT",
                  target_sku_count=10, created_by="테스트")
        session.add(w1)
        session.commit()
        session.refresh(w1)
        result_zero = run_algorithm("GGH1", w1.wave_id, session)

        # weight 원복
        cfg.config_value = original
        session.commit()
        invalidate_cache()

        w2 = Wave(wave_name="w2", wave_type="REGULAR", wave_status="DRAFT",
                  target_sku_count=10, created_by="테스트")
        session.add(w2)
        session.commit()
        session.refresh(w2)
        result_orig = run_algorithm("GGH1", w2.wave_id, session)

        # weight 복원 시 critical/high 수가 같거나 더 많아야 함
        assert result_orig.critical_count + result_orig.high_count >= \
               result_zero.critical_count + result_zero.high_count
