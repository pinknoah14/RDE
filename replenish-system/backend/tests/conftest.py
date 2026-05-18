from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, SQLModel

import app.models  # noqa: F401
from app.core.database import seed_system_config
from app.core.config import invalidate_cache
from app.models.zone import ZoneConfig


@pytest.fixture(autouse=True)
def clear_cache():
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        seed_system_config(s)
        s.add(ZoneConfig(
            zone_prefix="RA",
            zone_name="R존 메인",
            slack_channel="R존",
            slack_channel_id="C_TEST_R",
            access_type="FORKLIFT",
            list_section="MAIN",
            is_special_zone=False,
        ))
        s.add(ZoneConfig(
            zone_prefix="RB",
            zone_name="R존 B구역",
            slack_channel="R존",
            slack_channel_id="C_TEST_R",
            access_type="FORKLIFT",
            list_section="MAIN",
            is_special_zone=False,
        ))
        s.commit()
        yield s


@pytest.fixture
def sample_csv_path():
    return Path(__file__).parent / "fixtures" / "sample_inventory.csv"
