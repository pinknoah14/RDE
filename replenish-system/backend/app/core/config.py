from typing import Any, Callable, TypeVar
from sqlmodel import Session, select

from app.models.config import SystemConfig

_cache: dict[str, str] = {}

T = TypeVar("T")


def get_config(key: str, session: Session, cast: Callable[[str], T] | None = None) -> Any:
    if key not in _cache:
        row = session.exec(
            select(SystemConfig).where(SystemConfig.config_key == key)
        ).first()
        if not row:
            raise KeyError(f"system_config 키 없음: {key}")
        _cache[key] = row.config_value
    value = _cache[key]
    if cast is not None:
        return cast(value)
    return value


def get_config_list(key: str, session: Session, cast: Callable[[str], Any] = int) -> list:
    raw = get_config(key, session)
    return [cast(v.strip()) for v in raw.split(",")]


def invalidate_cache():
    _cache.clear()
