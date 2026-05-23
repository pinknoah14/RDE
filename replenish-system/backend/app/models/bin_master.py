from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class BinMaster(SQLModel, table=True):
    __tablename__ = "bin_master"

    bin_id: str = Field(primary_key=True)     # 지번 코드 (예: 15RA0010101)
    center_cd: str = Field(nullable=False)
    zone_prefix: str = Field(nullable=False)   # 존 코드
    bin_type: str = Field(nullable=False)      # PICKING / REPLENISH
    can_receive: bool = Field(default=False)
    can_pick: bool = Field(default=False)
    width_mm: Optional[int] = None
    height_mm: Optional[int] = None
    depth_mm: Optional[int] = None
    cbm: Optional[float] = None
    allow_mixed_product: bool = Field(default=False)
    allow_mixed_lot: bool = Field(default=False)
    description: Optional[str] = None
    status: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
