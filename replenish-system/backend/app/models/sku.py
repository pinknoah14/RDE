from datetime import datetime, date
from typing import Optional
from sqlmodel import Field, SQLModel


class SkuSalesSummary(SQLModel, table=True):
    __tablename__ = "sku_sales_summary"

    sku_id: str = Field(primary_key=True)
    center_cd: str = Field(primary_key=True)
    sku_name: Optional[str] = None
    catg_1: Optional[str] = None
    catg_2: Optional[str] = None
    base_daily_avg: float = Field(default=0.0)
    recent_daily_avg: float = Field(default=0.0)
    trend_coef: float = Field(default=1.0)
    adjusted_daily: float = Field(default=0.0)
    stockout_flag: bool = Field(default=False)
    event_flag: bool = Field(default=False)
    last_pivot_upload: Optional[datetime] = None
    last_shipment_upload: Optional[datetime] = None
    last_updated: Optional[datetime] = None


class SkuPickingHistory(SQLModel, table=True):
    __tablename__ = "sku_picking_history"

    sku_id: str = Field(primary_key=True)
    center_cd: str = Field(primary_key=True)
    picking_bin: Optional[str] = None
    zone: Optional[str] = None
    last_seen_date: Optional[date] = None
    last_seen_qty: Optional[int] = None
    is_new_sku: bool = Field(default=False)
    manually_assigned: bool = Field(default=False)
    confidence: str = Field(default="NEW", nullable=False)
    # HIGH / MEDIUM / LOW / STALE / NEW
    has_multi_bin: bool = Field(default=False)      # v1.6
    alt_bin_ids: Optional[str] = None               # v1.6: JSON 배열
    assigned_by: Optional[str] = None
    assigned_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DailySalesHistory(SQLModel, table=True):
    __tablename__ = "daily_sales_history"

    history_id: Optional[int] = Field(default=None, primary_key=True)
    sku_id: str = Field(nullable=False)
    center_cd: str = Field(nullable=False)
    sales_date: date = Field(nullable=False)
    sales_qty: int = Field(default=0, nullable=False)
    unassigned_qty: int = Field(default=0, nullable=False)
    upload_session_id: Optional[int] = Field(default=None, foreign_key="upload_sessions.upload_id")
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
