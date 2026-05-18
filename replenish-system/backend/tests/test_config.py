import pytest
from unittest.mock import patch, MagicMock
from sqlmodel import Session, select

from app.core.config import get_config, get_config_list, invalidate_cache, _cache
from app.models.config import SystemConfig


class TestGetConfig:
    def test_int_cast(self, session):
        result = get_config("wave_default_sku_count", session, cast=int)
        assert result == 40

    def test_float_cast(self, session):
        result = get_config("target_days_morning", session, cast=float)
        assert result == 2.0

    def test_string_no_cast(self, session):
        result = get_config("bin_id_pattern", session)
        assert isinstance(result, str)
        assert "15" in result

    def test_missing_key_raises_key_error(self, session):
        with pytest.raises(KeyError):
            get_config("nonexistent_key_xyz", session)


class TestGetConfigList:
    def test_score_boundary_hours(self, session):
        result = get_config_list("score_boundary_hours", session, cast=int)
        assert result == [0, 1, 2, 4, 6, 8]

    def test_score_boundary_values(self, session):
        result = get_config_list("score_boundary_values", session, cast=int)
        assert result == [100, 90, 75, 55, 35, 15, 0]

    def test_exclude_zone_patterns_as_str(self, session):
        result = get_config_list("exclude_zone_patterns", session, cast=str)
        assert "PKMOVE01" in result
        assert "STOP" in result


class TestCacheBehavior:
    def test_cache_populated_after_first_read(self, session):
        invalidate_cache()
        assert "wave_default_sku_count" not in _cache
        get_config("wave_default_sku_count", session)
        assert "wave_default_sku_count" in _cache

    def test_no_db_query_on_cache_hit(self, session):
        invalidate_cache()
        get_config("wave_default_sku_count", session)

        call_count = [0]
        original_exec = session.exec

        def counting_exec(stmt):
            call_count[0] += 1
            return original_exec(stmt)

        with patch.object(session, "exec", side_effect=counting_exec):
            get_config("wave_default_sku_count", session)

        assert call_count[0] == 0, "캐시 히트 시 DB 재조회 없어야 함"

    def test_requery_after_invalidate(self, session):
        get_config("wave_default_sku_count", session)
        invalidate_cache()
        assert "wave_default_sku_count" not in _cache

        result = get_config("wave_default_sku_count", session, cast=int)
        assert result == 40
        assert "wave_default_sku_count" in _cache

    def test_same_key_twice_same_result(self, session):
        r1 = get_config("operating_hours_per_day", session, cast=int)
        r2 = get_config("operating_hours_per_day", session, cast=int)
        assert r1 == r2 == 16
