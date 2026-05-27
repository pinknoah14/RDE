import asyncio
import hashlib
import hmac as _hmac
import json
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

router = APIRouter()

_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
_DEPLOY_SCRIPT = os.path.expanduser("~/RDE/deploy.sh")


def _verify(payload: bytes, sig_header: str) -> bool:
    if not _SECRET:
        return True  # 시크릿 미설정 시 검증 생략 (개발용)
    mac = _hmac.new(_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return _hmac.compare_digest(f"sha256={mac}", sig_header)


async def _run_deploy(branch: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        _DEPLOY_SCRIPT, branch,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


@router.post("/deploy")
async def github_webhook_deploy(
    request: Request,
    background_tasks: BackgroundTasks,
):
    body = await request.body()
    event = request.headers.get("X-GitHub-Event", "")

    # GitHub 연결 테스트 ping
    if event == "ping":
        return {"status": "pong"}

    sig = request.headers.get("X-Hub-Signature-256", "")
    if not _verify(body, sig):
        raise HTTPException(status_code=401, detail="서명 불일치")

    try:
        data = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="JSON 파싱 실패")

    ref = data.get("ref", "")
    branch = ref.removeprefix("refs/heads/") if ref.startswith("refs/heads/") else ""
    if not branch:
        return {"status": "skipped", "reason": "ref 없음"}

    background_tasks.add_task(_run_deploy, branch)
    return {"status": "queued", "branch": branch}
