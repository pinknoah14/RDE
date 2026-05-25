"""v1.9 신규 기능 회귀 테스트."""
import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.database import seed_system_config
from app.core.dependencies import get_session
from app.main import app
from app.models.event import Event
from app.models.inventory import ReplenishBinSnapshot
from app.models.sku import DailySalesHistory, SkuPickingHistory, SkuSalesSummary
from app.models.task import (
    ReplenishCandidate,
    ReplenishConfirmedTask,
    ReplenishTaskLocation,
)
from app.models.upload import UploadSession
from app.models.wave import Wave
from app.models.worker import Worker
from app.models.zone import PickingZoneMaster, ZoneConfig


# ---------------------------------------------------------------------------
# TestClient fixture (in-memory DB sharing via StaticPool)
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        seed_system_config(s)
        s.add(ZoneConfig(
            zone_prefix="RA", zone_name="RA존",
            slack_channel="#ra", slack_channel_id="C_RA",
            access_type="FORKLIFT", list_section="MAIN",
        ))
        s.commit()
        yield s


@pytest.fixture()
def client(api_session):
    def _override():
        yield api_session

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. matched_bins API 노출
# ---------------------------------------------------------------------------

def test_candidate_matched_bins_exposed(session):
    """GET /candidates 응답에 matched_bins 배열 존재."""
    wave = Wave(
        wave_name="t", wave_type="REGULAR", wave_status="DRAFT",
        target_sku_count=1, created_by="t",
    )
    session.add(wave)
    session.commit()
    session.refresh(wave)

    bins = [
        {"replenish_bin": "15RA9990001", "allocated_qty": 5,
         "deadline_days": 7, "receipt_date": "2026-05-20", "proximity_score": 4},
        {"replenish_bin": "15RA9990002", "allocated_qty": 3,
         "deadline_days": 14, "receipt_date": "2026-05-21", "proximity_score": 3},
    ]
    cand = ReplenishCandidate(
        wave_id=wave.wave_id, sku_id="SKU_T", sku_name="테스트",
        picking_bin="15RA0010001", zone="RA", slack_channel="R존",
        risk_score=90.0, risk_level="CRITICAL", recommended_qty=8,
        matched_bins_json=json.dumps(bins),
    )
    session.add(cand)
    session.commit()

    from app.api.waves import list_candidates
    result = list_candidates(wave.wave_id, status=None, min_score=None, session=session)
    assert len(result) == 1
    assert "matched_bins" in result[0]
    assert len(result[0]["matched_bins"]) == 2
    assert result[0]["matched_bins"][0]["replenish_bin"] == "15RA9990001"
    assert result[0]["matched_bins"][0]["allocated_qty"] == 5


# ---------------------------------------------------------------------------
# 2. today_sales 실제 계산
# ---------------------------------------------------------------------------

def test_today_sales_from_daily_history(session):
    """DailySalesHistory에 당일 데이터 있으면 today_sales 반영됨."""
    upload = UploadSession(
        upload_type="INVENTORY", file_name="t.csv",
        uploaded_by="t", uploaded_at=datetime.utcnow(),
        record_count=1, center_cd="GGH1",
    )
    session.add(upload)
    session.commit()
    session.refresh(upload)

    today = date.today()
    session.add(DailySalesHistory(
        sku_id="SKU_TS", center_cd="GGH1",
        sales_date=today, sales_qty=42, unassigned_qty=0,
    ))
    session.add(SkuSalesSummary(
        sku_id="SKU_TS", center_cd="GGH1", sku_name="TS상품",
        adjusted_daily=10.0,
    ))
    session.add(SkuPickingHistory(
        sku_id="SKU_TS", center_cd="GGH1",
        picking_bin="15RA0010001", zone="RA",
        last_seen_qty=5, confidence="HIGH",
    ))
    session.add(ReplenishBinSnapshot(
        upload_session_id=upload.upload_id,
        center_cd="GGH1", sku_id="SKU_TS", sku_name="TS상품",
        replenish_bin="15RA9990001", avail_qty=100, unit_size=1,
        deadline_days=10, receipt_date="2026-05-20",
    ))
    session.commit()

    wave = Wave(
        wave_name="ts", wave_type="REGULAR", wave_status="DRAFT",
        target_sku_count=1, created_by="t",
    )
    session.add(wave)
    session.commit()
    session.refresh(wave)

    from app.services.wave_builder import run_algorithm
    run_algorithm("GGH1", wave.wave_id, session)

    cand = session.exec(
        select(ReplenishCandidate).where(
            ReplenishCandidate.wave_id == wave.wave_id,
            ReplenishCandidate.sku_id == "SKU_TS",
        )
    ).first()
    assert cand is not None, "후보 생성 실패"
    assert cand.today_sales == 42, f"today_sales 불일치: {cand.today_sales}"


# ---------------------------------------------------------------------------
# 3. 이벤트 CRUD
# ---------------------------------------------------------------------------

def test_event_crud(client):
    """이벤트 생성/조회/수정/삭제 전체 흐름."""
    res = client.post("/api/v1/events", json={
        "sku_id": "SKU_E1", "event_name": "여름 행사",
        "event_type": "EVENT",
        "start_date": "2026-06-01", "end_date": "2026-06-30",
        "memo": "테스트",
    })
    assert res.status_code == 200, res.text
    event_id = res.json()["event_id"]

    res = client.get("/api/v1/events")
    assert res.status_code == 200
    assert any(e["event_id"] == event_id for e in res.json())

    res = client.patch(f"/api/v1/events/{event_id}", json={"event_name": "수정됨"})
    assert res.status_code == 200
    assert res.json()["event_name"] == "수정됨"

    res = client.delete(f"/api/v1/events/{event_id}")
    assert res.status_code == 200
    assert res.json() == {"deleted": event_id}


# ---------------------------------------------------------------------------
# 5. 피킹지번 CRUD
# ---------------------------------------------------------------------------

def test_picking_zone_create_read_delete(client):
    """피킹지번 추가/조회/삭제 흐름."""
    res = client.post("/api/v1/picking-zones", json={
        "bin_id": "15RA9999999", "zone": "RA", "memo": "TEST",
    })
    assert res.status_code == 200, res.text
    assert res.json()["bin_id"] == "15RA9999999"

    res = client.get("/api/v1/picking-zones?q=9999")
    assert res.status_code == 200
    assert any(p["bin_id"] == "15RA9999999" for p in res.json())

    res = client.patch("/api/v1/picking-zones/15RA9999999", json={"is_active": False})
    assert res.status_code == 200
    assert res.json()["is_active"] is False

    res = client.delete("/api/v1/picking-zones/15RA9999999")
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# 6. 존 CRUD
# ---------------------------------------------------------------------------

def test_zone_create_update_delete(client):
    """존 추가/수정/삭제 흐름."""
    res = client.post("/api/v1/zone-config", json={
        "zone_prefix": "ZZ", "zone_name": "테스트존",
        "slack_channel": "테스트", "access_type": "FORKLIFT",
        "list_section": "MAIN",
    })
    assert res.status_code == 200, res.text
    zid = res.json()["zone_config_id"]

    res = client.put(f"/api/v1/zone-config/{zid}", json={"zone_name": "수정존"})
    assert res.status_code == 200
    assert res.json()["zone_name"] == "수정존"

    res = client.delete(f"/api/v1/zone-config/{zid}")
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# 7. daily-reset
# ---------------------------------------------------------------------------

def test_daily_reset_clears_active(client, api_session):
    """is_active=True 작업자가 reset 후 is_active=False."""
    worker = Worker(
        worker_name="홍길동", worker_type="FORKLIFT",
        zone_access="[\"RA\"]", max_tasks=6,
        is_active=True, is_sub_worker=True,
    )
    api_session.add(worker)
    api_session.commit()
    api_session.refresh(worker)

    res = client.post("/api/v1/workers/daily-reset")
    assert res.status_code == 200
    assert res.json()["reset_count"] >= 1

    api_session.refresh(worker)
    assert worker.is_active is False
    assert worker.is_sub_worker is False
