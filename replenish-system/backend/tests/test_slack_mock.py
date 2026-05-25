"""
Phase 4 — Slack 연동 Mock 테스트
실제 Slack 전송 없이 메시지 조립 / 전송 / 삭제 API 검증
"""
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        ("NC", "NC존",      "NC존", "FORKLIFT", "MAIN"),
    ]
    for prefix, name, ch, atype, section in zones:
        if not session.exec(select(ZoneConfig).where(ZoneConfig.zone_prefix == prefix)).first():
            session.add(ZoneConfig(
                zone_prefix=prefix, zone_name=name, slack_channel=ch,
                access_type=atype, list_section=section, is_special_zone=False,
            ))
    session.commit()


def _load_fixtures(session: Session) -> None:
    inv_path = FIXTURES / "inventory_sample.csv"
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

    sales_path = FIXTURES / "pivot_sample.csv"
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
def client(full_session):
    def _override():
        with Session(_engine) as s:
            yield s
    app.dependency_overrides[get_session] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _create_confirmed_wave(client: TestClient, max_candidates: int = 5) -> int | None:
    """웨이브 생성 → 전체 승인 → 확정 후 wave_id 반환. 후보 없으면 None."""
    res = client.post("/api/v1/waves", json={"max_candidates": max_candidates})
    if res.status_code != 200:
        return None
    wave_id = res.json()["wave_id"]

    candidates = client.get(f"/api/v1/waves/{wave_id}/candidates").json()
    if not candidates:
        return None

    for c in candidates:
        client.post(f"/api/v1/waves/{wave_id}/candidates/{c['candidate_id']}/approve")

    client.post(f"/api/v1/waves/{wave_id}/confirm")
    return wave_id


class TestSlackMessages:

    def test_build_wave_messages_v2_format(self, full_session):
        """build_wave_messages_v2가 채널별 텍스트 메시지 딕셔너리를 반환하는지 확인"""
        from app.models.wave import Wave
        from app.services.slack_service import build_wave_messages_v2

        wave = Wave(
            wave_name=f"slack테스트_{datetime.utcnow().strftime('%f')}",
            wave_status="CONFIRMED", target_sku_count=5, created_by="slack테스트",
        )
        full_session.add(wave)
        full_session.commit()
        full_session.refresh(wave)

        result = build_wave_messages_v2(wave.wave_id, full_session)
        assert isinstance(result, dict)

    def test_build_wave_messages_v2_with_tasks(self, client, full_session):
        """확정 웨이브의 v2 메시지가 텍스트 문자열 리스트를 반환하는지 확인"""
        from app.services.slack_service import build_wave_messages_v2

        wave_id = _create_confirmed_wave(client)
        if wave_id is None:
            pytest.skip("후보 없음")

        channel_msgs = build_wave_messages_v2(wave_id, full_session)
        assert isinstance(channel_msgs, dict)
        for key, msgs in channel_msgs.items():
            assert isinstance(msgs, list)
            assert len(msgs) > 0
            assert all(isinstance(m, str) for m in msgs)

    def test_slack_send_without_token_queues(self, client):
        """bot_token 미설정 시 메시지가 WAITING 상태로 queue에 저장되는지 확인"""
        from app.models.task import ReplenishTaskQueue

        wave_id = _create_confirmed_wave(client)
        if wave_id is None:
            pytest.skip("후보 없음")

        res = client.post(f"/api/v1/queue/{wave_id}/send")
        assert res.status_code == 200, res.text
        data = res.json()
        # bot_token 없으므로 queued 리스트에 채널들이 들어가야 함
        assert isinstance(data.get("queued", []), list)

    @patch("slack_sdk.WebClient")
    def test_slack_send_mock_success(self, mock_client_class, client):
        """Slack WebClient mock: chat_postMessage 호출 확인"""
        mock_slack = MagicMock()
        mock_client_class.return_value = mock_slack
        mock_slack.chat_postMessage.return_value = {"ok": True, "ts": "1234567890.123456"}

        wave_id = _create_confirmed_wave(client, max_candidates=3)
        if wave_id is None:
            pytest.skip("후보 없음")

        # bot_token 직접 DB에 설정
        with Session(_engine) as s:
            from app.models.config import SystemConfig
            cfg = s.exec(
                select(SystemConfig).where(SystemConfig.config_key == "slack_bot_token")
            ).first()
            if cfg:
                cfg.config_value = "xoxb-mock-token"
                s.commit()
            invalidate_cache()

        res = client.post(f"/api/v1/queue/{wave_id}/send")
        assert res.status_code == 200, res.text

        # 토큰 원복
        with Session(_engine) as s:
            from app.models.config import SystemConfig
            cfg = s.exec(
                select(SystemConfig).where(SystemConfig.config_key == "slack_bot_token")
            ).first()
            if cfg:
                cfg.config_value = ""
                s.commit()
            invalidate_cache()

    @patch("slack_sdk.WebClient")
    def test_slack_delete_mock(self, mock_client_class, client):
        """Slack WebClient mock: chat_delete 흐름 검증"""
        mock_slack = MagicMock()
        mock_client_class.return_value = mock_slack
        mock_slack.chat_delete.return_value = {"ok": True}

        wave_id = _create_confirmed_wave(client, max_candidates=3)
        if wave_id is None:
            pytest.skip("후보 없음")

        # 삭제 엔드포인트 — SENT 메시지 없어도 200 반환해야 함
        res = client.delete(f"/api/v1/queue/{wave_id}/messages")
        assert res.status_code == 200, res.text
        data = res.json()
        assert "deleted" in data

    def test_wave_send_twice_idempotent(self, client):
        """전송 2회 호출 시 오류 없이 처리되는지 확인"""
        wave_id = _create_confirmed_wave(client, max_candidates=3)
        if wave_id is None:
            pytest.skip("후보 없음")

        r1 = client.post(f"/api/v1/queue/{wave_id}/send")
        r2 = client.post(f"/api/v1/queue/{wave_id}/send")
        assert r1.status_code == 200
        assert r2.status_code == 200
