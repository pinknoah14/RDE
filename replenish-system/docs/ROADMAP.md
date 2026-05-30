# 완성도 향상 로드맵 — 91점 → 100점

> 작성일: 2026-05-30  
> 현재 점수: **91 / 100 (A+)**  
> 목표 점수: **100 / 100**

---

## 현황 요약

| 항목 | 현재 | 목표 | 잔여 작업 |
|------|:----:|:----:|-----------|
| 기능 완성도 | 92 | 100 | GAP-01(외부 의존), GAP-04b 완료 이중검증 |
| 테스트 | 86 | 100 | print_service·webhook·upload·waves 백엔드 보강, 프론트 Vitest 도입, E2E, 부하 테스트 |
| 코드 품질 | 88 | 100 | run_algorithm 분해, waves.py 서비스 레이어 분리, mypy strict |
| 운영 준비도 | 93 | 100 | 모니터링/알림, 백업·복구, 배포 자동화, 런북 |
| 아키텍처 | 83 | 100 | 비동기 잡 큐, DB 확장 경로, API 버저닝 전략 |
| 보안 | 85 | 100 | 토큰 만료, Slack 429 처리, Rate limiting, 보안 리뷰 |
| 문서화 | 92 | 100 | 운영 런북, OpenAPI 게시, 온보딩 가이드 |

> ⚠️ **GAP-01**(WMS 미할당수량 컬럼)은 WMS 측 CSV 제공 없이는 해결 불가.  
> 코드만으로 도달 가능한 현실적 최대치: **97~98점**.

---

## Phase 1 — 테스트 보강 (목표 기간: 1~2일)

**효과**: 테스트 86 → 92, 기능 완성도 +1  
**우선순위가 높은 이유**: 가장 큰 점수 상승, GAP-05 fallback 신뢰성 확보

### 1-A. 백엔드 커버리지 집중 보강

| 파일 | 현재 | 목표 | 핵심 미검증 경로 |
|------|:----:|:----:|-----------------|
| `print_service.py` | 17% | 80%+ | `generate_print_html()` HTML 구조, 존별 태스크 분류, 빈 웨이브 처리 |
| `webhook.py` | 33% | 80%+ | Slack 서명 검증 성공/실패, deploy 이벤트 핸들링, 시크릿 미설정 분기 |
| `upload.py` | 46% | 75%+ | CSV 파싱 에러(줄 198-319), 부분 실패 롤백, bin_master 업로드 |
| `waves.py` | 62% | 80%+ | `distribute_wave_tasks` JUNIOR 라우팅 검증, `urgent_from_dashboard` 통합 |
| `slack.py` | 65% | 85%+ | `send_wave_messages` 실패 시 큐 상태 전환, retry-failed 경로 |

**테스트 파일 계획**:
```
tests/test_print_service.py   — HTML 생성, 존 분류, 빈 웨이브 케이스
tests/test_webhook.py         — HMAC 서명, deploy 핸들러, 비정상 페이로드
tests/test_upload_extended.py — CSV 에러 경로, 롤백, bin_master
tests/test_waves_extended.py  — distribute JUNIOR, urgent_from_dashboard
```

### 1-B. GAP-04b 완료 이중검증 구현 + 테스트

- `task_status=DONE`이지만 재고 CSV에서 `가용=0`이면 경고를 감지하는 로직
- `upload.py`의 재고 처리 후 DONE 태스크 교차 검증
- 시뮬레이션 Phase 5-C를 PASS로 전환

---

## Phase 2 — 보안 강화 (목표 기간: 반나절)

**효과**: 보안 85 → 93  
**우선순위가 높은 이유**: 운영 환경 노출 시 즉각적 위험

### 2-A. 인증 토큰 만료 (`exp` 클레임)

**파일**: `backend/app/core/auth.py`

```python
# 현재: 만료 없는 HMAC 토큰
token = hmac.new(SECRET, PIN.encode(), sha256).hexdigest()

# 목표: exp 포함 서명 페이로드
import time, json
payload = json.dumps({"pin_hash": ..., "exp": int(time.time()) + 86400})
token = base64(payload) + "." + hmac.sign(payload)
```

- 토큰 유효기간: 24시간 (시스템 설정으로 변경 가능)
- 만료 시 401 → 프론트 자동 PIN 재입력 유도

### 2-B. Slack Rate Limit 처리 (`Retry-After` 헤더 반영)

**파일**: `backend/app/services/slack_service.py`

```python
# 현재: 고정 지수 백오프만 적용
# 목표: 429 응답의 Retry-After 헤더 우선 적용
except SlackApiError as e:
    if e.response.status_code == 429:
        wait = int(e.response.headers.get("Retry-After", base_delay))
        time.sleep(wait)
```

### 2-C. Rate Limiting (업로드/인증 엔드포인트)

- `slowapi` 라이브러리로 `/api/v1/upload/*`, `/api/v1/admin/verify-pin` 요청 제한
- 분당 10회 초과 시 429 반환

---

## Phase 3 — 코드 품질 리팩터링 (목표 기간: 1일)

**효과**: 코드 품질 88 → 95  
**우선순위**: 유지보수 부채 해소, Phase 1 테스트 작성과 시너지

### 3-A. `run_algorithm` 분해 (wave_builder.py)

현재 49~298줄 단일 함수를 3개 책임으로 분리:

```python
def _load_algorithm_context(center_cd, wave_id, session) -> AlgorithmContext:
    """DB에서 필요한 모든 데이터를 미리 로드 (쿼리 집중)"""

def _score_sku(sku_id, history, ctx) -> tuple[int, str, list[str]]:
    """단일 SKU 점수·위험도·플래그 계산 (순수 함수, 테스트 용이)"""

def _build_candidate(sku_id, score, level, history, ctx) -> ReplenishCandidate | None:
    """후보 객체 생성 또는 기존 후보 업데이트"""
```

- `run_algorithm`은 이 세 함수의 조율자로만 남음 (~30줄)
- `_score_sku`는 외부 의존성 없는 순수 함수 → 단위 테스트 추가 용이

### 3-B. `waves.py` 서비스 레이어 분리

- `create_wave`, `confirm_wave`, `distribute_wave_tasks` 핵심 로직을 `wave_service.py`로 추출
- 라우터는 HTTP 계층만 담당 (요청 파싱 + 응답 직렬화)

### 3-C. 타입 엄격화

- `mypy --strict` 통과 (`sqlmodel-mypy-plugin` 추가)
- `ruff` 복잡도 규칙 `C901 (max-complexity=15)` 활성화

---

## Phase 4 — 프론트엔드 테스트 도입 (목표 기간: 1일)

**효과**: 테스트 +3 (프론트 0% → 기초 커버리지 확보)

### 4-A. Vitest + Testing Library 설정

```bash
npm install -D vitest @testing-library/react @testing-library/user-event
```

### 4-B. 핵심 단위 테스트 (우선순위 높은 순)

| 파일 | 테스트 대상 | 이유 |
|------|------------|------|
| `lib/api.ts` | `handleUnauthorized` — 토큰 있을 때만 reload | 무한 새로고침 버그 재발 방지 |
| `stores/auth.ts` | PIN 게이트 상태 전환 | 인증 흐름 핵심 |
| `lib/api.ts` | `request()` 에러 파싱 (`err.message \|\| err.detail`) | Phase 2 Fix 1 회귀 방지 |

### 4-C. E2E 테스트 (Playwright)

```
시나리오 1: 로그인 → 재고 CSV 업로드 → 웨이브 생성 → 확정 → 인쇄
시나리오 2: CSV 오류 → 에러 메시지 노출 확인
시나리오 3: Slack 전송 실패 → retry-failed → 성공
```

---

## Phase 5 — 운영 견고성 (목표 기간: 1일)

**효과**: 운영 준비도 93 → 98

### 5-A. 메트릭 엔드포인트

```python
# GET /api/v1/metrics (Prometheus 형식)
wave_creation_duration_seconds
algorithm_candidates_total
slack_send_failures_total
upload_rows_processed_total
```

### 5-B. 자동 DB 백업

```python
# cron 또는 APScheduler로 매일 02:00 실행
# backend/scripts/backup_db.py
import shutil, datetime
shutil.copy("data/replenish.db", f"data/backups/replenish_{datetime.date.today()}.db")
# 30일 초과분 자동 삭제
```

### 5-C. 휴게/마감 자동 스케줄

```python
# APScheduler로 현재 수동인 pre_break_sweep를 시간 기반 자동 실행
# 15:50, 17:50, 20:40, 23:10 → pre_break_sweep(actor="scheduler")
# 16:00, 20:30, 23:00 → cutoff_boost(center_cd=..., min_risk_level="HIGH")
```

---

## Phase 6 — 문서 완비 (목표 기간: 반나절)

**효과**: 문서화 92 → 100, 운영 준비도 +2

### 6-A. 운영 런북 (`docs/RUNBOOK.md`)

```
장애 시나리오별 대응 절차:
- 서버 다운 → print 뷰 URL 저장 → 인쇄본 사용 → 서버 복구 후 DB 재적재
- Slack 봇 장애 → retry-failed 수동 실행 → 채널별 메시지 재전송
- CSV 업로드 실패 → 에러 메시지 해석 가이드 → 올바른 컬럼 형식
- DB 손상 → 백업 복구 절차
```

### 6-B. 개발자 온보딩 가이드 (`docs/CONTRIBUTING.md`)

```
로컬 개발 환경 설정
테스트 실행 방법
알고리즘 로직 개요
PR 규칙 (Ruff 0, tsc 0, 테스트 통과 필수)
```

---

## 외부 의존 항목 (코드로 해결 불가)

| 항목 | 내용 | 필요 조건 |
|------|------|-----------|
| **GAP-01** | WMS 미할당수량 컬럼 없음 | WMS 팀에서 `미할당수량` 컬럼 포함 CSV 제공 |

GAP-01 해소 시 `algorithm.py`의 `stockout_flag` 로직을 미할당수량 기반으로 전환, `CRITICAL` 감지율이 현재보다 정확해짐.

---

## 완료 체크리스트

```
Phase 1 — 테스트 보강
  [x] test_print_service.py (print_service 17% → 100%, 12 tests)
  [x] test_webhook.py (webhook 33% → 100%, 11 tests)
  [x] test_upload_extended.py (upload 46% → 77%, 11 tests)
  [x] test_waves_extended.py (waves 62% → 70%, 11 tests) — distribute(GAP-07) 완전 커버
  [ ] GAP-04b 완료 이중검증 구현

Phase 2 — 보안 강화
  [ ] 인증 토큰 exp 클레임 추가
  [ ] Slack 429 Retry-After 처리
  [ ] slowapi Rate Limiting (upload, verify-pin)

Phase 3 — 코드 품질
  [ ] run_algorithm 3개 함수로 분해
  [ ] waves.py 서비스 레이어 분리
  [ ] mypy strict 통과

Phase 4 — 프론트엔드 테스트
  [ ] Vitest 설정
  [ ] lib/api.ts 단위 테스트 (handleUnauthorized, 에러 파싱)
  [ ] Playwright E2E 3개 시나리오

Phase 5 — 운영 견고성
  [ ] /api/v1/metrics 엔드포인트
  [ ] 자동 DB 백업 스크립트
  [ ] APScheduler 휴게/마감 자동 스케줄

Phase 6 — 문서
  [ ] docs/RUNBOOK.md
  [ ] docs/CONTRIBUTING.md

외부 의존
  [ ] GAP-01: WMS 미할당수량 컬럼 협의 (외부)
```
