from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class Event(SQLModel, table=True):
    __tablename__ = "events"

    event_id: Optional[int] = Field(default=None, primary_key=True)
    sku_id: str = Field(nullable=False)
    event_type: str = Field(nullable=False)
    event_name: Optional[str] = None
    start_dt: datetime = Field(nullable=False)
    end_dt: datetime = Field(nullable=False)
    registered_by: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    memo: Optional[str] = None
