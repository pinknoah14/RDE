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
    is_special_zone: bool = Field(default=False, nullable=False)
    is_active: bool = Field(default=True, nullable=False)
    memo: Optional[str] = None
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
