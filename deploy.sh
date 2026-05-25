#!/bin/bash
# RDE 배포 스크립트
# 사용법: ./deploy.sh [브랜치명]
# 예시:   ./deploy.sh main
#         ./deploy.sh claude/review-design-docs-dzIPq

set -e  # 오류 발생 시 즉시 중단

BRANCH=${1:-main}
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$PROJECT_DIR/replenish-system/frontend"
BACKEND_SERVICE="rde-backend.service"
FRONTEND_PM2="rde-frontend"

echo "=============================="
echo " RDE 배포 시작"
echo " 브랜치: $BRANCH"
echo " 경로:   $PROJECT_DIR"
echo "=============================="

# 1. 코드 업데이트
echo ""
echo "[1/4] 최신 코드 pull..."
git -C "$PROJECT_DIR" fetch origin
git -C "$PROJECT_DIR" checkout "$BRANCH"
git -C "$PROJECT_DIR" pull origin "$BRANCH"

# 2. 백엔드 재시작
echo ""
echo "[2/4] 백엔드 재시작..."
sudo systemctl restart "$BACKEND_SERVICE"
sleep 2
if sudo systemctl is-active --quiet "$BACKEND_SERVICE"; then
    echo "  ✓ 백엔드 정상 실행 중"
else
    echo "  ✗ 백엔드 시작 실패! 로그 확인:"
    sudo journalctl -u "$BACKEND_SERVICE" -n 20 --no-pager
    exit 1
fi

# 3. 프론트엔드 빌드
echo ""
echo "[3/4] 프론트엔드 빌드 중... (1~2분 소요)"
cd "$FRONTEND_DIR"
npm run build

# 4. 프론트엔드 재시작
echo ""
echo "[4/4] 프론트엔드 재시작..."
pm2 restart "$FRONTEND_PM2"
sleep 2
if pm2 show "$FRONTEND_PM2" | grep -q "online"; then
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
