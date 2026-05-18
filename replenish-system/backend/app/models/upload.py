from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class UploadSession(SQLModel, table=True):
    __tablename__ = "upload_sessions"

    upload_id: Optional[int] = Field(default=None, primary_key=True)
    upload_type: str = Field(nullable=False)          # INVENTORY / SHIPMENT / PIVOT
    file_name: str = Field(nullable=False)
    file_snapshot_dt: Optional[datetime] = None
    uploaded_by: str = Field(nullable=False)
    uploaded_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    record_count: Optional[int] = None
    center_cd: Optional[str] = None
