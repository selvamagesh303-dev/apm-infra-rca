"""profiles-service — backed by MongoDB."""
import asyncio
import logging
import os
import random

from fastapi import FastAPI
from pymongo import MongoClient
from starlette.concurrency import run_in_threadpool

from common.observability import DB_UP, setup_metrics, timed

log = logging.getLogger("profiles")
URI = os.getenv("MONGO_URI", "mongodb://mongo:27017")

app = FastAPI(title="profiles-service")
setup_metrics(app)
_client = MongoClient(URI, serverSelectionTimeoutMS=3000)
_col = _client["app"]["profiles"]


def _upsert(user_id: str):
    with timed("mongodb", "update"):
        _col.update_one({"_id": user_id}, {"$inc": {"visits": 1}}, upsert=True)


def _get(user_id: str):
    with timed("mongodb", "find"):
        return _col.find_one({"_id": user_id})


@app.get("/profiles/{user_id}")
async def get_profile(user_id: str):
    await run_in_threadpool(_upsert, user_id)
    doc = await run_in_threadpool(_get, user_id)
    return {"user_id": user_id, "visits": (doc or {}).get("visits", 0)}


@app.get("/health")
async def health():
    try:
        await run_in_threadpool(_client.admin.command, "ping")
        DB_UP.labels("mongodb").set(1)
        return {"status": "ok"}
    except Exception:  # noqa: BLE001
        DB_UP.labels("mongodb").set(0)
        return {"status": "degraded"}


async def _workload():
    while True:
        try:
            await run_in_threadpool(_upsert, f"user-{random.randint(1, 200)}")
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(random.uniform(1.0, 3.0))


@app.on_event("startup")
async def startup():
    try:
        await run_in_threadpool(_client.admin.command, "ping")
        DB_UP.labels("mongodb").set(1)
        log.info("mongodb ready")
    except Exception:  # noqa: BLE001
        DB_UP.labels("mongodb").set(0)
    asyncio.create_task(_workload())
