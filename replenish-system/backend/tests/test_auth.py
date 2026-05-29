"""공유 PIN 인증 — 무상태 토큰 서명/검증 회귀 테스트.

미들웨어는 전역 engine을 직접 읽으므로(get_session 오버라이드 무관) 단위
테스트에서는 토큰 서명/검증 로직을 집중 검증한다. 미들웨어 end-to-end
거동은 수동 통합 검증으로 확인됨.
"""
import hashlib
import hmac
import time

from app.core.auth import issue_token, verify_token, TOKEN_TTL_SECONDS


def test_token_roundtrip():
    """발급한 토큰은 동일 PIN으로 검증 통과."""
    token = issue_token("1234")
    assert verify_token("1234", token) is True


def test_token_wrong_pin_fails():
    """다른 PIN으로는 검증 실패 (PIN 변경 시 기존 토큰 무효화)."""
    token = issue_token("1234")
    assert verify_token("9999", token) is False


def test_token_tampered_signature_fails():
    """서명 위조 토큰 거부."""
    token = issue_token("1234")
    exp, _ = token.split(".", 1)
    assert verify_token("1234", f"{exp}.deadbeef") is False


def test_token_expired_fails():
    """만료된 토큰 거부."""
    past = str(int(time.time()) - 10)
    sig = hmac.new(b"1234", past.encode(), hashlib.sha256).hexdigest()
    assert verify_token("1234", f"{past}.{sig}") is False


def test_token_empty_and_malformed_fails():
    """빈/형식 오류 토큰 거부 (예외 없이 False)."""
    assert verify_token("1234", "") is False
    assert verify_token("1234", "no-dot") is False
    assert verify_token("1234", "notanumber.abc") is False


def test_token_ttl_future():
    """발급 토큰 만료시각은 미래 (TTL 적용)."""
    token = issue_token("1234")
    exp = int(token.split(".", 1)[0])
    now = int(time.time())
    assert now < exp <= now + TOKEN_TTL_SECONDS + 1
