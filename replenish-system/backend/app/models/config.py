from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class SystemConfig(SQLModel, table=True):
    __tablename__ = "system_config"

    config_key: str = Field(primary_key=True)
    config_value: str = Field(nullable=False)
    config_type: str = Field(nullable=False)
    # INTEGER / REAL / TEXT / BOOLEAN / SECRET / CSV_INT / CSV_STR
    config_group: str = Field(nullable=False)
    # SLACK / WAVE / ALGORITHM / PICKING / WORKER / SYSTEM
    label: str = Field(nullable=False)
    description: Optional[str] = None
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None
