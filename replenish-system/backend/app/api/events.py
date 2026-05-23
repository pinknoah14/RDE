from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.dependencies import get_session
from app.models.event import Event

router = APIRouter()


def _parse_dt(s: str) -> datetime:
    """YYYY-MM-DD 또는 ISO 형식을 datetime으로."""
    if not s:
        raise HTTPException(status_code=400, detail="날짜 형식 오류")
    try:
        if "T" in s or " " in s:
            return datetime.fromisoformat(s.replace("T", " "))
        return datetime.fromisoformat(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"날짜 형식 오류: {s}")


class EventCreate(BaseModel):
    sku_id: str
    event_name: str | None = None
    event_type: str = "EVENT"
    start_date: str       # YYYY-MM-DD
    end_date: str         # YYYY-MM-DD
    registered_by: str = "관리자"
    memo: str | None = None


class EventUpdate(BaseModel):
    event_name: str | None = None
    event_type: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    memo: str | None = None


def _serialize(e: Event) -> dict:
    return {
        "event_id": e.event_id,
        "sku_id": e.sku_id,
        "event_type": e.event_type,
        "event_name": e.event_name,
        "start_date": e.start_dt.date().isoformat() if e.start_dt else None,
        "end_date": e.end_dt.date().isoformat() if e.end_dt else None,
        "registered_by": e.registered_by,
        "memo": e.memo,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.get("")
def list_events(session: Session = Depends(get_session)) -> list[dict]:
    rows = session.exec(select(Event).order_by(Event.start_dt.desc())).all()
    return [_serialize(e) for e in rows]


@router.post("")
def create_event(body: EventCreate, session: Session = Depends(get_session)) -> Any:
    event = Event(
        sku_id=body.sku_id,
        event_type=body.event_type,
        event_name=body.event_name,
        start_dt=_parse_dt(body.start_date),
        end_dt=_parse_dt(body.end_date),
        registered_by=body.registered_by,
        memo=body.memo,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return _serialize(event)


@router.patch("/{event_id}")
def update_event(
    event_id: int,
    body: EventUpdate,
    session: Session = Depends(get_session),
) -> Any:
    event = session.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="이벤트 없음")
    data = body.model_dump(exclude_none=True)
    if "start_date" in data:
        event.start_dt = _parse_dt(data.pop("start_date"))
    if "end_date" in data:
        event.end_dt = _parse_dt(data.pop("end_date"))
    for field, value in data.items():
        setattr(event, field, value)
    session.commit()
    session.refresh(event)
    return _serialize(event)


@router.delete("/{event_id}")
def delete_event(event_id: int, session: Session = Depends(get_session)) -> dict:
    event = session.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="이벤트 없음")
    session.delete(event)
    session.commit()
    return {"deleted": event_id}
