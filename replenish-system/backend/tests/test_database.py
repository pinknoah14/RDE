import sqlite3

import pytest
from sqlmodel import Session, create_engine, SQLModel, select, text

import app.models  # noqa: F401
from app.core.database import seed_system_config, SYSTEM_CONFIG_SEED
from app.models.config import SystemConfig


REQUIRED_KEYS = [
    "slack_bot_token",
    "wave_default_sku_count",
    "score_boundary_hours",
    "bin_id_pattern",
    "exclude_zone_patterns",
    "operating_hours_per_day",
]


@pytest.fixture
def mem_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return engine


class TestInitDb:
    def test_all_16_tables_exist(self, mem_engine):
        expected = {
            "upload_sessions",
            "waves",
            "replenish_candidates",
            "replenish_confirmed_tasks",
            "replenish_task_locations",
            "replenish_task_queue",
            "workers",
            "sku_sales_summary",
            "sku_picking_history",
            "daily_sales_history",
            "zone_config",
            "unknown_zone_flags",
            "picking_zone_master",
            "system_config",
            "audit_log",
            "events",
        }
        with mem_engine.connect() as conn:
            result = conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ))
            actual = {row[0] for row in result}
        assert expected == actual, f"missing: {expected - actual}, extra: {actual - expected}"

    def test_table_count_is_16(self, mem_engine):
        with mem_engine.connect() as conn:
            result = conn.execute(text(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ))
            count = result.scalar()
        assert count == 16


class TestSeedSystemConfig:
    def test_seed_count_gte_37(self, mem_engine):
        with Session(mem_engine) as s:
            seed_system_config(s)
            count = s.exec(select(SystemConfig)).all()
        assert len(count) >= 37

    def test_seed_is_idempotent(self, mem_engine):
        with Session(mem_engine) as s:
            seed_system_config(s)
            seed_system_config(s)
            rows = s.exec(select(SystemConfig)).all()
        assert len(rows) == len(SYSTEM_CONFIG_SEED)

    def test_required_keys_exist(self, mem_engine):
        with Session(mem_engine) as s:
            seed_system_config(s)
            for key in REQUIRED_KEYS:
                row = s.exec(
                    select(SystemConfig).where(SystemConfig.config_key == key)
                ).first()
                assert row is not None, f"키 없음: {key}"

    def test_wave_default_sku_count_value(self, mem_engine):
        with Session(mem_engine) as s:
            seed_system_config(s)
            row = s.exec(
                select(SystemConfig).where(SystemConfig.config_key == "wave_default_sku_count")
            ).first()
        assert row.config_value == "40"


class TestSqlitePragma:
    def test_wal_mode(self, tmp_path):
        db_file = tmp_path / "test.db"
        engine = create_engine(
            f"sqlite:///{db_file}",
            connect_args={"check_same_thread": False},
        )
        from sqlalchemy import event as sa_event

        @sa_event.listens_for(engine, "connect")
        def _set_pragma(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=10000")
            cur.close()

        SQLModel.metadata.create_all(engine)

        # journal_mode is database-level (persisted), busy_timeout is connection-level
        # Check both via the engine's own connection to see what it actually sets
        with engine.connect() as conn:
            journal = conn.execute(text("PRAGMA journal_mode")).fetchone()[0]
            busy = conn.execute(text("PRAGMA busy_timeout")).fetchone()[0]

        assert journal == "wal"
        assert busy == 10000
