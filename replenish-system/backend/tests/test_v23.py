"""v2.3 신규 기능 회귀 테스트.

- 피킹지번 자동 보정 (restore_missing_picking_bins)
- PIN 인증 (admin/verify-pin)
- 통일 에러 응답 (code/message/detail)
- 긴급 웨이브 API 존재 확인
- DB 자동 백업 (auto_backup_db)
"""
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.database import seed_system_config
from app.core.dependencies import get_session
from app.main import app
from app.models.sku import SkuPickingHistory
from app.models.config import SystemConfig
from sqlmodel import select


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
# 1. 피킹지번 자동 보정
# ---------------------------------------------------------------------------

def test_restore_missing_picking_bins(api_session):
    """WMS 유실(avail=0) SKU가 sku_picking_history 기반으로 복구된다."""
    from app.services.csv_parser import restore_missing_picking_bins

    api_session.add(SkuPickingHistory(
        sku_id="RESTORE_TEST_SKU",
        center_cd="GGH1",
        picking_bin="15RA0101001",
        zone="RA",
        confidence="HIGH",
        last_seen_date=date(2026, 5, 1),
        last_seen_qty=30,
    ))
    api_session.commit()

    picking_df = pl.DataFrame({
        "상품코드": ["OTHER_SKU"],
        "지번": ["15RA0202001"],
        "가용수량": [10],
    })

    result = restore_missing_picking_bins(picking_df, api_session)

    restored = result.filter(pl.col("상품코드") == "RESTORE_TEST_SKU")
    assert restored.height == 1
    assert restored["지번"][0] == "15RA0101001"
    assert restored["가용수량"][0] == 0


def test_restore_skip_if_already_in_csv(api_session):
    """이미 CSV에 있는 SKU는 복구 대상에서 제외."""
    from app.services.csv_parser import restore_missing_picking_bins

    api_session.add(SkuPickingHistory(
        sku_id="SKU_PRESENT",
        center_cd="GGH1",
        picking_bin="15RA0101001",
        zone="RA",
        confidence="HIGH",
        last_seen_date=date(2026, 5, 1),
        last_seen_qty=30,
    ))
    api_session.commit()

    picking_df = pl.DataFrame({
        "상품코드": ["SKU_PRESENT"],
        "지번": ["15RA0202001"],
        "가용수량": [10],
    })

    result = restore_missing_picking_bins(picking_df, api_session)
    assert result.height == 1


# ---------------------------------------------------------------------------
# 2. PIN 인증
# ---------------------------------------------------------------------------

def test_pin_unset_passes(client):
    """admin_pin 빈 값 → 어떤 입력이든 통과."""
    res = client.post("/api/v1/admin/verify-pin", json={"pin": ""})
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_pin_wrong_returns_401(client, api_session):
    """admin_pin 설정 + 다른 입력 → 401."""
    cfg = api_session.exec(
        select(SystemConfig).where(SystemConfig.config_key == "admin_pin")
    ).first()
    cfg.config_value = "1234"
    api_session.add(cfg)
    api_session.commit()

    from app.core.config import invalidate_cache
    invalidate_cache()

    res = client.post("/api/v1/admin/verify-pin", json={"pin": "0000"})
    assert res.status_code == 401
    body = res.json()
    assert body["ok"] is False
    assert body["code"] == "INVALID_PIN"


def test_pin_correct_passes(client, api_session):
    """올바른 PIN → 200."""
    cfg = api_session.exec(
        select(SystemConfig).where(SystemConfig.config_key == "admin_pin")
    ).first()
    cfg.config_value = "1234"
    api_session.add(cfg)
    api_session.commit()

    from app.core.config import invalidate_cache
    invalidate_cache()

    res = client.post("/api/v1/admin/verify-pin", json={"pin": "1234"})
    assert res.status_code == 200
    assert res.json()["ok"] is True


# ---------------------------------------------------------------------------
# 3. 에러 응답 통일
# ---------------------------------------------------------------------------

def test_error_response_unified_format(client):
    """존재하지 않는 웨이브 조회 → code/message/detail 포함."""
    res = client.get("/api/v1/waves/99999")
    body = res.json()
    assert res.status_code == 404
    assert "code" in body
    assert "message" in body
    assert "detail" in body
    assert body["code"] == "WAVE_NOT_FOUND"


def test_validation_error_format(client):
    """Pydantic validation 오류 → VALIDATION_ERROR 코드."""
    res = client.post("/api/v1/admin/verify-pin", json={})  # pin 누락
    body = res.json()
    assert res.status_code == 422
    assert body["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# 4. 긴급 웨이브 API 존재
# ---------------------------------------------------------------------------

def test_urgent_wave_endpoint_exists(client):
    """긴급 웨이브 엔드포인트 등록 확인 (실데이터 없이도 동작)."""
    res = client.post(
        "/api/v1/waves/urgent-from-dashboard",
        json={"auto_confirm": False, "auto_send": False},
    )
    # 데이터 없으면 201로 wave만 생성, candidates=0
    assert res.status_code == 201
    body = res.json()
    assert "wave_id" in body
    assert "candidates" in body
    assert body["confirmed"] is False


# ---------------------------------------------------------------------------
# 5. DB 자동 백업
# ---------------------------------------------------------------------------

def test_auto_backup_creates_file(tmp_path, monkeypatch):
    """DB가 있으면 백업 파일이 생성된다."""
    from app.core import database as db_module

    fake_db = tmp_path / "replenish.db"
    fake_db.write_bytes(b"fake-sqlite-content")
    monkeypatch.setattr(db_module, "DB_PATH", fake_db)

    backup_path = db_module.auto_backup_db()

    assert backup_path is not None
    assert backup_path.exists()
    assert backup_path.parent.name == "backups"
    assert backup_path.read_bytes() == b"fake-sqlite-content"


def test_auto_backup_skips_when_no_db(tmp_path, monkeypatch):
    """DB가 없으면 None 반환."""
    from app.core import database as db_module
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "nonexistent.db")
    assert db_module.auto_backup_db() is None
