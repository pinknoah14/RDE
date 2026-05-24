"""
원 데이 시뮬레이션 초기 설정

기존 replenish.db를 삭제하고 6개 존 + 4명 작업자로 초기화.
"""
import os
import sys
from pathlib import Path

# 기존 DB 초기화 (data/replenish.db — database.py의 DB_PATH와 동일)
db_path = Path("data/replenish.db")
if db_path.exists():
    db_path.unlink()
    print(f"기존 DB 삭제: {db_path}")

sys.path.insert(0, ".")

# models 먼저 import (app 초기화 전)
import app.models  # noqa: F401, E402
from app.main import app  # noqa: E402
from app.core.database import init_db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

init_db()
client = TestClient(app)

ZONES = [
    {"zone_prefix": "RA", "zone_name": "R존메인",   "slack_channel": "R존",
     "access_type": "FORKLIFT", "list_section": "MAIN", "floor": 0},
    {"zone_prefix": "RB", "zone_name": "R존B구역",  "slack_channel": "R존",
     "access_type": "FORKLIFT", "list_section": "MAIN", "floor": 0},
    {"zone_prefix": "NC", "zone_name": "NC존",      "slack_channel": "R존",
     "access_type": "FORKLIFT", "list_section": "MAIN", "floor": 0},
    {"zone_prefix": "PW", "zone_name": "PW존",      "slack_channel": "R존",
     "access_type": "FORKLIFT", "list_section": "MAIN",
     "floor": 0, "is_scattered": True},
    {"zone_prefix": "SF", "zone_name": "S존메자닌", "slack_channel": "P존",
     "access_type": "WALKING",  "list_section": "SUB",  "floor": 1},
    {"zone_prefix": "SM", "zone_name": "SM존",      "slack_channel": "P존",
     "access_type": "WALKING",  "list_section": "SUB",
     "floor": 1, "is_scattered": True},
]

WORKERS = [
    {"worker_name": "지게차A", "worker_type": "FORKLIFT",
     "skill_level": "EXPERT",  "work_type": "FORKLIFT",
     "zone_access": '["RA","RB","NC"]', "max_tasks": 6, "is_active": True},
    {"worker_name": "지게차B", "worker_type": "FORKLIFT",
     "skill_level": "NORMAL",  "work_type": "FORKLIFT",
     "zone_access": '["PW","NC"]',      "max_tasks": 5, "is_active": True},
    {"worker_name": "도보A",   "worker_type": "WALKING",
     "skill_level": "NORMAL",  "work_type": "WALKING",
     "zone_access": '["RA","RB"]',      "max_tasks": 8, "is_active": True},
    {"worker_name": "도보B",   "worker_type": "WALKING",
     "skill_level": "JUNIOR",  "work_type": "WALKING",
     "zone_access": '["SF","SM"]',      "max_tasks": 6, "is_active": True},
]

print("=== 초기 설정 ===")
for z in ZONES:
    r = client.post("/api/v1/zone-config", json=z)
    print(f"  {'✅' if r.status_code in [200, 201] else '❌'} 존 [{z['zone_prefix']}]"
          f" {'→ ' + str(r.json()) if r.status_code not in [200,201] else ''}")

for w in WORKERS:
    r = client.post("/api/v1/workers", json=w)
    print(f"  {'✅' if r.status_code in [200, 201] else '❌'} 작업자 [{w['worker_name']}]"
          f" ({w['skill_level']}, {w['work_type']})"
          f" {'→ ' + str(r.json()) if r.status_code not in [200,201] else ''}")

print("초기 설정 완료")
