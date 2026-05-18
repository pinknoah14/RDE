from datetime import datetime
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.dependencies import get_session
from app.models.zone import ZoneConfig, UnknownZoneFlag

router = APIRouter()


class ZoneConfigCreate(BaseModel):
    zone_prefix: str
    zone_name: str
    slack_channel: str
    slack_channel_id: str | None = None
    access_type: str        # FORKLIFT / WALKING
    list_section: str       # MAIN / SUB
    is_special_zone: bool = False
    memo: str | None = None


class ZoneConfigUpdate(BaseModel):
    zone_name: str | None = None
    slack_channel: str | None = None
    slack_channel_id: str | None = None
    access_type: str | None = None
    list_section: str | None = None
    is_special_zone: bool | None = None
    is_active: bool | None = None
    memo: str | None = None


@router.get("")
def list_zone_configs(session: Session = Depends(get_session)) -> list[Any]:
    return session.exec(select(ZoneConfig).order_by(ZoneConfig.zone_prefix)).all()


@router.post("")
def create_zone_config(body: ZoneConfigCreate, session: Session = Depends(get_session)) -> Any:
    zone = ZoneConfig(**body.model_dump())
    session.add(zone)
    session.commit()
    session.refresh(zone)
    return zone


@router.put("/{zone_config_id}")
def update_zone_config(
    zone_config_id: int,
    body: ZoneConfigUpdate,
    session: Session = Depends(get_session),
) -> Any:
    zone = session.get(ZoneConfig, zone_config_id)
    if not zone:
        raise HTTPException(status_code=404, detail="존 설정 없음")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(zone, field, value)
    zone.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(zone)
    return zone


@router.delete("/{zone_config_id}")
def delete_zone_config(zone_config_id: int, session: Session = Depends(get_session)) -> dict:
    zone = session.get(ZoneConfig, zone_config_id)
    if not zone:
        raise HTTPException(status_code=404, detail="존 설정 없음")
    session.delete(zone)
    session.commit()
    return {"deleted": zone_config_id}


@router.get("/unknown-zones")
def list_unknown_zones(session: Session = Depends(get_session)) -> list[Any]:
    return session.exec(
        select(UnknownZoneFlag)
        .where(UnknownZoneFlag.is_resolved == False)  # noqa: E712
        .order_by(UnknownZoneFlag.last_seen_at.desc())
    ).all()
