"""sessions-service — backed by Redis."""
import asyncio
import logging
import os
import random

import redis
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from common.observability import DB_UP, setup_metrics, timed

log = logging.getLogger("sessions")
URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

app = FastAPI(title="sessions-service")
setup_metrics(app)
_r = redis.Redis.from_url(URL, socket_connect_timeout=3, socket_timeout=3)


def _set(session_id: str):
    with timed("redis", "set"):
        _r.setex(f"session:{session_id}", 300, "active")


def _get(session_id: str):
    with timed("redis", "get"):
        return _r.get(f"session:{session_id}")


@app.put("/sessions/{session_id}")
async def put_session(session_id: str):
    await run_in_threadpool(_set, session_id)
    return {"session_id": session_id, "ttl": 300}


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    val = await run_in_threadpool(_get, session_id)
    return {"session_id": session_id, "active": val is not None}


@app.get("/health")
async def health():
    try:
        await run_in_threadpool(_r.ping)
        DB_UP.labels("redis").set(1)
        return {"status": "ok"}
    except Exception:  # noqa: BLE001
        DB_UP.labels("redis").set(0)
        return {"status": "degraded"}


async def _workload():
    while True:
        try:
            sid = f"s-{random.randint(1, 500)}"
            await run_in_threadpool(_set, sid)
            await run_in_threadpool(_get, sid)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(random.uniform(0.5, 2.0))


@app.on_event("startup")
async def startup():
    try:
        await run_in_threadpool(_r.ping)
        DB_UP.labels("redis").set(1)
        log.info("redis ready")
    except Exception:  # noqa: BLE001
        DB_UP.labels("redis").set(0)
    asyncio.create_task(_workload())
