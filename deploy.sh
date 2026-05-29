#!/bin/bash
# RDE 배포 스크립트
# 사용법: ./deploy.sh [브랜치명]

set -e

BRANCH=${1:-main}
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/replenish-system/backend"
FRONTEND_DIR="$PROJECT_DIR/replenish-system/frontend"
VENV_DIR="$BACKEND_DIR/.venv"
BACKEND_SERVICE="rde-backend.service"
FRONTEND_PM2="rde-frontend"
SERVICE_FILE="/etc/systemd/system/$BACKEND_SERVICE"
CURRENT_USER="$(whoami)"

echo "=============================="
echo " RDE 배포 시작"
echo " 브랜치: $BRANCH"
echo " 경로:   $PROJECT_DIR"
echo " 사용자: $CURRENT_USER"
echo "=============================="

# ── 1. 코드 업데이트 ──────────────────────────────────────────
echo ""
echo "[1/5] 최신 코드 pull..."
git -C "$PROJECT_DIR" fetch origin
git -C "$PROJECT_DIR" checkout "$BRANCH"
git -C "$PROJECT_DIR" pull origin "$BRANCH"

# ── 2. 백엔드 Python 환경 ────────────────────────────────────
echo ""
echo "[2/5] 백엔드 환경 준비..."

# python3-venv 패키지 사전 보장 (dpkg 없으면 설치 시도)
if ! python3 -m venv --help &>/dev/null 2>&1 || \
   ! dpkg -s python3-venv &>/dev/null 2>&1; then
    echo "  python3-venv 설치 중..."
    sudo apt-get install -y --no-install-recommends python3-venv python3-pip
fi

# pip 바이너리가 없으면 venv 불완전 → 삭제 후 재생성
if [ ! -f "$VENV_DIR/bin/pip" ]; then
    echo "  venv 생성 중..."
    rm -rf "$VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

echo "  패키지 설치 중..."
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q -r "$BACKEND_DIR/requirements.txt"
echo "  ✓ Python 환경 준비 완료"

# ── 3. systemd 서비스 등록 ────────────────────────────────────
echo ""
echo "[3/5] 백엔드 서비스 설정..."

if [ ! -f "$SERVICE_FILE" ]; then
    echo "  systemd 서비스 파일 생성 중..."
    sudo tee "$SERVICE_FILE" > /dev/null <<SVCEOF
[Unit]
Description=RDE Backend (FastAPI/uvicorn)
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$BACKEND_DIR
ExecStart=$VENV_DIR/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5
EnvironmentFile=-$PROJECT_DIR/.env

[Install]
WantedBy=multi-user.target
SVCEOF
    sudo systemctl daemon-reload
    sudo systemctl enable "$BACKEND_SERVICE"
    echo "  ✓ 서비스 등록 완료"
fi

echo "  백엔드 재시작 중..."
sudo fuser -k 8000/tcp 2>/dev/null || true
sleep 1
sudo systemctl restart "$BACKEND_SERVICE"
sleep 2
if sudo systemctl is-active --quiet "$BACKEND_SERVICE"; then
    echo "  ✓ 백엔드 정상 실행 중"
else
    echo "  ✗ 백엔드 시작 실패! 로그 확인:"
    sudo journalctl -u "$BACKEND_SERVICE" -n 30 --no-pager
    exit 1
fi

# ── 4. 프론트엔드 빌드 ───────────────────────────────────────
echo ""
echo "[4/5] 프론트엔드 빌드 중... (1~2분 소요)"

# node_modules 없으면 설치 (package-lock.json 기준 npm ci)
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    echo "  node_modules 설치 중..."
    cd "$FRONTEND_DIR" && npm ci --prefer-offline 2>/dev/null || npm install
fi

# 포트 정리 후 빌드
sudo fuser -k 3000/tcp 2>/dev/null || true
pm2 stop "$FRONTEND_PM2" 2>/dev/null || true
cd "$FRONTEND_DIR" && npm run build

# ── 5. 프론트엔드 재시작 ─────────────────────────────────────
echo ""
echo "[5/5] 프론트엔드 재시작..."
# restart 실패(미등록) → start로 폴백
pm2 restart "$FRONTEND_PM2" 2>/dev/null || \
    pm2 start npm --name "$FRONTEND_PM2" -- start
pm2 save --force

sleep 3
if pm2 show "$FRONTEND_PM2" 2>/dev/null | grep -q "online"; then
    echo "  ✓ 프론트엔드 정상 실행 중"
else
    echo "  ✗ 프론트엔드 시작 실패!"
    pm2 logs "$FRONTEND_PM2" --lines 20 --nostream
    exit 1
fi

echo ""
echo "=============================="
echo " 배포 완료!"
echo "=============================="
