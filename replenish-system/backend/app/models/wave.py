from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class Wave(SQLModel, table=True):
    __tablename__ = "waves"

    wave_id: Optional[int] = Field(default=None, primary_key=True)
    wave_name: str = Field(nullable=False)
    wave_type: str = Field(default="REGULAR", nullable=False)   # REGULAR / URGENT
    wave_status: str = Field(default="DRAFT", nullable=False)
    # DRAFT / CONFIRMED / SENT / COMPLETED / CANCELLED
    upload_session_id: Optional[int] = Field(default=None, foreign_key="upload_sessions.upload_id")
    generation_mode: str = Field(default="CHANNEL", nullable=False)
    target_sku_count: int = Field(nullable=False)
    options_json: Optional[str] = None
    created_by: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    confirmed_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    memo: Optional[str] = None
