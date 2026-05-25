"""
Phase 4 — 성능 테스트
CSV 파싱 / 알고리즘 / API 응답 속도 측정
"""
import time
import pytest
from datetime import datetime
from pathlib import Path

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select
from fastapi.testclient import TestClient

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
        ("RA", "R존 메인",  "R존",  "FORKLIFT", "MAIN"),
        ("RB", "R존 B구역", "R존",  "FORKLIFT", "MAIN"),
        ("SF", "S존 1층",   "S존",  "WALKING",  "MAIN"),
        ("PW", "P존",       "P존",  "FORKLIFT", "SUB"),
        ("NC", "NC존",      "NC존", "FORKLIFT", "MAIN"),
    ]
    for prefix, name, ch, atype, section in zones:
        if not session.exec(select(ZoneConfig).where(ZoneConfig.zone_prefix == prefix)).first():
            session.add(ZoneConfig(
                zone_prefix=prefix, zone_name=name, slack_channel=ch,
                access_type=atype, list_section=section, is_special_zone=False,
            ))
    session.commit()


def _load_data(session: Session) -> None:
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
        _load_data(s)
        yield s


@pytest.fixture(scope="module")
def api_client(full_session):
    def _override():
        with Session(_engine) as s:
            yield s
    app.dependency_overrides[get_session] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestPerformance:

    def test_csv_parse_speed(self, full_session):
        """재고 CSV 파싱 속도: 10초 이내"""
        from app.services.csv_parser import load_inventory_csv, classify_inventory

        t0 = time.time()
        df = load_inventory_csv(str(FIXTURES / "inventory_sample.csv"))
        classify_inventory(df, full_session)
        elapsed = time.time() - t0

        print(f"\n재고 CSV: {len(df):,}행 / {elapsed:.2f}초")
        assert elapsed < 10.0, f"파싱 10초 초과: {elapsed:.1f}초"

    def test_algorithm_speed(self, full_session):
        """알고리즘 실행 속도: 20초 이내"""
        from app.services.wave_builder import run_algorithm
        from app.models.wave import Wave

        wave = Wave(
            wave_name=f"성능테스트_{datetime.utcnow().strftime('%f')}",
            wave_status="DRAFT", target_sku_count=200, created_by="성능테스트",
        )
        full_session.add(wave)
        full_session.commit()
        full_session.refresh(wave)

        t0 = time.time()
        result = run_algorithm("GGH1", wave.wave_id, full_session)
        elapsed = time.time() - t0

        print(f"\n알고리즘: {result.total_candidates}개 후보 / {elapsed:.2f}초")
        assert elapsed < 20.0, f"알고리즘 20초 초과: {elapsed:.1f}초"

    def test_wave_create_api_speed(self, api_client):
        """웨이브 생성 API: 5초 이내 (알고리즘 포함)"""
        t0 = time.time()
        res = api_client.post("/api/v1/waves", json={"max_candidates": 40})
        elapsed = time.time() - t0

        assert res.status_code == 200, res.text
        print(f"\n웨이브 생성 API: {elapsed:.2f}초")
        assert elapsed < 5.0, f"웨이브 생성 5초 초과: {elapsed:.1f}초"

    def test_candidate_fetch_speed(self, api_client):
        """후보 목록 조회 속도: 1초 이내"""
        res = api_client.post("/api/v1/waves", json={"max_candidates": 40})
        wave_id = res.json()["wave_id"]

        t0 = time.time()
        res = api_client.get(f"/api/v1/waves/{wave_id}/candidates")
        elapsed = time.time() - t0

        assert res.status_code == 200
        print(f"\n후보 조회: {elapsed:.2f}초 / {len(res.json())}건")
        assert elapsed < 1.0, f"후보 조회 1초 초과: {elapsed:.1f}초"

    def test_sales_upsert_speed(self, full_session):
        """판매 데이터 UPSERT 속도: 30초 이내"""
        from app.services.sales_service import upsert_daily_sales
        from app.services.sales_parser import parse_outbound_csv

        sales_path = FIXTURES / "pivot_sample.csv"
        if not sales_path.exists():
            pytest.skip("pivot_sample.csv 없음")

        sales_df = parse_outbound_csv(sales_path.read_bytes())
        t0 = time.time()
        count = upsert_daily_sales("GGH1", sales_df, full_session)
        elapsed = time.time() - t0

        print(f"\n판매 UPSERT: {count:,}행 / {elapsed:.2f}초")
        assert elapsed < 30.0, f"판매 UPSERT 30초 초과: {elapsed:.1f}초"
