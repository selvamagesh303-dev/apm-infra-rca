"""sessions-service — full CRUD over Redis (each session stored as a hash)."""
import asyncio
import logging
import os
import random

import redis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from common.observability import DB_UP, setup_metrics, timed

log = logging.getLogger("sessions")
URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
TTL = 600

app = FastAPI(title="sessions-service")
setup_metrics(app)
_r = redis.Redis.from_url(URL, socket_connect_timeout=3, socket_timeout=3, decode_responses=True)


class SessionIn(BaseModel):
    session_id: str
    user: str
    data: str = ""


class SessionUpdate(BaseModel):
    user: str | None = None
    data: str | None = None


def _key(sid: str) -> str:
    return f"session:{sid}"


def db_create(sid: str, user: str, data: str) -> dict:
    with timed("redis", "set"):
        _r.hset(_key(sid), mapping={"user": user, "data": data})
        _r.expire(_key(sid), TTL)
        return {"session_id": sid, "user": user, "data": data}


def db_list(limit: int) -> list[dict]:
    with timed("redis", "scan"):
        out = []
        for k in _r.scan_iter(match="session:*", count=100):
            out.append({"session_id": k.split(":", 1)[1], **_r.hgetall(k)})
            if len(out) >= limit:
                break
        return out


def db_get(sid: str) -> dict | None:
    with timed("redis", "get"):
        h = _r.hgetall(_key(sid))
        return {"session_id": sid, **h} if h else None


def db_update(sid: str, fields: dict) -> dict | None:
    with timed("redis", "update"):
        if not _r.exists(_key(sid)):
            return None
        clean = {k: v for k, v in fields.items() if v is not None}
        if clean:
            _r.hset(_key(sid), mapping=clean)
        _r.expire(_key(sid), TTL)
        return {"session_id": sid, **_r.hgetall(_key(sid))}


def db_delete(sid: str) -> bool:
    with timed("redis", "delete"):
        return _r.delete(_key(sid)) > 0


@app.post("/sessions", status_code=201)
async def create_session(body: SessionIn):
    return await run_in_threadpool(db_create, body.session_id, body.user, body.data)


@app.get("/sessions")
async def list_sessions(limit: int = 50):
    return await run_in_threadpool(db_list, limit)


@app.get("/sessions/{sid}")
async def get_session(sid: str):
    row = await run_in_threadpool(db_get, sid)
    if not row:
        raise HTTPException(404, "session not found")
    return row


@app.put("/sessions/{sid}")
async def update_session(sid: str, body: SessionUpdate):
    row = await run_in_threadpool(db_update, sid, body.model_dump())
    if not row:
        raise HTTPException(404, "session not found")
    return row


@app.delete("/sessions/{sid}")
async def delete_session(sid: str):
    if not await run_in_threadpool(db_delete, sid):
        raise HTTPException(404, "session not found")
    return {"deleted": sid}


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
            await run_in_threadpool(db_create, sid, f"user-{random.randint(1, 200)}", "active")
            await run_in_threadpool(db_get, sid)
            await run_in_threadpool(db_list, 20)
            if random.random() < 0.5:
                await run_in_threadpool(db_update, sid, {"data": "refreshed"})
            if random.random() < 0.3:
                await run_in_threadpool(db_delete, sid)
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
