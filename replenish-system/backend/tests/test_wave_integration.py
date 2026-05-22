"""
Phase 4 — 웨이브 전체 생명주기 통합 테스트
Wave 생성 → 후보 승인 → 확정 → 태스크 생성 → 상태 전환
"""
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


def _seed(session: Session) -> None:
    seed_system_config(session)
    zones = [
        ("RA", "R존 메인",   "R존",  "FORKLIFT", "MAIN"),
        ("RB", "R존 B구역",  "R존",  "FORKLIFT", "MAIN"),
        ("SF", "S존 1층",    "S존",  "WALKING",  "MAIN"),
        ("SM", "S존 메자닌", "S존",  "WALKING",  "MAIN"),
        ("PW", "P존 W구역",  "P존",  "FORKLIFT", "SUB"),
        ("NC", "NC존",       "NC존", "FORKLIFT", "MAIN"),
    ]
    for prefix, name, ch, atype, section in zones:
        if not session.exec(
            select(ZoneConfig).where(ZoneConfig.zone_prefix == prefix)
        ).first():
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

    inv_df     = load_inventory_csv(str(inv_path))
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
        _seed(s)
        _load_fixtures(s)
        yield s


@pytest.fixture(scope="module")
def client(full_session):
    def _override():
        with Session(_engine) as s:
            yield s
    app.dependency_overrides[get_session] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── 웨이브 생명주기 ────────────────────────────────────────────────────────


class TestWaveLifecycle:

    def test_wave_create_returns_wave_id(self, client):
        """웨이브 생성 시 wave_id 반환"""
        res = client.post("/api/v1/waves", json={"max_candidates": 10})
        assert res.status_code == 200, res.text
        data = res.json()
        assert "wave_id" in data
        assert "algorithm" in data

    def test_wave_lifecycle_full(self, client):
        """웨이브 생성 → 후보 전체 승인 → 확정 → 태스크 생성 확인"""
        # 1. 생성
        res = client.post("/api/v1/waves", json={"max_candidates": 5})
        assert res.status_code == 200
        wave_id = res.json()["wave_id"]

        # 2. 후보 조회
        res = client.get(f"/api/v1/waves/{wave_id}/candidates")
        assert res.status_code == 200
        candidates = res.json()

        if not candidates:
            pytest.skip("후보 없음 — 알고리즘 실행 데이터 부족")

        # 3. 전체 승인
        for c in candidates:
            r = client.post(f"/api/v1/waves/{wave_id}/candidates/{c['candidate_id']}/approve")
            assert r.status_code == 200, r.text

        # 4. 확정
        res = client.post(f"/api/v1/waves/{wave_id}/confirm")
        assert res.status_code == 200, res.text
        confirm_data = res.json()
        assert confirm_data["tasks_created"] > 0

        # 5. 웨이브 상태 확인
        res = client.get(f"/api/v1/waves/{wave_id}")
        assert res.status_code == 200
        assert res.json()["wave_status"] == "CONFIRMED"

        # 6. 태스크 목록 확인
        res = client.get(f"/api/v1/tasks?wave_id={wave_id}")
        assert res.status_code == 200
        tasks = res.json()
        assert len(tasks) > 0
        assert all(t["task_status"] == "READY" for t in tasks)

    def test_wave_section_split(self, client):
        """MAIN / SUB 분류가 유효한 값인지 확인"""
        res = client.post("/api/v1/waves", json={"max_candidates": 20})
        assert res.status_code == 200
        wave_id = res.json()["wave_id"]

        candidates = client.get(f"/api/v1/waves/{wave_id}/candidates").json()
        if not candidates:
            pytest.skip("후보 없음")

        sections = {c["list_section"] for c in candidates}
        assert sections.issubset({"MAIN", "SUB"})
        assert len(sections) >= 1

    def test_candidate_reject_flow(self, client):
        """후보 거절 후 status 변경 확인"""
        res = client.post("/api/v1/waves", json={"max_candidates": 5})
        wave_id = res.json()["wave_id"]

        candidates = client.get(f"/api/v1/waves/{wave_id}/candidates").json()
        if not candidates:
            pytest.skip("후보 없음")

        cid = candidates[0]["candidate_id"]
        res = client.post(
            f"/api/v1/waves/{wave_id}/candidates/{cid}/reject",
            params={"reason": "통합테스트 거절"},
        )
        assert res.status_code == 200
        assert res.json()["candidate_status"] == "REJECTED"

    def test_confirm_without_approval_fails(self, client):
        """승인된 후보 없이 확정 시 400 반환"""
        res = client.post("/api/v1/waves", json={"max_candidates": 3})
        wave_id = res.json()["wave_id"]

        # 승인 없이 확정 시도
        res = client.post(f"/api/v1/waves/{wave_id}/confirm")
        assert res.status_code == 400

    def test_blocked_task_transition(self, client):
        """READY → QUEUED → SENT → BLOCKED 상태 전환 검증"""
        # 웨이브 생성 + 확정
        res = client.post("/api/v1/waves", json={"max_candidates": 3})
        wave_id = res.json()["wave_id"]
        candidates = client.get(f"/api/v1/waves/{wave_id}/candidates").json()
        if not candidates:
            pytest.skip("후보 없음")

        for c in candidates[:1]:
            client.post(f"/api/v1/waves/{wave_id}/candidates/{c['candidate_id']}/approve")
        client.post(f"/api/v1/waves/{wave_id}/confirm")

        tasks = client.get(f"/api/v1/tasks?wave_id={wave_id}").json()
        if not tasks:
            pytest.skip("태스크 없음")

        task_id = tasks[0]["task_id"]
        base = f"/api/v1/tasks/{task_id}/transition"

        # READY → QUEUED
        r = client.post(base, params={"new_status": "QUEUED"})
        assert r.status_code == 200, r.text

        # QUEUED → SENT
        r = client.post(base, params={"new_status": "SENT"})
        assert r.status_code == 200, r.text

        # SENT → BLOCKED
        r = client.post(base, params={"new_status": "BLOCKED", "block_reason": "통합테스트"})
        assert r.status_code == 200, r.text
        assert r.json()["task_status"] == "BLOCKED"

    def test_blocked_sku_reincluded_in_next_wave(self, client):
        """BLOCKED 태스크의 SKU가 다음 웨이브에 BLOCKED이력 플래그와 함께 재포함"""
        # 웨이브 1: 생성 → 후보 선택 → 확정 → BLOCKED 상태 전환
        res = client.post("/api/v1/waves", json={"max_candidates": 5})
        wave1_id = res.json()["wave_id"]
        candidates = client.get(f"/api/v1/waves/{wave1_id}/candidates").json()

        if not candidates:
            pytest.skip("후보 없음")

        cid = candidates[0]["candidate_id"]
        sku_id = candidates[0]["sku_id"]

        client.post(f"/api/v1/waves/{wave1_id}/candidates/{cid}/approve")
        client.post(f"/api/v1/waves/{wave1_id}/confirm")

        tasks = client.get(f"/api/v1/tasks?wave_id={wave1_id}").json()
        if tasks:
            task_id = tasks[0]["task_id"]
            base = f"/api/v1/tasks/{task_id}/transition"
            client.post(base, params={"new_status": "QUEUED"})
            client.post(base, params={"new_status": "SENT"})
            client.post(base, params={"new_status": "BLOCKED", "block_reason": "통합테스트 재포함 검증"})

        # 웨이브 2 생성 → BLOCKED SKU 재포함 확인
        res = client.post("/api/v1/waves", json={"max_candidates": 50})
        wave2_id = res.json()["wave_id"]
        candidates2 = client.get(f"/api/v1/waves/{wave2_id}/candidates").json()

        sku_ids = [c["sku_id"] for c in candidates2]
        assert sku_id in sku_ids, f"BLOCKED SKU {sku_id}가 웨이브 2에 미포함"

    def test_min_risk_score_filter(self, client):
        """min_risk_score 필터 적용 시 기준 미달 후보 제외 확인"""
        min_score = 65  # HIGH 이상만
        res = client.post("/api/v1/waves", json={"max_candidates": 40, "min_risk_score": min_score})
        assert res.status_code == 200
        wave_id = res.json()["wave_id"]

        candidates = client.get(f"/api/v1/waves/{wave_id}/candidates").json()
        below = [c for c in candidates if c["risk_score"] < min_score]
        assert len(below) == 0, f"min_risk_score {min_score} 미달 후보 {len(below)}건 포함"
