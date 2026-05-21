"""
Phase 2 검증 Step 5: 알고리즘 통합 검증 + Step 6: 엣지 케이스
(설계서 검증 프롬프트 §5, §6)
"""
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlmodel import select

from app.models.task import ReplenishCandidate
from app.models.zone import ZoneConfig
from app.models.sku import SkuPickingHistory, SkuSalesSummary
from app.models.inventory import ReplenishBinSnapshot
from app.models.upload import UploadSession
from app.models.wave import Wave
from app.services.algorithm import (
    get_proximity_score_for_bins,
    match_replen_bins,
    run_algorithm,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _upload(session):
    u = UploadSession(
        upload_type="INVENTORY", file_name="test.csv",
        uploaded_by="테스트", uploaded_at=datetime.utcnow(), center_cd="GGH1",
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _wave(session, name="통합테스트웨이브"):
    w = Wave(wave_name=name, wave_type="REGULAR", wave_status="DRAFT",
             target_sku_count=40, created_by="테스트")
    session.add(w)
    session.commit()
    session.refresh(w)
    return w


def _seed_sku(session, sku_id, upload_id, picking_bin="15RA0101001",
              replenish_bin="15RA0201001", avail_qty=0, replen_qty=100,
              deadline_days=30, daily_avg=10.0):
    session.add(SkuPickingHistory(
        sku_id=sku_id, center_cd="GGH1",
        picking_bin=picking_bin, zone="RA",
        last_seen_qty=avail_qty, confidence="HIGH",
    ))
    session.add(ReplenishBinSnapshot(
        upload_session_id=upload_id, center_cd="GGH1",
        sku_id=sku_id, replenish_bin=replenish_bin,
        avail_qty=replen_qty, unit_size=12, deadline_days=deadline_days,
    ))
    session.add(SkuSalesSummary(
        sku_id=sku_id, center_cd="GGH1",
        base_daily_avg=daily_avg, recent_daily_avg=daily_avg,
        trend_coef=1.0, adjusted_daily=daily_avg,
    ))
    session.commit()


# ---------------------------------------------------------------------------
# Step 5-1. run_algorithm() 실행 시 ReplenishCandidate 생성
# ---------------------------------------------------------------------------

class TestRunAlgorithmIntegration:
    def test_produces_candidates_and_saves_to_db(self, session):
        u = _upload(session)
        _seed_sku(session, "SKU_INT1", u.upload_id)
        _seed_sku(session, "SKU_INT2", u.upload_id,
                  picking_bin="15RA0102001", replenish_bin="15RA0202001")

        w = _wave(session)
        result = run_algorithm("GGH1", w.wave_id, session)

        assert result.total_candidates >= 2
        assert result.execution_ms >= 0

        db_count = len(session.exec(
            select(ReplenishCandidate).where(ReplenishCandidate.wave_id == w.wave_id)
        ).all())
        assert db_count == result.total_candidates

    def test_risk_levels_are_populated(self, session):
        u = _upload(session)
        _seed_sku(session, "SKU_RISK", u.upload_id, avail_qty=0, daily_avg=100.0)

        w = _wave(session)
        run_algorithm("GGH1", w.wave_id, session)

        candidate = session.exec(
            select(ReplenishCandidate).where(
                ReplenishCandidate.wave_id == w.wave_id,
                ReplenishCandidate.sku_id == "SKU_RISK",
            )
        ).first()
        assert candidate is not None
        assert candidate.risk_level in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
        assert 0 <= candidate.risk_score <= 100

    def test_no_replen_skus_excluded(self, session):
        u = _upload(session)
        # 보충존 재고 0 → 보충 불가
        _seed_sku(session, "SKU_NO_REPLEN", u.upload_id, replen_qty=0)

        w = _wave(session)
        result = run_algorithm("GGH1", w.wave_id, session)

        assert "SKU_NO_REPLEN" in result.no_replen_skus

    def test_execution_ms_within_limit(self, session):
        u = _upload(session)
        for i in range(10):
            _seed_sku(session, f"SKU_PERF{i:02d}", u.upload_id,
                      picking_bin=f"15RA{i+1:02d}01001",
                      replenish_bin=f"15RA{i+1:02d}02001")

        w = _wave(session)
        result = run_algorithm("GGH1", w.wave_id, session)

        assert result.execution_ms < 10000  # 10초 이내


# ---------------------------------------------------------------------------
# Step 5-2. proximity_score가 보충지번 정렬에 실제 반영
# ---------------------------------------------------------------------------

class TestProximityAppliedInSorting:
    def _zone_cfg(self, floor=0):
        z = MagicMock()
        z.is_scattered = False
        z.floor = floor
        z.origin_x = 0.0
        z.origin_y = 0.0
        z.aisle_direction = "y"
        z.aisle_gap = 3.0
        z.bay_gap = 1.5
        return z

    def _rb(self, bin_id, qty, deadline):
        rb = MagicMock()
        rb.replenish_bin = bin_id
        rb.avail_qty = qty
        rb.deadline_days = deadline
        rb.unit_size = 12
        rb.receipt_date = None
        return rb

    def test_same_deadline_closer_bin_first(self):
        zone_cfg = {"RA": self._zone_cfg()}
        bins = [
            self._rb("15RA1001001", 100, deadline=30),  # 통로10 (먼)
            self._rb("15RA0201001", 100, deadline=30),  # 통로2  (가까움)
        ]
        result = match_replen_bins(
            "15RA0101001", bins, 24, zone_cfg, {}, [], {}
        )
        assert result[0]["replenish_bin"] == "15RA0201001"  # 가까운 통로 먼저

    def test_shorter_deadline_beats_proximity(self):
        zone_cfg = {"RA": self._zone_cfg()}
        bins = [
            self._rb("15RA0201001", 100, deadline=100),  # 가깝지만 유통기한 김
            self._rb("15RA1001001", 100, deadline=5),    # 멀지만 유통기한 임박
        ]
        result = match_replen_bins(
            "15RA0101001", bins, 24, zone_cfg, {}, [], {}
        )
        assert result[0]["deadline_days"] == 5  # FEFO 우선

    def test_multi_bin_fefo_sequence_correct(self):
        bins = [
            self._rb("15RA0401001", 10, deadline=60),
            self._rb("15RA0301001", 10, deadline=20),
            self._rb("15RA0201001", 10, deadline=10),
        ]
        result = match_replen_bins("15RA0101001", bins, 25, {}, {}, [], {})
        deadlines = [r["deadline_days"] for r in result]
        assert deadlines == sorted(deadlines)  # 마감일 ASC 순서


# ---------------------------------------------------------------------------
# Step 6. 엣지 케이스
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_no_coord_config_graceful(self, session):
        """좌표 미설정 상태에서 알고리즘이 오류 없이 실행"""
        u = _upload(session)
        _seed_sku(session, "SKU_NC", u.upload_id)

        w = _wave(session)
        result = run_algorithm("GGH1", w.wave_id, session)
        assert result is not None
        assert result.total_candidates >= 1

    def test_mixed_floor_travel_cost_applied(self):
        """피킹지번 1층 ↔ 보충지번 메자닌: 층 이동 비용 반영"""
        def _z(floor):
            z = MagicMock()
            z.is_scattered = False
            z.floor = floor
            z.origin_x = 0.0
            z.origin_y = 0.0
            z.aisle_direction = "y"
            z.aisle_gap = 3.0
            z.bay_gap = 1.5
            return z

        zone_cfg = {"RA": _z(0), "SF": _z(1)}
        score = get_proximity_score_for_bins(
            "15RA0101001", "15SF0101001",
            zone_cfg=zone_cfg, aisle_anchors={},
            access_points=[{"x": 5.0, "y": 5.0}],
            config={"floor_change_penalty": "60"},
        )
        assert score <= 2  # 층 이동으로 점수 낮음

    def test_null_bin_id_safe(self):
        """보류지번(parse_bin_id→None)이 들어와도 폴백으로 안전 처리"""
        score = get_proximity_score_for_bins(
            "PKMOVE01", "15RA0101001",
            zone_cfg={}, aisle_anchors={},
            access_points=[], config={}
        )
        assert score in [1, 2]

    def test_both_hold_bins_falls_back(self):
        """양쪽 모두 보류지번인 경우"""
        score = get_proximity_score_for_bins(
            "PKMOVE01", "RT0001234",
            zone_cfg={}, aisle_anchors={},
            access_points=[], config={}
        )
        assert score in [1, 2]

    def test_empty_replenish_no_candidate(self, session):
        """보충 가능 재고 없으면 후보 생성 안 됨"""
        u = _upload(session)
        session.add(SkuPickingHistory(
            sku_id="SKU_EMPTY", center_cd="GGH1",
            picking_bin="15RA0101001", zone="RA",
            last_seen_qty=0, confidence="HIGH",
        ))
        # ReplenishBinSnapshot 없음
        session.add(SkuSalesSummary(
            sku_id="SKU_EMPTY", center_cd="GGH1",
            base_daily_avg=10.0, recent_daily_avg=10.0,
            trend_coef=1.0, adjusted_daily=10.0,
        ))
        session.commit()

        w = _wave(session)
        result = run_algorithm("GGH1", w.wave_id, session)
        assert "SKU_EMPTY" in result.no_replen_skus

    def test_max_score_capped_at_100(self, session):
        """여러 가중치 중첩 시 스코어가 100 초과하지 않음"""
        u = _upload(session)
        # 모든 가중치 조건 활성화: 품절 + 유통기한 위급 + 이벤트
        session.add(SkuPickingHistory(
            sku_id="SKU_MAX", center_cd="GGH1",
            picking_bin="15RA0101001", zone="RA",
            last_seen_qty=0, confidence="HIGH",
        ))
        session.add(ReplenishBinSnapshot(
            upload_session_id=u.upload_id, center_cd="GGH1",
            sku_id="SKU_MAX", replenish_bin="15RA0201001",
            avail_qty=100, unit_size=12, deadline_days=1,  # 위급 마감
        ))
        summary = SkuSalesSummary(
            sku_id="SKU_MAX", center_cd="GGH1",
            base_daily_avg=100.0, recent_daily_avg=100.0,
            trend_coef=1.0, adjusted_daily=100.0,
        )
        summary.stockout_flag = True
        summary.event_flag = True
        session.add(summary)
        session.commit()

        w = _wave(session)
        run_algorithm("GGH1", w.wave_id, session)

        candidate = session.exec(
            select(ReplenishCandidate).where(
                ReplenishCandidate.sku_id == "SKU_MAX"
            )
        ).first()
        assert candidate is not None
        assert candidate.risk_score <= 100
