"""존 설정 / 계단-리프트 API 통합 테스트 (from test_step4_api)"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine, Session, SQLModel

from app.main import app
from app.core.dependencies import get_session
from app.core.database import seed_system_config
from app.models.zone import ZoneConfig


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


class TestZoneLayoutApi:
    def test_put_and_get_layout(self, client, api_session):
        res = client.put("/api/v1/zone-config/RA/layout", json={
            "floor": 0, "is_scattered": False,
            "origin_x": 5.0, "origin_y": 10.0,
            "aisle_direction": "y", "aisle_gap": 3.0, "bay_gap": 1.5,
        })
        assert res.status_code == 200
        res = client.get("/api/v1/zone-config/RA/layout")
        assert res.status_code == 200
        data = res.json()
        assert data["origin_x"] == 5.0
        assert data["origin_y"] == 10.0

    def test_layout_unknown_zone_404(self, client):
        res = client.get("/api/v1/zone-config/ZZ/layout")
        assert res.status_code == 404

    def test_aisle_anchors_put_get(self, client, api_session):
        api_session.add(ZoneConfig(
            zone_prefix="PW", zone_name="PW존", slack_channel="#pw",
            access_type="WALKING", list_section="SUB", is_scattered=True,
        ))
        api_session.commit()
        res = client.put("/api/v1/zone-config/PW/aisle-anchors", json=[
            {"aisle_no": 1, "anchor_x": 12.0, "anchor_y": 45.0, "floor": 0},
            {"aisle_no": 2, "anchor_x": 55.0, "anchor_y": 10.0, "floor": 0},
            {"aisle_no": 3, "anchor_x": 80.0, "anchor_y": 60.0, "floor": 1},
        ])
        assert res.status_code == 200
        res = client.get("/api/v1/zone-config/PW/aisle-anchors")
        anchors = res.json()
        assert len(anchors) == 3
        assert next(a for a in anchors if a["aisle_no"] == 3)["floor"] == 1

    def test_aisle_anchors_upsert_idempotent(self, client, api_session):
        api_session.add(ZoneConfig(
            zone_prefix="SF2", zone_name="SF2존", slack_channel="#sf2",
            access_type="WALKING", list_section="SUB", is_scattered=True,
        ))
        api_session.commit()
        client.put("/api/v1/zone-config/SF2/aisle-anchors", json=[
            {"aisle_no": 1, "anchor_x": 10.0, "anchor_y": 20.0, "floor": 0},
        ])
        client.put("/api/v1/zone-config/SF2/aisle-anchors", json=[
            {"aisle_no": 1, "anchor_x": 99.0, "anchor_y": 88.0, "floor": 0},
        ])
        res = client.get("/api/v1/zone-config/SF2/aisle-anchors")
        anchors = res.json()
        assert len(anchors) == 1
        assert anchors[0]["anchor_x"] == 99.0


class TestFloorAccessPointsApi:
    def test_create_update_delete(self, client):
        res = client.post("/api/v1/floor-access-points", json={
            "name": "계단1", "x": 15.0, "y": 20.0, "access_type": "STAIRS",
        })
        assert res.status_code == 200
        access_id = res.json()["access_id"]
        res = client.put(f"/api/v1/floor-access-points/{access_id}", json={"x": 16.0})
        assert res.status_code == 200
        assert res.json()["x"] == 16.0
        res = client.delete(f"/api/v1/floor-access-points/{access_id}")
        assert res.status_code == 200
        ids = [ap["access_id"] for ap in client.get("/api/v1/floor-access-points").json()]
        assert access_id not in ids

    def test_update_unknown_id_404(self, client):
        assert client.put("/api/v1/floor-access-points/9999", json={"x": 1.0}).status_code == 404

    def test_delete_unknown_id_404(self, client):
        assert client.delete("/api/v1/floor-access-points/9999").status_code == 404
