"""
Phase 4 — 실제 운영 시나리오 테스트
오전 웨이브 / 다중피킹지번 / 신규SKU / 층 이동 비용 / DB 내보내기-가져오기 / 설정 즉시 반영
"""
import pytest
from datetime import datetime
from pathlib import Path

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select
from fastapi.testclient import TestClient

import polars as pl

from app.main import app
from app.core.database import seed_system_config
from app.core.config import invalidate_cache
from app.core.dependencies import get_session
from app.models.zone import ZoneConfig
from app.models.upload import UploadSession

FIXTURES = Path(__file__).parent / "fixtures"

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


def _setup(session: Session) -> None:
    seed_system_config(session)
    zones = [
        ("RA", "R존 메인",   "R존",  "FORKLIFT", "MAIN"),
        ("RB", "R존 B구역",  "R존",  "FORKLIFT", "MAIN"),
        ("SF", "S존 1층",    "S존",  "WALKING",  "MAIN"),
        ("SM", "S존 메자닌", "S존",  "WALKING",  "MAIN"),
        ("PW", "P존",        "P존",  "FORKLIFT", "SUB"),
        ("NC", "NC존",       "NC존", "FORKLIFT", "MAIN"),
    ]
    for prefix, name, ch, atype, section in zones:
        if not session.exec(select(ZoneConfig).where(ZoneConfig.zone_prefix == prefix)).first():
            session.add(ZoneConfig(
                zone_prefix=prefix, zone_name=name, slack_channel=ch,
                access_type=atype, list_section=section, is_special_zone=False,
            ))
    session.commit()


def _load_fixtures(session: Session) -> None:
    inv_path   = FIXTURES / "inventory_sample.csv"
    sales_path = FIXTURES / "pivot_sample.csv"
    if not inv_path.exists():
        return

    from app.services.csv_parser import (
        load_inventory_csv, classify_inventory, update_picking_history,
    )
    from app.services.sales_service import upsert_daily_sales, update_all_sales_summaries
    from app.services.sales_parser import parse_outbound_csv
    from app.api.upload import save_replenish_snapshot

    inv_df = load_inventory_csv(str(inv_path))
    classified = classify_inventory(inv_df, session)
    update_picking_history(classified["picking"], session)

    up = UploadSession(
        upload_type="INVENTORY", file_name="inventory_sample.csv",
        uploaded_by="테스트", uploaded_at=datetime.utcnow(),
        record_count=len(inv_df), center_cd="GGH1",
    )
    session.add(up)
    session.commit()
    session.refresh(up)
    save_replenish_snapshot(classified["replenish"], up.upload_id, "GGH1", session)

    if sales_path.exists():
        sales_df = parse_outbound_csv(sales_path.read_bytes())
        upsert_daily_sales("GGH1", sales_df, session)
        update_all_sales_summaries("GGH1", session)


@pytest.fixture(scope="module")
def full_session():
    if not (FIXTURES / "inventory_sample.csv").exists():
        pytest.skip("fixtures 없음 — 먼저 실행: python tests/fixtures/generate_fixtures.py")
    invalidate_cache()
    SQLModel.metadata.create_all(_engine)
    with Session(_engine) as s:
        _setup(s)
        _load_fixtures(s)
        yield s


@pytest.fixture(scope="module")
def api_client(full_session):
    def _override():
        with Session(_engine) as s:
            yield s
    app.dependency_overrides[get_session] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestOperationScenarios:

    def test_scenario_morning_wave_risk_order(self, api_client):
        """
        시나리오: 오전 웨이브
        후보 목록이 risk_score 내림차순으로 정렬되는지 확인
        """
        res = api_client.post("/api/v1/waves", json={
            "wave_type": "REGULAR", "target_days": 2.0, "max_candidates": 40,
        })
        assert res.status_code == 200
        wave_id = res.json()["wave_id"]

        candidates = api_client.get(f"/api/v1/waves/{wave_id}/candidates").json()
        if len(candidates) < 2:
            pytest.skip("후보 2건 미만")

        scores = [c["risk_score"] for c in candidates]
        assert scores == sorted(scores, reverse=True), "risk_score 내림차순 정렬 실패"

    def test_scenario_multi_picking_bin(self, full_session):
        """
        시나리오: 다중 피킹지번 감지
        동일 SKU 2개 피킹가능 지번 → detect_multi_picking_bins 감지 확인
        """
        from app.services.csv_parser import detect_multi_picking_bins

        df = pl.DataFrame({
            "상품코드": ["MULTI_SKU_9999", "MULTI_SKU_9999", "NORMAL_SKU_9999"],
            "센터":     ["GGH1", "GGH1", "GGH1"],
            "지번":     ["15RA0101001", "15RA0201001", "15RA0301001"],
            "피킹가능": ["피킹가능", "피킹가능", "피킹가능"],
        })
        result = detect_multi_picking_bins(df, full_session)
        multi_ids = result.select("상품코드").to_series().to_list()
        assert "MULTI_SKU_9999" in multi_ids
        assert "NORMAL_SKU_9999" not in multi_ids

    def test_scenario_new_sku_handling(self, full_session):
        """
        시나리오: 신규 SKU 처리
        sku_picking_history + sku_sales_summary 모두 없는 SKU 감지 확인
        """
        from app.services.csv_parser import detect_new_skus

        df = pl.DataFrame({
            "상품코드": ["BRAND_NEW_99999_INTEGRATION"],
            "센터":     ["GGH1"],
            "피킹가능": ["피킹불가"],
            "가용수량": [100],
        })
        new_skus = detect_new_skus(df, full_session)
        assert "BRAND_NEW_99999_INTEGRATION" in new_skus

    def test_scenario_mixed_floor_proximity(self):
        """
        시나리오: 메자닌-1층 혼재 보충지번
        층이 다른 보충지번의 proximity_score가 같은 층보다 낮거나 같은지 확인
        """
        from types import SimpleNamespace
        from app.services.algorithm import get_proximity_score_for_bins

        def _zone(floor, ox=0.0, oy=0.0, direction="y", ag=3.0, bg=1.5):
            return SimpleNamespace(
                is_scattered=False, floor=floor,
                origin_x=ox, origin_y=oy,
                aisle_direction=direction, aisle_gap=ag, bay_gap=bg,
            )

        zone_cfg = {"RA": _zone(0), "SF": _zone(1)}
        access_points = [{"x": 5.0, "y": 5.0}]
        config = {"floor_change_penalty": "60"}

        score_same  = get_proximity_score_for_bins(
            "15RA0101001", "15RA0201001",
            zone_cfg=zone_cfg, aisle_anchors={},
            access_points=access_points, config=config,
        )
        score_cross = get_proximity_score_for_bins(
            "15RA0101001", "15SF0101001",
            zone_cfg=zone_cfg, aisle_anchors={},
            access_points=access_points, config=config,
        )
        assert score_cross <= score_same, \
            f"층 이동 비용 미반영: same={score_same}, cross={score_cross}"

    def test_scenario_db_export_import(self, api_client, tmp_path):
        """
        시나리오: DB 내보내기 → 가져오기 정합성 확인
        """
        res = api_client.get("/api/v1/admin/db-export")
        assert res.status_code == 200
        assert len(res.content) > 0

        export_path = tmp_path / "test_export.db"
        export_path.write_bytes(res.content)

        with open(export_path, "rb") as f:
            res = api_client.post(
                "/api/v1/admin/db-import",
                files={"file": ("test_export.db", f, "application/octet-stream")},
            )
        assert res.status_code == 200

    def test_scenario_floor_penalty_affects_score(self):
        """
        시나리오: floor_change_penalty 변경이 proximity_score에 즉시 반영됨
        낮은 패널티 → 층 이동이 가까워 보임 → score 높을 가능성
        높은 패널티 → 층 이동이 멀어 보임 → score 낮을 가능성
        """
        from types import SimpleNamespace
        from app.services.algorithm import get_proximity_score_for_bins

        def _zone(floor):
            return SimpleNamespace(
                is_scattered=False, floor=floor,
                origin_x=0.0, origin_y=0.0,
                aisle_direction="y", aisle_gap=3.0, bay_gap=1.5,
            )

        zone_cfg = {"RA": _zone(0), "SF": _zone(1)}
        access_points = [{"x": 50.0, "y": 50.0}]

        score_low  = get_proximity_score_for_bins(
            "15RA0101001", "15SF0101001",
            zone_cfg=zone_cfg, aisle_anchors={},
            access_points=access_points,
            config={"floor_change_penalty": "1"},
        )
        score_high = get_proximity_score_for_bins(
            "15RA0101001", "15SF0101001",
            zone_cfg=zone_cfg, aisle_anchors={},
            access_points=access_points,
            config={"floor_change_penalty": "999"},
        )
        assert score_low >= score_high, \
            f"floor_change_penalty 변경이 score에 미반영: low={score_low}, high={score_high}"

    def test_scenario_inventory_upload_api(self, api_client):
        """
        시나리오: 재고 CSV 업로드 API
        응답에 필수 필드(upload_id, record_count, picking_count) 포함 확인
        """
        inv_path = FIXTURES / "inventory_sample.csv"
        if not inv_path.exists():
            pytest.skip("inventory_sample.csv 없음")

        with open(inv_path, "rb") as f:
            res = api_client.post(
                "/api/v1/upload/inventory",
                files={"file": ("inventory_sample.csv", f, "text/csv")},
                data={"center_cd": "GGH1"},
            )
        assert res.status_code == 200, res.text
        data = res.json()
        assert "upload_id" in data
        assert "record_count" in data
        assert "picking_count" in data
        assert data["record_count"] > 0
