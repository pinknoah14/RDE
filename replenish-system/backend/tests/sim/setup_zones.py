"""시뮬레이션 전용 존 설정 초기화"""
import os
os.environ["RDE_DB_PATH"] = "data/sim_test.db"

from fastapi.testclient import TestClient
from app.main import app
from app.core.database import init_db

init_db()
client = TestClient(app)

ZONES = [
    {"zone_prefix": "RA", "zone_name": "R존 메인",  "slack_channel": "R존",
     "access_type": "FORKLIFT", "list_section": "MAIN", "floor": 0, "is_scattered": False},
    {"zone_prefix": "RB", "zone_name": "R존 B구역", "slack_channel": "R존",
     "access_type": "FORKLIFT", "list_section": "MAIN", "floor": 0, "is_scattered": False},
    {"zone_prefix": "SF", "zone_name": "S존 메자닌", "slack_channel": "S존",
     "access_type": "WALKING",  "list_section": "SUB",  "floor": 1, "is_scattered": False},
    {"zone_prefix": "SM", "zone_name": "SM존",       "slack_channel": "S존",
     "access_type": "WALKING",  "list_section": "SUB",  "floor": 1, "is_scattered": True},
    {"zone_prefix": "PW", "zone_name": "PW존",       "slack_channel": "R존",
     "access_type": "FORKLIFT", "list_section": "MAIN", "floor": 0, "is_scattered": True},
    {"zone_prefix": "NC", "zone_name": "NC존",       "slack_channel": "R존",
     "access_type": "FORKLIFT", "list_section": "MAIN", "floor": 0, "is_scattered": False},
]
WORKERS = [
    {"worker_name": "작업자A", "worker_type": "FORKLIFT", "zone_access": "[\"RA\",\"RB\"]", "max_tasks": 6},
    {"worker_name": "작업자B", "worker_type": "FORKLIFT", "zone_access": "[\"PW\",\"NC\"]", "max_tasks": 5},
    {"worker_name": "작업자C", "worker_type": "WALKING",  "zone_access": "[\"SF\",\"SM\"]", "max_tasks": 8},
]

for z in ZONES:
    r = client.post("/api/v1/zone-config", json=z)
    status = "✅" if r.status_code in [200, 201] else f"❌({r.status_code}: {r.text[:80]})"
    print(f"{status} 존 등록: {z['zone_prefix']}")

for w in WORKERS:
    r = client.post("/api/v1/workers", json=w)
    status = "✅" if r.status_code in [200, 201] else f"❌({r.status_code}: {r.text[:80]})"
    print(f"{status} 작업자 등록: {w['worker_name']}")

print("초기 설정 완료")
