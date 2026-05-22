"""
Phase 2 검증 Step 4: API 엔드포인트 동작 확인
(설계서 검증 프롬프트 §4-1 ~ §4-3)
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine, Session, SQLModel, select

from app.main import app
from app.core.dependencies import get_session
from app.core.database import seed_system_config
from app.models.zone import ZoneConfig
from app.models.sku import SkuPickingHistory, SkuSalesSummary
from app.models.inventory import ReplenishBinSnapshot
from app.models.upload import UploadSession
from app.models.task import ReplenishConfirmedTask


# ---------------------------------------------------------------------------
# TestClient with in-memory DB override
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
        # RA 존 기본 세팅
        s.add(ZoneConfig(
            zone_prefix="RA", zone_name="RA존",
            slack_channel="#ra", slack_channel_id="C_RA",
            access_type="FORKLIFT", list_section="MAIN",
            origin_x=0.0, origin_y=0.0,
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


def _seed_wave_data(api_session, sku_id="SKU_API"):
    u = UploadSession(
        upload_type="INVENTORY", file_name="test.csv",
        uploaded_by="테스트", uploaded_at=datetime.utcnow(), center_cd="GGH1",
    )
    api_session.add(u)
    api_session.commit()
    api_session.refresh(u)

    api_session.add(SkuPickingHistory(
        sku_id=sku_id, center_cd="GGH1",
        picking_bin="15RA0101001", zone="RA",
        last_seen_qty=0, confidence="HIGH",
    ))
    api_session.add(ReplenishBinSnapshot(
        upload_session_id=u.upload_id, center_cd="GGH1",
        sku_id=sku_id, replenish_bin="15RA0201001",
        avail_qty=100, unit_size=12, deadline_days=30,
    ))
    api_session.add(SkuSalesSummary(
        sku_id=sku_id, center_cd="GGH1",
        base_daily_avg=10.0, recent_daily_avg=10.0,
        trend_coef=1.0, adjusted_daily=10.0,
    ))
    api_session.commit()


# ---------------------------------------------------------------------------
# 4-1. 존 배치 설정 API
# ---------------------------------------------------------------------------

class TestZoneLayoutApi:
    def test_put_and_get_layout(self, client, api_session):
        res = client.put("/api/v1/zone-config/RA/layout", json={
            "floor": 0,
            "is_scattered": False,
            "origin_x": 5.0,
            "origin_y": 10.0,
            "aisle_direction": "y",
            "aisle_gap": 3.0,
            "bay_gap": 1.5,
        })
        assert res.status_code == 200

        res = client.get("/api/v1/zone-config/RA/layout")
        assert res.status_code == 200
        data = res.json()
        assert data["origin_x"] == 5.0
        assert data["origin_y"] == 10.0
        assert data["is_scattered"] is False

    def test_layout_unknown_zone_404(self, client):
        res = client.get("/api/v1/zone-config/ZZ/layout")
        assert res.status_code == 404

    def test_aisle_anchors_put_get(self, client, api_session):
        # PW 존 추가 후 산재 앵커 저장
        api_session.add(ZoneConfig(
            zone_prefix="PW", zone_name="PW존",
            slack_channel="#pw", access_type="WALKING",
            list_section="SUB", is_scattered=True,
        ))
        api_session.commit()

        res = client.put("/api/v1/zone-config/PW/aisle-anchors", json=[
            {"aisle_no": 1, "anchor_x": 12.0, "anchor_y": 45.0, "floor": 0},
            {"aisle_no": 2, "anchor_x": 55.0, "anchor_y": 10.0, "floor": 0},
            {"aisle_no": 3, "anchor_x": 80.0, "anchor_y": 60.0, "floor": 1},
        ])
        assert res.status_code == 200

        res = client.get("/api/v1/zone-config/PW/aisle-anchors")
        assert res.status_code == 200
        anchors = res.json()
        assert len(anchors) == 3
        pw3 = next(a for a in anchors if a["aisle_no"] == 3)
        assert pw3["floor"] == 1

    def test_aisle_anchors_upsert_idempotent(self, client, api_session):
        api_session.add(ZoneConfig(
            zone_prefix="SF", zone_name="SF존",
            slack_channel="#sf", access_type="WALKING",
            list_section="SUB", is_scattered=True,
        ))
        api_session.commit()

        # 첫 번째 저장
        client.put("/api/v1/zone-config/SF/aisle-anchors", json=[
            {"aisle_no": 1, "anchor_x": 10.0, "anchor_y": 20.0, "floor": 0},
        ])
        # 같은 aisle_no로 업데이트
        client.put("/api/v1/zone-config/SF/aisle-anchors", json=[
            {"aisle_no": 1, "anchor_x": 99.0, "anchor_y": 88.0, "floor": 0},
        ])
        res = client.get("/api/v1/zone-config/SF/aisle-anchors")
        anchors = res.json()
        assert len(anchors) == 1  # 중복 생성 안 됨
        assert anchors[0]["anchor_x"] == 99.0


class TestFloorAccessPointsApi:
    def test_create_update_delete(self, client):
        # 생성
        res = client.post("/api/v1/floor-access-points", json={
            "name": "계단1",
            "x": 15.0,
            "y": 20.0,
            "access_type": "STAIRS",
        })
        assert res.status_code == 200
        access_id = res.json()["access_id"]
        assert access_id is not None

        # 수정
        res = client.put(f"/api/v1/floor-access-points/{access_id}", json={
            "x": 16.0,
        })
        assert res.status_code == 200
        assert res.json()["x"] == 16.0

        # 삭제
        res = client.delete(f"/api/v1/floor-access-points/{access_id}")
        assert res.status_code == 200

        # 삭제 후 목록에서 사라짐
        res = client.get("/api/v1/floor-access-points")
        ids = [ap["access_id"] for ap in res.json()]
        assert access_id not in ids

    def test_update_unknown_id_404(self, client):
        res = client.put("/api/v1/floor-access-points/9999", json={"x": 1.0})
        assert res.status_code == 404

    def test_delete_unknown_id_404(self, client):
        res = client.delete("/api/v1/floor-access-points/9999")
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# 4-2. 웨이브 생성 → 검수 → 확정 흐름
# ---------------------------------------------------------------------------

class TestWaveFullFlow:
    def test_wave_create_list_get(self, client, api_session):
        _seed_wave_data(api_session)
        res = client.post("/api/v1/waves", json={"wave_type": "REGULAR"})
        assert res.status_code == 200
        wave_id = res.json()["wave_id"]

        # 목록 조회
        res = client.get("/api/v1/waves")
        assert res.status_code == 200
        ids = [w["wave_id"] for w in res.json()]
        assert wave_id in ids

        # 단건 조회
        res = client.get(f"/api/v1/waves/{wave_id}")
        assert res.status_code == 200
        assert res.json()["wave_id"] == wave_id

    def test_wave_full_flow(self, client, api_session):
        _seed_wave_data(api_session)

        # 1. 웨이브 생성
        res = client.post("/api/v1/waves", json={"wave_type": "REGULAR"})
        assert res.status_code == 200
        wave_id = res.json()["wave_id"]
        assert res.json()["algorithm"]["total_candidates"] >= 1

        # 2. 후보 조회
        res = client.get(f"/api/v1/waves/{wave_id}/candidates")
        assert res.status_code == 200
        candidates = res.json()
        assert len(candidates) >= 1

        # 3. 첫 번째 후보 승인
        cid = candidates[0]["candidate_id"]
        res = client.post(f"/api/v1/waves/{wave_id}/candidates/{cid}/approve")
        assert res.status_code == 200
        assert res.json()["candidate_status"] == "APPROVED"

        # 4. 웨이브 확정
        res = client.post(f"/api/v1/waves/{wave_id}/confirm")
        assert res.status_code == 200
        assert res.json()["tasks_created"] >= 1

        # 5. 웨이브 상태 CONFIRMED
        res = client.get(f"/api/v1/waves/{wave_id}")
        assert res.json()["wave_status"] == "CONFIRMED"

    def test_wave_candidate_reject(self, client, api_session):
        _seed_wave_data(api_session)
        res = client.post("/api/v1/waves", json={})
        wave_id = res.json()["wave_id"]

        candidates = client.get(f"/api/v1/waves/{wave_id}/candidates").json()
        cid = candidates[0]["candidate_id"]

        res = client.post(
            f"/api/v1/waves/{wave_id}/candidates/{cid}/reject",
            params={"reason": "재고 확인 필요"},
        )
        assert res.status_code == 200
        assert res.json()["candidate_status"] == "REJECTED"

    def test_confirm_without_approved_candidate_400(self, client, api_session):
        _seed_wave_data(api_session)
        res = client.post("/api/v1/waves", json={})
        wave_id = res.json()["wave_id"]
        # 승인 없이 바로 확정 시도
        res = client.post(f"/api/v1/waves/{wave_id}/confirm")
        assert res.status_code == 400

    def test_wave_tasks_list(self, client, api_session):
        _seed_wave_data(api_session)
        res = client.post("/api/v1/waves", json={})
        wave_id = res.json()["wave_id"]

        candidates = client.get(f"/api/v1/waves/{wave_id}/candidates").json()
        cid = candidates[0]["candidate_id"]
        client.post(f"/api/v1/waves/{wave_id}/candidates/{cid}/approve")
        client.post(f"/api/v1/waves/{wave_id}/confirm")

        res = client.get(f"/api/v1/tasks?wave_id={wave_id}")
        assert res.status_code == 200
        assert len(res.json()) >= 1


# ---------------------------------------------------------------------------
# 4-3. BLOCKED 재포함 검증
# ---------------------------------------------------------------------------

class TestBlockedTaskReincluded:
    def test_blocked_sku_reincluded_in_next_wave(self, client, api_session):
        _seed_wave_data(api_session, sku_id="SKU_BLK")

        # 웨이브 1: 생성 → 승인 → 확정
        res = client.post("/api/v1/waves", json={})
        wave1_id = res.json()["wave_id"]
        candidates = client.get(f"/api/v1/waves/{wave1_id}/candidates").json()

        if not candidates:
            pytest.skip("후보 없음 - 사전 데이터 없음")

        cid = candidates[0]["candidate_id"]
        client.post(f"/api/v1/waves/{wave1_id}/candidates/{cid}/approve")
        client.post(f"/api/v1/waves/{wave1_id}/confirm")

        tasks = client.get(f"/api/v1/tasks?wave_id={wave1_id}").json()
        assert tasks, "태스크 생성 필요"
        task_id = tasks[0]["task_id"]
        sku_id  = tasks[0]["sku_id"]

        # READY → QUEUED → SENT → BLOCKED (state machine 순서)
        client.post(f"/api/v1/tasks/{task_id}/transition", params={"new_status": "QUEUED"})
        client.post(f"/api/v1/tasks/{task_id}/transition", params={"new_status": "SENT"})
        res = client.post(
            f"/api/v1/tasks/{task_id}/transition",
            params={"new_status": "BLOCKED", "block_reason": "통로 막힘"},
        )
        assert res.status_code == 200

        # 웨이브 2: BLOCKED SKU가 재포함되는지 확인
        res = client.post("/api/v1/waves", json={})
        wave2_id = res.json()["wave_id"]
        candidates2 = client.get(f"/api/v1/waves/{wave2_id}/candidates").json()

        sku_ids = [c["sku_id"] for c in candidates2]
        assert sku_id in sku_ids, f"{sku_id} 가 웨이브2 후보에 없음"
