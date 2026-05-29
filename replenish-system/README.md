# RDE — 보충 운영 보조 시스템

보충팀 관리자의 웨이브 생성 · 작업자 배분 · Slack 전송을 지원하는 반자동 보충 운영 시스템.

---

## 시작 전 준비 (최초 1회)

### 필수 설치 프로그램

| 프로그램 | 버전 | 다운로드 |
|---|---|---|
| Python | 3.11 이상 | https://www.python.org/downloads/ |
| Node.js | 20 이상 | https://nodejs.org/ko |
| Git | 최신 | https://git-scm.com/download/win |

> ⚠️ Python 설치 시 **"Add Python to PATH"** 체크박스 반드시 선택

---

## 설치

### 1. 프로젝트 다운로드

```
Git Bash 또는 명령 프롬프트(cmd) 실행 후:

git clone https://github.com/[저장소주소]/replenish-system.git
cd replenish-system
```

### 2. 백엔드 설치

```
cd backend

python -m venv venv
venv\Scripts\activate

pip install -r requirements.txt
```

설치 완료 확인:
```
python -c "import fastapi, polars, sqlmodel; print('OK')"
```

### 3. 프론트엔드 설치

새 명령 프롬프트 창을 열고:

```
cd replenish-system\frontend
npm install
```

---

## 실행

매일 시작할 때 아래 순서대로 실행합니다.

### 터미널 1 — 백엔드

```
cd replenish-system\backend
venv\Scripts\activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

정상 실행 확인:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete.
```

### 터미널 2 — 프론트엔드

```
cd replenish-system\frontend
npm run dev
```

브라우저에서 `http://localhost:3000` 열기

---

## 초기 설정 (최초 1회)

### 1. 환경 변수 설정

`backend\.env` 파일 생성 (`.env.example` 복사 후 값 입력):

```
copy .env.example .env
메모장으로 .env 열어서 값 입력
```

### 2. Slack 연결

`설정 → 시스템 설정 → Slack 탭`

```
Slack 봇 토큰:  xoxb-... 입력
워크스페이스:   회사명 입력
```

### 3. 존 등록

`설정 → 존 설정 → [+ 존 추가]`

```
센터 내 모든 존 등록
존코드, 존이름, 채널, 접근유형(지게차/도보), 구분, 층
산재 존(PW 등): 산재 토글 ON → 통로별 위치 설정
```

### 4. 작업자 등록

`설정 → 작업자 관리 → [+ 추가]`

```
이름, 유형(지게차/도보), 담당 존, 숙련도, Slack ID
```

### 5. 피킹지번 마스터 등록

`설정 → 피킹지번 관리 → [+ 추가]` 또는 CSV 일괄 등록

---

## 일별 운영 절차

### 출근 시

1. 터미널 2개 열어서 백엔드 · 프론트엔드 실행
2. `설정 → 작업자 관리` → 오늘 출근 작업자 활성화
3. 작업자 당일 작업 유형 확인 (지게차 / 도보)

### 주문 인입마다 (30분 / 15분 주기)

```
1. WMS에서 재고현황 CSV + 출고현황 CSV 다운로드
2. 업로드 화면에서 두 파일 업로드
3. 웨이브 생성 → 검수 → 확정 → Slack 전송
```

### 선보충 (23:20)

```
1. 웨이브 생성 → 유형: 선보충 선택
2. 컷오프 자동 산출 확인 (작업자 수 × UPH × 100분)
3. 확정 → Slack 전송
```

### 퇴근 시

```
설정 → 데이터 관리 → DB 내보내기
→ 파일을 다음 관리자에게 전달

작업자 관리 → 일괄 초기화
```

### 출근 인계 시

```
설정 → 데이터 관리 → DB 가져오기
→ 전달받은 파일 업로드
```

---

## 시스템 설정 주요 항목

`설정 → 시스템 설정 → 알고리즘 탭`

| 항목 | 기본값 | 조정 기준 |
|---|---|---|
| 미할당 가중치 | +15 | CRITICAL 추천 늦으면 +20~25 |
| 층 이동 패널티 | 60m | 계단 실측 후 조정 |
| 인접 판정 근접 | 10m | 센터 크기 기준 조정 |
| 선보충 UPH | 12 | 작업자 실측 후 조정 |
| 선보충 가용 시간 | 100분 | 23:20~01:00 기준 |

---

## 자주 묻는 문제

**Q. 브라우저에서 화면이 안 열려요**
```
백엔드가 먼저 실행 중인지 확인
터미널 1에서 에러 메시지 확인
http://localhost:8000/docs 접속해서 API 동작 확인
```

**Q. CSV 업로드가 실패해요**
```
파일 인코딩 확인 (UTF-8 또는 CP949)
필수 컬럼 누락 여부 확인
대시보드 → 미등록 존 경고 확인
```

**Q. Slack 메시지가 안 가요**
```
설정 → 시스템 설정 → Slack 탭에서 봇 토큰 확인
Slack 앱이 해당 채널에 초대됐는지 확인 (/invite @봇이름)
```

**Q. 재실행 후 데이터가 없어요**
```
DB 파일 위치 확인: replenish-system\backend\data\replenish.db
파일이 없으면 DB 가져오기로 복구
```

---

## 폴더 구조

```
replenish-system\
├── README.md
├── backend\          백엔드 (FastAPI)
│   ├── app\
│   ├── data\         DB 파일 저장 위치
│   ├── tests\
│   ├── .env          환경 변수 (직접 생성)
│   ├── .env.example  환경 변수 예시
│   └── requirements.txt
└── frontend\         프론트엔드 (Next.js)
    └── src\
```

---

## 버전 정보

| 버전 | 주요 변경 |
|---|---|
| v2.4 | CSV 컬럼명 22개 시스템 설정 런타임 변경, 업로드 성능 개선 (bulk insert·N+1 제거), GitHub Webhook 배포 엔드포인트, WAL checkpoint DB 내보내기 버그 수정 |
| v2.3 | 코드 품질 정제 (Ruff 0 errors, 미사용 코드 제거, console.error → toast 통일, 타입 정확도 개선) |
| v2.2 | README, 로깅, 환경변수 분리 |
| v2.1 | 14:00 미할당 폭발 대응, 배치 그룹 절단 방지 |
| v2.0 | 현장 운영 방식 반영 (배치 태그, 선보충 컷오프, 현장 Slack 형식) |
| v1.9 | 보충지번 목록 표시, 이벤트/피킹지번 관리 |
| v1.7 | 물리 좌표 시스템, 존 배치 설정 |
