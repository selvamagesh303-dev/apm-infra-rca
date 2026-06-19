"""profiles-service — full CRUD over MongoDB."""
import asyncio
import logging
import os
import random

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient, ReturnDocument
from starlette.concurrency import run_in_threadpool

from common.observability import DB_UP, setup_metrics, timed

log = logging.getLogger("profiles")
URI = os.getenv("MONGO_URI", "mongodb://mongo:27017")

app = FastAPI(title="profiles-service")
setup_metrics(app)
_client = MongoClient(URI, serverSelectionTimeoutMS=3000)
_col = _client["app"]["profiles"]


class ProfileIn(BaseModel):
    user_id: str
    name: str = ""


class ProfileUpdate(BaseModel):
    name: str


def _doc(d: dict | None) -> dict | None:
    return d  # _id is already the user_id string


def db_create(user_id: str, name: str) -> dict:
    with timed("mongodb", "insert"):
        _col.replace_one({"_id": user_id}, {"_id": user_id, "name": name, "visits": 0}, upsert=True)
        return {"user_id": user_id, "name": name, "visits": 0}


def db_list(limit: int) -> list[dict]:
    with timed("mongodb", "find"):
        return [{"user_id": d["_id"], "name": d.get("name", ""), "visits": d.get("visits", 0)}
                for d in _col.find().sort("_id", 1).limit(limit)]


def db_get(user_id: str) -> dict | None:
    with timed("mongodb", "find"):
        d = _col.find_one_and_update({"_id": user_id}, {"$inc": {"visits": 1}}, return_document=ReturnDocument.AFTER)
        return {"user_id": d["_id"], "name": d.get("name", ""), "visits": d.get("visits", 0)} if d else None


def db_update(user_id: str, name: str) -> dict | None:
    with timed("mongodb", "update"):
        d = _col.find_one_and_update({"_id": user_id}, {"$set": {"name": name}}, return_document=ReturnDocument.AFTER)
        return {"user_id": d["_id"], "name": d.get("name", ""), "visits": d.get("visits", 0)} if d else None


def db_delete(user_id: str) -> bool:
    with timed("mongodb", "delete"):
        return _col.delete_one({"_id": user_id}).deleted_count > 0


@app.post("/profiles", status_code=201)
async def create_profile(body: ProfileIn):
    return await run_in_threadpool(db_create, body.user_id, body.name)


@app.get("/profiles")
async def list_profiles(limit: int = 50):
    return await run_in_threadpool(db_list, limit)


@app.get("/profiles/{user_id}")
async def get_profile(user_id: str):
    row = await run_in_threadpool(db_get, user_id)
    if not row:
        raise HTTPException(404, "profile not found")
    return row


@app.put("/profiles/{user_id}")
async def update_profile(user_id: str, body: ProfileUpdate):
    row = await run_in_threadpool(db_update, user_id, body.name)
    if not row:
        raise HTTPException(404, "profile not found")
    return row


@app.delete("/profiles/{user_id}")
async def delete_profile(user_id: str):
    if not await run_in_threadpool(db_delete, user_id):
        raise HTTPException(404, "profile not found")
    return {"deleted": user_id}


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
            uid = f"user-{random.randint(1, 200)}"
            await run_in_threadpool(db_create, uid, f"name-{random.randint(1, 999)}")
            await run_in_threadpool(db_get, uid)
            await run_in_threadpool(db_list, 20)
            if random.random() < 0.5:
                await run_in_threadpool(db_update, uid, f"name-{random.randint(1, 999)}")
            if random.random() < 0.3:
                await run_in_threadpool(db_delete, uid)
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
