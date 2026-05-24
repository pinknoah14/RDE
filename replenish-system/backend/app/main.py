from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import init_db
from app.core.logging_config import setup_logging, get_logger
from app.api import (
    upload,
    waves,
    tasks,
    workers,
    zone_config,
    system_config,
    slack,
    dashboard,
    admin,
    audit,
    events,
)


logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("RDE 시스템 시작", version=app.version)
    init_db()
    logger.info("DB 초기화 완료")
    yield
    logger.info("RDE 시스템 종료")


app = FastAPI(title="보충 운영 보조 시스템", version="2.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router,        prefix="/api/v1/upload",        tags=["upload"])
app.include_router(waves.router,         prefix="/api/v1/waves",         tags=["waves"])
app.include_router(tasks.router,         prefix="/api/v1/tasks",         tags=["tasks"])
app.include_router(workers.router,       prefix="/api/v1/workers",       tags=["workers"])
app.include_router(zone_config.router,          prefix="/api/v1/zone-config",          tags=["zone-config"])
app.include_router(zone_config.floor_ap_router, prefix="/api/v1/floor-access-points",  tags=["floor-access-points"])
app.include_router(zone_config.picking_router,  prefix="/api/v1/picking-zones",        tags=["picking-zones"])
app.include_router(events.router,        prefix="/api/v1/events",        tags=["events"])
app.include_router(system_config.router, prefix="/api/v1/system-config", tags=["system-config"])
app.include_router(slack.router,         prefix="/api/v1/queue",         tags=["queue"])
app.include_router(dashboard.router,     prefix="/api/v1/dashboard",     tags=["dashboard"])
app.include_router(admin.router,         prefix="/api/v1/admin",         tags=["admin"])
app.include_router(audit.router,         prefix="/api/v1/audit-log",     tags=["audit"])
