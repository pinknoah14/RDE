# 코드 품질 개선 작업 기록

> 작업 일시: 2026-05-25  
> 목적: 기능 변경 없이 코드 수준을 고급 개발자 기준으로 정제

---

## Phase 1 — 구조 정리 (코드 품질 1차)

### Backend

| 파일 | 변경 사항 |
|---|---|
| `slack_service.py` | `build_task_block()` 삭제 (미사용 함수), `build_wave_messages()` 삭제 (v2로 대체됨), 함수 내 inline import → 파일 상단으로 이동, `_get_bot_token(session)` 헬퍼 추출로 토큰 조회 중복 제거 |
| `waves.py` | `HTTPException` import 제거, `InvalidTransitionError` 핸들러 → `RDEException`으로 통일, JSON 파싱 catch `except Exception` → `except json.JSONDecodeError`로 좁힘 + `logger.warning` 추가 |
| `upload.py` | `HTTPException` → `RDEException`, `import io` 상단 이동, `decode_csv_bytes` 재사용 |
| `admin.py` | `HTTPException` → `RDEException` |
| `csv_parser.py` | `Optional[str]` import 제거, `decode_csv_bytes()` 함수 추출 (CP949/UTF-8 폴백 로직 단일화) |
| `wave_builder.py` | `except Exception: bins = []` → `except json.JSONDecodeError` + `logger.warning` |
| `tests/test_v19_fixes.py` | `build_task_block` import 및 관련 테스트 제거 |
| `tests/test_slack_mock.py` | `build_wave_messages` → `build_wave_messages_v2`로 교체 |
| `tests/sim/run_simulation.py` | import 동기화 |

### Frontend

| 파일 | 변경 사항 |
|---|---|
| `types/index.ts` | `WaveCreateResponse` 인터페이스 추가 |
| `lib/api.ts` | `createWave` 반환 타입 → `WaveCreateResponse` 참조, **버그픽스**: `err.detail ??` → `err.message \|\| err.detail \|\|` (`??`는 빈 문자열 `""`에 비반응) |
| `globals.css` | 브랜드 컬러 이미 `--color-primary`로 정의되어 있음 확인 |
| `upload/page.tsx` | 인라인 `style={{ color: "#5F0080" }}` → `className="text-primary"` |
| `waves/page.tsx` | 인라인 스타일 → `text-primary`, `console.error` → `toast({ variant: "destructive" })` |
| `settings/system/page.tsx` | `text-[#5F0080]` → `text-primary`, `console.error` → toast |
| `settings/zones/page.tsx` | `console.error` → toast |
| `dashboard/page.tsx` | `data!.new_skus` 등 non-null assertion `!` 제거 → `data?.xxx ?? 0` 패턴, `hasAlerts` 변수 추출로 알림 없음 조건 단순화 |
| `waves/[wave_id]/page.tsx` | 반복 async 핸들러 → `withAction(fn, msg)` 헬퍼로 통합 |

---

## Phase 2 — 버그 수정 (코드 품질 2차)

### Fix 1 — `api.ts` 에러 파싱 버그 (CRITICAL)

**파일**: `frontend/src/lib/api.ts`  
**문제**: RDEException은 `detail: str = ""` (빈 문자열)을 기본값으로 사용.  
JavaScript `??` 연산자는 `null`/`undefined`에만 반응하며, 빈 문자열 `""`에는 반응하지 않음.  
결과적으로 `err.detail ?? fallback`은 빈 문자열을 그대로 에러 메시지로 노출.

```ts
// Before
throw new Error(err.detail ?? `HTTP ${res.status}`);

// After
throw new Error(err.message || err.detail || `HTTP ${res.status}`);
```

`request()`와 `upload()` 두 함수 모두 적용.

---

### Fix 2 — `CandidatePatch` 모델에 `list_section` 누락 (CRITICAL)

**파일**: `backend/app/api/waves.py`  
**문제**: 프론트엔드에서 `PATCH /candidates/{id}` 시 `{ list_section: "SUB" }`를 전송하지만,  
Pydantic 모델에 해당 필드가 없어 무시(silent drop). 결과적으로 섹션 이동 버튼이 아무 효과 없음.

```python
# Before
class CandidatePatch(BaseModel):
    modified_qty: int | None = None

# After
class CandidatePatch(BaseModel):
    modified_qty: int | None = None
    list_section: Literal["MAIN", "SUB"] | None = None
```

`update_candidate` 핸들러에도 `list_section` 처리 로직 추가.

---

### Fix 3 — `withAction` 성공 토스트가 refresh 실패 시 노출 안 됨

**파일**: `frontend/src/app/waves/[wave_id]/page.tsx`  
**문제**: `await fn(); await refresh(); toast(성공)` 순서에서 `refresh()`가 실패하면 catch로 넘어가  
성공 토스트 대신 에러 토스트가 노출됨.

```ts
// Before
try { await fn(); await refresh(); if (successMsg) toast({ title: successMsg }); }
catch (e) { toast({ variant: "destructive", ... }); }

// After
try {
  await fn();
  if (successMsg) toast({ title: successMsg });
  refresh().catch(() => {});  // fire-and-forget
} catch (e) { toast({ variant: "destructive", ... }); }
```

---

### Fix 4 — `PROXIMITY_ICON` 죽은 코드 제거

**파일**: `backend/app/services/slack_service.py`  
**문제**: `PROXIMITY_ICON = {4: "🟢", 3: "🟠", 2: "🟡", 1: "⚪"}` 상수가  
`build_task_block` 삭제 후 어디서도 참조되지 않음.

```python
# 제거
PROXIMITY_ICON = {4: "🟢", 3: "🟠", 2: "🟡", 1: "⚪"}
```

---

### Fix 5 — 중복 예외 클래스 제거

**파일**: `backend/app/api/admin.py`  
**문제**: `sqlite3.OperationalError`는 `sqlite3.DatabaseError`의 하위 클래스이므로  
`except (sqlite3.DatabaseError, sqlite3.OperationalError)`는 중복.

```python
# Before
except (sqlite3.DatabaseError, sqlite3.OperationalError):

# After
except sqlite3.DatabaseError:
```

---

### Fix 6 — `useRef` 불필요 사용 제거

**파일**: `frontend/src/app/dashboard/page.tsx`  
**문제**: `intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)` 로  
setInterval ID를 ref에 저장하지만, cleanup은 useEffect 내 클로저 변수로 충분.

```ts
// Before
const intervalRef = useRef<...>(null);
useEffect(() => {
  loadDashboard();
  intervalRef.current = setInterval(loadDashboard, POLL_INTERVAL_MS);
  return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
}, [loadDashboard]);

// After
useEffect(() => {
  loadDashboard();
  const id = setInterval(loadDashboard, POLL_INTERVAL_MS);
  return () => clearInterval(id);
}, [loadDashboard]);
```

---

### Fix 7 — config int 로드 반복 패턴 → `_cfg_int` 헬퍼 추출

**파일**: `backend/app/services/wave_builder.py`  
**문제**: try/except KeyError로 int config를 읽는 동일 패턴이 여러 곳 반복.

```python
# Before (각 위치마다 반복)
try:
    uph = int(get_config("prestock_uph", session) or 12)
except KeyError:
    uph = 12

# After
def _cfg_int(key: str, session: Session, default: int) -> int:
    try:
        return int(get_config(key, session) or default)
    except KeyError:
        return default

uph = _cfg_int("prestock_uph", session, 12)
minutes = _cfg_int("prestock_minutes", session, 100)
# apply_batch_tags_to_wave 내 batch_tag_min_group도 동일 적용
```

---

### Fix 8 — 테스트 파일 섹션 번호 정정

**파일**: `backend/tests/test_v19_fixes.py`  
**문제**: `build_task_block` 테스트(섹션 3, 4) 제거 후 이후 섹션 번호가 5, 6, 7로 어긋남.  
5 → 4, 6 → 5, 7 → 6으로 순서 정정.

---

## 검증 결과

- `pytest tests/ -x -q` → 246 passed
- `npx tsc --noEmit` → 0 errors
