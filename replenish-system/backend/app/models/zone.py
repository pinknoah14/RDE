from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class ZoneConfig(SQLModel, table=True):
    __tablename__ = "zone_config"

    zone_config_id: Optional[int] = Field(default=None, primary_key=True)
    zone_prefix: str = Field(unique=True, nullable=False)
    zone_name: str = Field(nullable=False)
    slack_channel: str = Field(nullable=False)
    slack_channel_id: Optional[str] = None
    access_type: str = Field(nullable=False)        # FORKLIFT / WALKING
    list_section: str = Field(nullable=False)       # MAIN / SUB
    is_special_zone: bool = Field(default=False, nullable=False)  # v1.6 compat, not used in routing
    is_active: bool = Field(default=True, nullable=False)
    memo: Optional[str] = None
    # v1.7: 물리 좌표 시스템
    floor: int = Field(default=0, nullable=False)               # 0=1층, 1=메자닌
    is_scattered: bool = Field(default=False, nullable=False)   # TRUE=산재 존(PW 등)
    origin_x: Optional[float] = None
    origin_y: Optional[float] = None
    aisle_direction: str = Field(default="y")                   # 'y' | 'x'
    aisle_gap: float = Field(default=3.0)
    bay_gap: float = Field(default=1.5)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: Optional[datetime] = None


class UnknownZoneFlag(SQLModel, table=True):
    __tablename__ = "unknown_zone_flags"

    flag_id: Optional[int] = Field(default=None, primary_key=True)
    zone_prefix: str = Field(unique=True, nullable=False)
    sample_bin_id: Optional[str] = None
    first_seen_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    seen_count: int = Field(default=1, nullable=False)
    is_resolved: bool = Field(default=False, nullable=False)
    resolved_at: Optional[datetime] = None


class PickingZoneMaster(SQLModel, table=True):
    __tablename__ = "picking_zone_master"

    bin_id: str = Field(primary_key=True)
    zone: str = Field(nullable=False)
    zone_config_id: Optional[int] = Field(default=None, foreign_key="zone_config.zone_config_id")
    is_active: bool = Field(default=True)
    memo: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: Optional[datetime] = None


class ScatteredAisleAnchor(SQLModel, table=True):
    """산재 존(PW 등)의 통로별 물리 좌표 앵커."""
    __tablename__ = "scattered_aisle_anchor"

    zone_prefix: str = Field(foreign_key="zone_config.zone_prefix", primary_key=True)
    aisle_no: int = Field(primary_key=True)
    anchor_x: float
    anchor_y: float
    floor: int = Field(default=0)   # 산재 존은 통로마다 층이 다를 수 있음


class FloorAccessPoint(SQLModel, table=True):
    """계단 / 리프트 위치 (층 이동 경로 계산용)."""
    __tablename__ = "floor_access_points"

    access_id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(nullable=False)       # "계단1", "리프트"
    x: float
    y: float
    access_type: str = Field(default="STAIRS")  # STAIRS | LIFT
    is_active: bool = Field(default=True)
