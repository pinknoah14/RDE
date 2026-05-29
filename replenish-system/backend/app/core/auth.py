"""공유 PIN 기반 인증 — 무상태(stateless) HMAC 토큰.

설계:
- 비밀키 = admin_pin (별도 환경변수 불필요). PIN이 바뀌면 기존 토큰 자동 무효화.
- 토큰 형식: "{만료_unix}.{HMAC-SHA256(key=pin, msg=만료)}"
- admin_pin 이 비어 있으면 인증 비활성화(기존 동작 유지, 하위 호환).

운영 진입 절차: 시스템 설정 → SYSTEM → admin_pin 에 PIN 입력하면 즉시 인증 활성화.
"""
import hashlib
import hmac
import time

from sqlmodel import Session

from app.core.config import get_config

# 토큰 유효 기간 (초) — 1교대 + 인계 여유 고려 24시간
TOKEN_TTL_SECONDS = 24 * 60 * 60


def get_admin_pin(session: Session) -> str:
    """현재 설정된 admin_pin. 미설정이면 빈 문자열."""
    try:
        return get_config("admin_pin", session) or ""
    except KeyError:
        return ""


def auth_enabled(session: Session) -> bool:
    """PIN이 설정돼 있으면 인증 활성."""
    return bool(get_admin_pin(session))


def issue_token(pin: str, ttl: int = TOKEN_TTL_SECONDS) -> str:
    exp = int(time.time()) + ttl
    sig = hmac.new(pin.encode(), str(exp).encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def verify_token(pin: str, token: str) -> bool:
    if not token:
        return False
    try:
        exp_str, sig = token.split(".", 1)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        return False
    if exp < int(time.time()):
        return False
    expected = hmac.new(pin.encode(), exp_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)
