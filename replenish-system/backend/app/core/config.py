"""런타임 시스템 설정 캐시 + 환경 변수 로더.

- SystemConfig DB 캐시: get_config / get_config_list / invalidate_cache
- 환경 변수 (.env): DB_PATH / HOST / PORT / FRONTEND_URL / LOG_LEVEL / LOG_TO_FILE / LOG_FILE_PATH
"""
import os
from pathlib import Path
from typing import Any, Callable, TypeVar

from sqlmodel import Session, select

from app.models.config import SystemConfig

# ---------------------------------------------------------------------------
# 환경 변수 (.env) 로드
# ---------------------------------------------------------------------------

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / ".env")
except ImportError:
    pass  # python-dotenv 미설치 시에도 OS 환경 변수만으로 동작


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


DB_PATH       = Path(_env("RDE_DB_PATH", "data/replenish.db"))
HOST          = _env("HOST", "0.0.0.0")
PORT          = int(_env("PORT", "8000"))
FRONTEND_URL  = _env("FRONTEND_URL", "http://localhost:3000")
LOG_LEVEL     = _env("LOG_LEVEL", "INFO")
LOG_TO_FILE   = _env("LOG_TO_FILE", "false").lower() == "true"
LOG_FILE_PATH = Path(_env("LOG_FILE_PATH", "logs/rde.log"))


# ---------------------------------------------------------------------------
# SystemConfig 런타임 캐시
# ---------------------------------------------------------------------------

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
