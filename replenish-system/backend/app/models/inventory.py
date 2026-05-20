from typing import Optional
from sqlmodel import Field, SQLModel


class ReplenishBinSnapshot(SQLModel, table=True):
    """업로드 시점의 보충존 재고 스냅샷. run_algorithm()이 이 테이블을 읽는다."""
    __tablename__ = "replenish_bin_snapshot"

    snapshot_id: Optional[int] = Field(default=None, primary_key=True)
    upload_session_id: int = Field(foreign_key="upload_sessions.upload_id", nullable=False)
    center_cd: str = Field(nullable=False)
    sku_id: str = Field(nullable=False)
    sku_name: Optional[str] = None
    replenish_bin: str = Field(nullable=False)
    avail_qty: int = Field(default=0)
    unit_size: int = Field(default=1)       # 입수
    deadline_days: Optional[int] = None    # 판매마감일수
    receipt_date: Optional[str] = None     # 입고일자 (TEXT)
