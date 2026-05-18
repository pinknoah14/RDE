import pytest
from sqlmodel import Session, create_engine, SQLModel

import app.models  # noqa: F401 — 모든 모델 메타데이터 등록
from app.core.database import seed_system_config


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        seed_system_config(s)
        yield s
