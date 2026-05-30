"""GitHub Webhook 배포 엔드포인트 (webhook.py) 회귀 테스트.

검증 대상:
- _verify: HMAC-SHA256 서명 검증 (시크릿 유무, 일치/불일치)
- /api/webhook/deploy: ping, 서명 검증 실패, JSON 오류,
  ref 없음 skip, 정상 큐잉 (배포 스크립트는 mock)
"""
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import app.models  # noqa: F401
from app.api import webhook
from app.main import app


client = TestClient(app)


def _sign(secret: str, payload: bytes) -> str:
    mac = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


# ────────────────────────────────────────────────────────────────
# _verify 단위 테스트
# ────────────────────────────────────────────────────────────────

def test_verify_skips_when_no_secret(monkeypatch):
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    # 시크릿 미설정 → 항상 통과 (개발용)
    assert webhook._verify(b"anything", "sha256=irrelevant") is True


def test_verify_accepts_valid_signature(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "topsecret")
    payload = b'{"ref":"refs/heads/main"}'
    assert webhook._verify(payload, _sign("topsecret", payload)) is True


def test_verify_rejects_invalid_signature(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "topsecret")
    payload = b'{"ref":"refs/heads/main"}'
    assert webhook._verify(payload, "sha256=deadbeef") is False


def test_verify_rejects_signature_from_wrong_secret(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "topsecret")
    payload = b'{"ref":"refs/heads/main"}'
    assert webhook._verify(payload, _sign("wrongsecret", payload)) is False


# ────────────────────────────────────────────────────────────────
# /api/webhook/deploy 엔드포인트
# ────────────────────────────────────────────────────────────────

def test_ping_returns_pong():
    r = client.post("/api/webhook/deploy", headers={"X-GitHub-Event": "ping"})
    assert r.status_code == 200
    assert r.json() == {"status": "pong"}


def test_invalid_signature_returns_401(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "topsecret")
    r = client.post(
        "/api/webhook/deploy",
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=bad"},
        content=b'{"ref":"refs/heads/main"}',
    )
    assert r.status_code == 401


def test_invalid_json_returns_400(monkeypatch):
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    r = client.post(
        "/api/webhook/deploy",
        headers={"X-GitHub-Event": "push"},
        content=b"not-json{{{",
    )
    assert r.status_code == 400


def test_missing_ref_skipped(monkeypatch):
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    r = client.post(
        "/api/webhook/deploy",
        headers={"X-GitHub-Event": "push"},
        content=json.dumps({"zen": "no ref here"}).encode(),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"


def test_valid_push_queues_deploy(monkeypatch):
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    with patch.object(webhook, "_run_deploy", new=AsyncMock()) as mock_deploy:
        r = client.post(
            "/api/webhook/deploy",
            headers={"X-GitHub-Event": "push"},
            content=json.dumps({"ref": "refs/heads/main"}).encode(),
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["branch"] == "main"
    # BackgroundTasks가 _run_deploy를 호출했는지 확인
    mock_deploy.assert_awaited_once_with("main")


def test_valid_push_with_signature(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "topsecret")
    payload = json.dumps({"ref": "refs/heads/feature-x"}).encode()
    with patch.object(webhook, "_run_deploy", new=AsyncMock()) as mock_deploy:
        r = client.post(
            "/api/webhook/deploy",
            headers={
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": _sign("topsecret", payload),
            },
            content=payload,
        )
    assert r.status_code == 200
    assert r.json()["branch"] == "feature-x"
    mock_deploy.assert_awaited_once_with("feature-x")


@pytest.mark.asyncio
async def test_run_deploy_invokes_subprocess():
    """_run_deploy가 배포 스크립트를 올바른 인자로 실행하는지 검증."""
    fake_proc = AsyncMock()
    fake_proc.wait = AsyncMock(return_value=0)
    with patch(
        "app.api.webhook.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_proc),
    ) as mock_exec:
        await webhook._run_deploy("main")
    mock_exec.assert_awaited_once()
    # 첫 두 인자가 (스크립트경로, 브랜치)
    args = mock_exec.await_args.args
    assert args[0] == webhook._DEPLOY_SCRIPT
    assert args[1] == "main"
    fake_proc.wait.assert_awaited_once()
