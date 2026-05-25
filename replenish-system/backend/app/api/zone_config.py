from datetime import datetime
from typing import Any
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.dependencies import get_session
from app.core.exceptions import RDEException
from app.models.zone import FloorAccessPoint, ScatteredAisleAnchor, ZoneConfig, UnknownZoneFlag, PickingZoneMaster

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
        raise RDEException(code="ZONE_NOT_FOUND", message="존 설정을 찾을 수 없습니다.", detail=f"zone_config_id={zone_config_id}", status_code=404)
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
        raise RDEException(code="ZONE_NOT_FOUND", message="존 설정을 찾을 수 없습니다.", detail=f"zone_config_id={zone_config_id}", status_code=404)
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


# ---------------------------------------------------------------------------
# 연속 존 좌표 설정 (v1.7)
# ---------------------------------------------------------------------------

class ZoneLayoutUpdate(BaseModel):
    floor: int = 0
    is_scattered: bool = False
    origin_x: float | None = None
    origin_y: float | None = None
    aisle_direction: str = "y"
    aisle_gap: float = 3.0
    bay_gap: float = 1.5


@router.get("/{zone_code}/layout")
def get_zone_layout(zone_code: str, session: Session = Depends(get_session)) -> Any:
    zone = session.exec(select(ZoneConfig).where(ZoneConfig.zone_prefix == zone_code)).first()
    if not zone:
        raise RDEException(code="ZONE_NOT_FOUND", message="존을 찾을 수 없습니다.", detail=f"zone_code={zone_code}", status_code=404)
    return {
        "zone_prefix": zone.zone_prefix,
        "floor": zone.floor,
        "is_scattered": zone.is_scattered,
        "origin_x": zone.origin_x,
        "origin_y": zone.origin_y,
        "aisle_direction": zone.aisle_direction,
        "aisle_gap": zone.aisle_gap,
        "bay_gap": zone.bay_gap,
    }


@router.put("/{zone_code}/layout")
def update_zone_layout(
    zone_code: str,
    body: ZoneLayoutUpdate,
    session: Session = Depends(get_session),
) -> Any:
    zone = session.exec(select(ZoneConfig).where(ZoneConfig.zone_prefix == zone_code)).first()
    if not zone:
        raise RDEException(code="ZONE_NOT_FOUND", message="존을 찾을 수 없습니다.", detail=f"zone_code={zone_code}", status_code=404)
    for f, v in body.model_dump().items():
        setattr(zone, f, v)
    zone.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(zone)
    return zone


# ---------------------------------------------------------------------------
# 산재 존 통로별 앵커 (v1.7)
# ---------------------------------------------------------------------------

class AisleAnchorItem(BaseModel):
    aisle_no: int
    anchor_x: float
    anchor_y: float
    floor: int = 0


@router.get("/{zone_code}/aisle-anchors")
def get_aisle_anchors(zone_code: str, session: Session = Depends(get_session)) -> list[Any]:
    return session.exec(
        select(ScatteredAisleAnchor).where(ScatteredAisleAnchor.zone_prefix == zone_code)
    ).all()


@router.put("/{zone_code}/aisle-anchors")
def upsert_aisle_anchors(
    zone_code: str,
    body: list[AisleAnchorItem],
    session: Session = Depends(get_session),
) -> Any:
    for item in body:
        existing = session.exec(
            select(ScatteredAisleAnchor).where(
                ScatteredAisleAnchor.zone_prefix == zone_code,
                ScatteredAisleAnchor.aisle_no == item.aisle_no,
            )
        ).first()
        if existing:
            existing.anchor_x = item.anchor_x
            existing.anchor_y = item.anchor_y
            existing.floor = item.floor
        else:
            session.add(ScatteredAisleAnchor(
                zone_prefix=zone_code,
                aisle_no=item.aisle_no,
                anchor_x=item.anchor_x,
                anchor_y=item.anchor_y,
                floor=item.floor,
            ))
    session.commit()
    return session.exec(
        select(ScatteredAisleAnchor).where(ScatteredAisleAnchor.zone_prefix == zone_code)
    ).all()


# ---------------------------------------------------------------------------
# 계단 / 리프트 관리 (v1.7)
# ---------------------------------------------------------------------------

floor_ap_router = APIRouter()


class FloorAPCreate(BaseModel):
    name: str
    x: float
    y: float
    access_type: str = "STAIRS"
    is_active: bool = True


class FloorAPUpdate(BaseModel):
    name: str | None = None
    x: float | None = None
    y: float | None = None
    access_type: str | None = None
    is_active: bool | None = None


@floor_ap_router.get("")
def list_floor_access_points(session: Session = Depends(get_session)) -> list[Any]:
    return session.exec(select(FloorAccessPoint)).all()


@floor_ap_router.post("")
def create_floor_access_point(body: FloorAPCreate, session: Session = Depends(get_session)) -> Any:
    ap = FloorAccessPoint(**body.model_dump())
    session.add(ap)
    session.commit()
    session.refresh(ap)
    return ap


@floor_ap_router.put("/{access_id}")
def update_floor_access_point(
    access_id: int,
    body: FloorAPUpdate,
    session: Session = Depends(get_session),
) -> Any:
    ap = session.get(FloorAccessPoint, access_id)
    if not ap:
        raise RDEException(code="ACCESS_POINT_NOT_FOUND", message="접근 포인트를 찾을 수 없습니다.", detail=f"access_id={access_id}", status_code=404)
    for f, v in body.model_dump(exclude_none=True).items():
        setattr(ap, f, v)
    session.commit()
    session.refresh(ap)
    return ap


@floor_ap_router.delete("/{access_id}")
def delete_floor_access_point(access_id: int, session: Session = Depends(get_session)) -> Any:
    ap = session.get(FloorAccessPoint, access_id)
    if not ap:
        raise RDEException(code="ACCESS_POINT_NOT_FOUND", message="접근 포인트를 찾을 수 없습니다.", detail=f"access_id={access_id}", status_code=404)
    session.delete(ap)
    session.commit()
    return {"deleted": access_id}


# ---------------------------------------------------------------------------
# 피킹지번 마스터 (v1.9)
# ---------------------------------------------------------------------------

picking_router = APIRouter()


class PickingZoneCreate(BaseModel):
    bin_id: str
    zone: str
    memo: str | None = None


class PickingZoneUpdate(BaseModel):
    is_active: bool | None = None
    memo: str | None = None
    zone: str | None = None


@picking_router.get("")
def list_picking_zones(
    q: str | None = Query(default=None),
    limit: int = Query(default=200, le=1000),
    session: Session = Depends(get_session),
) -> list[Any]:
    stmt = select(PickingZoneMaster)
    if q:
        stmt = stmt.where(PickingZoneMaster.bin_id.contains(q))
    return session.exec(stmt.order_by(PickingZoneMaster.bin_id).limit(limit)).all()


@picking_router.post("")
def create_picking_zone(
    body: PickingZoneCreate,
    session: Session = Depends(get_session),
) -> Any:
    existing = session.get(PickingZoneMaster, body.bin_id)
    if existing:
        raise RDEException(code="PICKING_ZONE_DUPLICATE", message="이미 등록된 지번입니다.", detail=f"bin_id={body.bin_id}", status_code=409)
    pz = PickingZoneMaster(**body.model_dump())
    session.add(pz)
    session.commit()
    session.refresh(pz)
    return pz


@picking_router.patch("/{bin_id}")
def update_picking_zone(
    bin_id: str,
    body: PickingZoneUpdate,
    session: Session = Depends(get_session),
) -> Any:
    pz = session.get(PickingZoneMaster, bin_id)
    if not pz:
        raise RDEException(code="PICKING_ZONE_NOT_FOUND", message="지번을 찾을 수 없습니다.", detail=f"bin_id={bin_id}", status_code=404)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(pz, field, value)
    pz.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(pz)
    return pz


@picking_router.delete("/{bin_id}")
def delete_picking_zone(bin_id: str, session: Session = Depends(get_session)) -> dict:
    pz = session.get(PickingZoneMaster, bin_id)
    if not pz:
        raise RDEException(code="PICKING_ZONE_NOT_FOUND", message="지번을 찾을 수 없습니다.", detail=f"bin_id={bin_id}", status_code=404)
    session.delete(pz)
    session.commit()
    return {"deleted": bin_id}
