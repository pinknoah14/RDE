"""업로드 엔드포인트 (upload.py) 미검증 경로 보강 테스트.

기존 test_scenarios.py가 정상 경로를 다루므로, 여기서는:
- CSV 파싱 실패 → 400 (inventory/outbound/pivot)
- bin-master: 인코딩 오류, 파싱 오류, 필수 컬럼 누락,
  정상 업서트 + 존 자동 등록, 기존 bin 갱신, _bool/_int/_float 변환
- /sessions 목록 조회
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.models  # noqa: F401
from app.core.config import invalidate_cache
from app.core.database import seed_system_config
from app.core.dependencies import get_session
from app.main import app
from app.models.bin_master import BinMaster
from app.models.upload import UploadSession
from app.models.zone import ZoneConfig

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@pytest.fixture
def client():
    invalidate_cache()
    SQLModel.metadata.create_all(_engine)
    with Session(_engine) as s:
        seed_system_config(s)
        s.commit()

    def _override():
        with Session(_engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()
    SQLModel.metadata.drop_all(_engine)


def _upload(client, path, filename, content: bytes, **data):
    return client.post(
        path,
        files={"file": (filename, content, "text/csv")},
        data=data,
    )


# ────────────────────────────────────────────────────────────────
# 파싱 실패 → 400
# ────────────────────────────────────────────────────────────────

def test_inventory_parse_error_returns_400(client):
    r = _upload(client, "/api/v1/upload/inventory", "bad.csv",
                b"wrong1,wrong2\n1,2\n", center_cd="GGH1")
    assert r.status_code == 400
    assert r.json()["code"] == "UPLOAD_PARSE_ERROR"


def test_outbound_parse_error_returns_400(client):
    r = _upload(client, "/api/v1/upload/outbound", "bad.csv",
                b"nope\n1\n", center_cd="GGH1")
    assert r.status_code == 400
    assert r.json()["code"] == "UPLOAD_PARSE_ERROR"


def test_pivot_parse_error_returns_400(client):
    r = _upload(client, "/api/v1/upload/pivot-sales", "bad.csv",
                b"nope\n1\n", center_cd="GGH1")
    assert r.status_code == 400
    assert r.json()["code"] == "UPLOAD_PARSE_ERROR"


# ────────────────────────────────────────────────────────────────
# bin-master
# ────────────────────────────────────────────────────────────────

def test_bin_master_missing_columns_returns_400(client):
    # 존/지번 컬럼 없음
    r = _upload(client, "/api/v1/upload/bin-master", "bin.csv",
                b"foo,bar\n1,2\n", center_cd="GGH1")
    assert r.status_code == 400
    assert r.json()["code"] == "UPLOAD_MISSING_COLUMNS"


def test_bin_master_encoding_error_returns_400(client):
    # 디코딩 불가능한 바이트 (utf-8-sig/cp949/utf-8 모두 실패 유도)
    # 0xFF 0xFE는 cp949/utf-8에서 디코딩 실패
    bad_bytes = b"\xff\xfe\x00\x01\x80\x81"
    r = _upload(client, "/api/v1/upload/bin-master", "bin.csv",
                bad_bytes, center_cd="GGH1")
    # 디코딩 실패 시 400 (ENCODING 또는 PARSE)
    assert r.status_code == 400


def test_bin_master_valid_upsert_and_zone_creation(client):
    csv = (
        "존,작업존 지번 설명,센터 창고,피킹가능,입고가능,가로(mm),높이(mm),세로(mm),CBM,제품 혼적,Lot. 혼적,작업구역 지번명 상태\n"
        "RA,RA-01-01,GGH1,Y,N,1000,500,800,0.4,Y,N,정상\n"
        "RB,RB-02-03,GGH1,N,Y,1200,600,900,0.65,N,N,정상\n"
    )
    r = _upload(client, "/api/v1/upload/bin-master", "bin.csv",
                csv.encode("utf-8"), center_cd="GGH1")
    assert r.status_code == 200
    body = r.json()
    assert body["bins_upserted"] == 2
    assert body["zones_created"] == 2  # RA, RB 신규 등록
    assert body["zones_existing"] == 0

    # DB 검증: bin 저장 + 변환값
    with Session(_engine) as s:
        ra = s.get(BinMaster, "RA-01-01")
        assert ra is not None
        assert ra.can_pick is True
        assert ra.can_receive is False
        assert ra.width_mm == 1000
        assert ra.cbm == 0.4
        assert ra.allow_mixed_product is True
        # 존 자동 등록
        zones = s.exec(select(ZoneConfig.zone_prefix)).all()
        assert "RA" in zones and "RB" in zones


def test_bin_master_existing_zone_not_recreated(client):
    # 사전에 RA 존 등록
    with Session(_engine) as s:
        s.add(ZoneConfig(
            zone_prefix="RA", zone_name="기존R존", slack_channel="R존",
            access_type="FORKLIFT", list_section="MAIN",
        ))
        s.commit()

    csv = (
        "존,작업존 지번 설명,센터 창고,피킹가능\n"
        "RA,RA-09-09,GGH1,Y\n"
    )
    r = _upload(client, "/api/v1/upload/bin-master", "bin.csv",
                csv.encode("utf-8"), center_cd="GGH1")
    assert r.status_code == 200
    body = r.json()
    assert body["zones_created"] == 0
    assert body["zones_existing"] == 1


def test_bin_master_updates_existing_bin(client):
    csv1 = "존,작업존 지번 설명,피킹가능\nRA,RA-01-01,Y\n"
    r1 = _upload(client, "/api/v1/upload/bin-master", "bin.csv",
                 csv1.encode("utf-8"), center_cd="GGH1")
    assert r1.status_code == 200

    # 동일 bin_id 재업로드 → 갱신 (can_pick N으로 변경)
    csv2 = "존,작업존 지번 설명,피킹가능\nRA,RA-01-01,N\n"
    r2 = _upload(client, "/api/v1/upload/bin-master", "bin.csv",
                 csv2.encode("utf-8"), center_cd="GGH1")
    assert r2.status_code == 200
    assert r2.json()["bins_upserted"] == 1

    with Session(_engine) as s:
        ra = s.get(BinMaster, "RA-01-01")
        assert ra.can_pick is False  # 갱신됨


def test_bin_master_skips_blank_rows(client):
    # bin_id 또는 zone 비어있는 행은 skip
    csv = (
        "존,작업존 지번 설명,피킹가능\n"
        "RA,RA-01-01,Y\n"
        ",RB-02-02,Y\n"          # 존 비어있음 → skip
        "RC,,Y\n"                # 지번 비어있음 → skip
    )
    r = _upload(client, "/api/v1/upload/bin-master", "bin.csv",
                csv.encode("utf-8"), center_cd="GGH1")
    assert r.status_code == 200
    assert r.json()["bins_upserted"] == 1


def test_bin_master_replenish_type_via_query(client):
    csv = "존,작업존 지번 설명,피킹가능\nRA,RA-05-05,Y\n"
    r = client.post(
        "/api/v1/upload/bin-master?bin_type=REPLENISH",
        files={"file": ("bin.csv", csv.encode("utf-8"), "text/csv")},
        data={"center_cd": "GGH1"},
    )
    assert r.status_code == 200
    with Session(_engine) as s:
        ra = s.get(BinMaster, "RA-05-05")
        assert ra.bin_type == "REPLENISH"


# ────────────────────────────────────────────────────────────────
# /sessions
# ────────────────────────────────────────────────────────────────

def test_list_upload_sessions(client):
    # 세션 2건 직접 삽입
    with Session(_engine) as s:
        s.add(UploadSession(
            upload_type="INVENTORY", file_name="a.csv",
            uploaded_by="관리자", record_count=10, center_cd="GGH1",
        ))
        s.add(UploadSession(
            upload_type="OUTBOUND", file_name="b.csv",
            uploaded_by="관리자", record_count=20, center_cd="GGH1",
        ))
        s.commit()

    r = client.get("/api/v1/upload/sessions")
    assert r.status_code == 200
    sessions = r.json()
    assert len(sessions) == 2
    file_names = {s["file_name"] for s in sessions}
    assert file_names == {"a.csv", "b.csv"}
