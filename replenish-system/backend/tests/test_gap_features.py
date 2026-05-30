"""GAP-03 / GAP-06 신규 엔드포인트·서비스 회귀 테스트.

- GAP-03: app/api/schedule.py (pre_break_sweep, cutoff_boost)
- GAP-06: app/services/slack_service.py 재시도 로직 (_post_with_retry,
          send_wave_messages 재시도, retry_failed_messages)
          및 app/api/slack.py retry_failed 라우트
"""
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

import app.models  # noqa: F401 — 모델 등록
from app.api.schedule import cutoff_boost, pre_break_sweep
from app.api.slack import retry_failed
from app.core.config import invalidate_cache
from app.core.database import seed_system_config
from app.core.exceptions import RDEException
from app.models.config import SystemConfig
from app.models.task import ReplenishConfirmedTask, ReplenishTaskQueue
from app.models.wave import Wave
from app.models.zone import ZoneConfig
from app.services.slack_service import (
    _post_with_retry,
    retry_failed_messages,
    send_wave_messages,
)


@pytest.fixture
def db():
    invalidate_cache()
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        seed_system_config(s)
        s.add(ZoneConfig(
            zone_prefix="RA",
            zone_name="R존 메인",
            slack_channel="R존",
            slack_channel_id="C_TEST_R",
            access_type="FORKLIFT",
            list_section="MAIN",
            is_special_zone=False,
        ))
        s.commit()
        yield s
    invalidate_cache()


def _set_cfg(db, key, value):
    """SystemConfig 값 설정 + 캐시 무효화 (set_config 헬퍼 부재 대응)."""
    row = db.exec(select(SystemConfig).where(SystemConfig.config_key == key)).first()
    if row:
        row.config_value = value
    else:
        db.add(SystemConfig(
            config_key=key, config_value=value,
            value_type="STRING", category="SLACK", description="",
        ))
    db.commit()
    invalidate_cache()


def _make_wave(db, status="CONFIRMED", wave_type="REGULAR"):
    wave = Wave(
        wave_name="테스트웨이브",
        wave_type=wave_type,
        wave_status=status,
        target_sku_count=5,
        created_by="테스트",
    )
    db.add(wave)
    db.commit()
    db.refresh(wave)
    return wave


def _add_task(db, wave_id, status="READY", seq=1, qty=10, sku="SKU0"):
    task = ReplenishConfirmedTask(
        wave_id=wave_id,
        candidate_id=None,
        sku_id=sku,
        sku_name=f"상품_{sku}",
        picking_bin="RA-01-01",
        zone="RA",
        total_qty=qty,
        task_status=status,
        slack_channel="R존",
        worker_type="FORKLIFT",
        confirm_type="AUTO",
        confirmed_by="테스트",
        section_seq=seq,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


# ────────────────────────────────────────────────────────────────
# GAP-06: _post_with_retry 백오프 로직
# ────────────────────────────────────────────────────────────────

def test_post_with_retry_succeeds_first_try():
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": "111.222"}
    with patch("app.services.slack_service.time.sleep") as sleep:
        ok, ts, err, attempts = _post_with_retry(client, "ch", "msg", 3, 1)
    assert ok is True
    assert ts == "111.222"
    assert err is None
    assert attempts == 1
    sleep.assert_not_called()  # 첫 시도 성공 → 대기 없음


def test_post_with_retry_recovers_after_failures():
    client = MagicMock()
    # 2회 실패 후 3회차 성공
    client.chat_postMessage.side_effect = [
        Exception("boom1"),
        Exception("boom2"),
        {"ts": "333.444"},
    ]
    with patch("app.services.slack_service.time.sleep") as sleep:
        ok, ts, err, attempts = _post_with_retry(client, "ch", "msg", 3, 1)
    assert ok is True
    assert ts == "333.444"
    assert attempts == 3
    # 지수 백오프: 1s, 2s (마지막 성공 전 2회 대기)
    assert sleep.call_count == 2
    assert sleep.call_args_list[0].args[0] == 1
    assert sleep.call_args_list[1].args[0] == 2


def test_post_with_retry_exhausts_and_fails():
    client = MagicMock()
    client.chat_postMessage.side_effect = Exception("persistent")
    with patch("app.services.slack_service.time.sleep") as sleep:
        ok, ts, err, attempts = _post_with_retry(client, "ch", "msg", 3, 1)
    assert ok is False
    assert ts is None
    assert "persistent" in err
    assert attempts == 3
    # 마지막 시도 후엔 대기하지 않음 → 시도 3회 중 2회만 sleep
    assert sleep.call_count == 2


# ────────────────────────────────────────────────────────────────
# GAP-06: send_wave_messages 재시도 통합 + retry_failed_messages
# ────────────────────────────────────────────────────────────────

def test_send_marks_failed_after_exhausting_retries(db):
    wave = _make_wave(db)
    _add_task(db, wave.wave_id)
    _set_cfg(db, "slack_bot_token", "xoxb-test")
    with patch("app.services.slack_service.WebClient") as MockClient, \
         patch("app.services.slack_service.time.sleep"):
        MockClient.return_value.chat_postMessage.side_effect = Exception("down")
        result = send_wave_messages(wave.wave_id, db)
    assert len(result["failed"]) >= 1
    failed = db.exec(
        select(ReplenishTaskQueue).where(ReplenishTaskQueue.queue_status == "FAILED")
    ).all()
    assert len(failed) >= 1
    assert failed[0].error_message is not None


def test_retry_failed_messages_resends_only_failed(db):
    wave = _make_wave(db)
    _add_task(db, wave.wave_id)
    _set_cfg(db, "slack_bot_token", "xoxb-test")
    # 1차 전송 실패 → FAILED 적재
    with patch("app.services.slack_service.WebClient") as MockClient, \
         patch("app.services.slack_service.time.sleep"):
        MockClient.return_value.chat_postMessage.side_effect = Exception("down")
        send_wave_messages(wave.wave_id, db)
    # 2차 재시도 → 성공
    with patch("app.services.slack_service.WebClient") as MockClient, \
         patch("app.services.slack_service.time.sleep"):
        MockClient.return_value.chat_postMessage.return_value = {"ts": "999.000"}
        result = retry_failed_messages(wave.wave_id, db)
    assert len(result["retried"]) >= 1
    assert result["still_failed"] == []
    remaining = db.exec(
        select(ReplenishTaskQueue).where(ReplenishTaskQueue.queue_status == "FAILED")
    ).all()
    assert len(remaining) == 0


def test_retry_failed_without_token_noop(db):
    wave = _make_wave(db)
    # seed 기본값에서 slack_bot_token이 빈 문자열 → 토큰 없음 분기
    result = retry_failed_messages(wave.wave_id, db)
    assert result["retried"] == []
    assert result["skipped"] == -1  # 토큰 없음 신호


def test_retry_failed_route_404_on_missing_wave(db):
    with pytest.raises(RDEException) as exc:
        retry_failed(wave_id=99999, session=db)
    assert exc.value.status_code == 404


# ────────────────────────────────────────────────────────────────
# GAP-03: pre_break_sweep
# ────────────────────────────────────────────────────────────────

def test_pre_break_sweep_cancels_ready_only(db):
    wave = _make_wave(db)
    t_ready = _add_task(db, wave.wave_id, status="READY", sku="A")
    t_sent = _add_task(db, wave.wave_id, status="SENT", sku="B")
    t_queued = _add_task(db, wave.wave_id, status="QUEUED", sku="C")

    result = pre_break_sweep(actor="관리자", wave_id=wave.wave_id, session=db)

    assert result["cancelled"] == 1
    db.refresh(t_ready)
    db.refresh(t_sent)
    db.refresh(t_queued)
    assert t_ready.task_status == "CANCELLED"
    assert t_ready.cancel_reason == "PRE_BREAK_SWEEP"
    # 진행 중(SENT/QUEUED)은 보존
    assert t_sent.task_status == "SENT"
    assert t_queued.task_status == "QUEUED"


def test_pre_break_sweep_no_active_wave(db):
    # 활성 웨이브 없음 (DRAFT만 존재) → wave_id 미지정 시 0건
    _make_wave(db, status="DRAFT")
    result = pre_break_sweep(actor="관리자", wave_id=None, session=db)
    assert result["cancelled"] == 0


def test_pre_break_sweep_all_active_waves(db):
    wave = _make_wave(db, status="CONFIRMED")
    _add_task(db, wave.wave_id, status="READY", sku="A")
    _add_task(db, wave.wave_id, status="READY", sku="B")
    result = pre_break_sweep(actor="관리자", wave_id=None, session=db)
    assert result["cancelled"] == 2


# ────────────────────────────────────────────────────────────────
# GAP-03: cutoff_boost
# ────────────────────────────────────────────────────────────────

def test_cutoff_boost_cancels_wave_when_no_candidates(db):
    # 재고/판매 데이터 없음 → 추천 0건 → 웨이브 취소 처리
    result = cutoff_boost(
        center_cd="GGH1", min_risk_level="HIGH", actor="관리자", session=db
    )
    assert result["confirmed"] is False
    assert result["candidates"] == 0
    wave = db.get(Wave, result["wave_id"])
    assert wave.wave_status == "CANCELLED"
