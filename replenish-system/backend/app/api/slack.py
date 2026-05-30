from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.dependencies import get_session
from app.core.exceptions import RDEException
from app.models.wave import Wave
from app.services.slack_service import delete_wave_messages, retry_failed_messages, send_wave_messages

router = APIRouter()


@router.post("/{wave_id}/send")
def send_wave(wave_id: int, session: Session = Depends(get_session)) -> Any:
    wave = session.get(Wave, wave_id)
    if not wave:
        raise RDEException(code="WAVE_NOT_FOUND", message="웨이브를 찾을 수 없습니다.", detail=f"wave_id={wave_id}", status_code=404)
    result = send_wave_messages(wave_id, session)
    wave.wave_status = "SENT"
    wave.sent_at = datetime.utcnow()
    session.commit()
    return result


@router.delete("/{wave_id}/messages")
def delete_messages(wave_id: int, session: Session = Depends(get_session)) -> Any:
    return delete_wave_messages(wave_id, session)


@router.post("/{wave_id}/resend")
def resend_wave(
    wave_id: int,
    session: Session = Depends(get_session),
) -> Any:
    return send_wave_messages(wave_id, session)


@router.post("/{wave_id}/retry-failed")
def retry_failed(
    wave_id: int,
    session: Session = Depends(get_session),
) -> Any:
    """FAILED 상태 큐 항목만 골라서 재전송 (GAP-06)."""
    wave = session.get(Wave, wave_id)
    if not wave:
        raise RDEException(code="WAVE_NOT_FOUND", message="웨이브를 찾을 수 없습니다.", detail=f"wave_id={wave_id}", status_code=404)
    return retry_failed_messages(wave_id, session)
