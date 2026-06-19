"""search-service — full CRUD over Elasticsearch (documents in the `catalog` index)."""
import asyncio
import logging
import os
import random
import uuid

from elasticsearch import Elasticsearch, NotFoundError
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from common.observability import DB_UP, setup_metrics, timed

log = logging.getLogger("search")
URL = os.getenv("ELASTICSEARCH_URL", "http://elasticsearch:9200")
INDEX = "catalog"

app = FastAPI(title="search-service")
setup_metrics(app)
_es = Elasticsearch(URL, request_timeout=3, retry_on_timeout=False)


class DocIn(BaseModel):
    name: str
    tags: list[str] = []


def db_create(doc_id: str, body: dict) -> dict:
    with timed("elasticsearch", "index"):
        _es.index(index=INDEX, id=doc_id, document=body, refresh=True)
        return {"id": doc_id, **body}


def db_search(term: str, limit: int) -> list[dict]:
    with timed("elasticsearch", "search"):
        query = {"match_all": {}} if not term else {"match": {"name": term}}
        res = _es.search(index=INDEX, query=query, size=limit)
        return [{"id": h["_id"], **h["_source"]} for h in res["hits"]["hits"]]


def db_get(doc_id: str) -> dict | None:
    with timed("elasticsearch", "get"):
        try:
            res = _es.get(index=INDEX, id=doc_id)
            return {"id": res["_id"], **res["_source"]}
        except NotFoundError:
            return None


def db_update(doc_id: str, body: dict) -> dict | None:
    with timed("elasticsearch", "update"):
        try:
            _es.update(index=INDEX, id=doc_id, doc=body, refresh=True)
            res = _es.get(index=INDEX, id=doc_id)
            return {"id": res["_id"], **res["_source"]}
        except NotFoundError:
            return None


def db_delete(doc_id: str) -> bool:
    with timed("elasticsearch", "delete"):
        try:
            _es.delete(index=INDEX, id=doc_id, refresh=True)
            return True
        except NotFoundError:
            return False


@app.post("/documents", status_code=201)
async def create_document(body: DocIn):
    return await run_in_threadpool(db_create, uuid.uuid4().hex[:12], body.model_dump())


@app.get("/documents")
async def list_documents(q: str = "", limit: int = 20):
    return await run_in_threadpool(db_search, q, limit)


@app.get("/documents/{doc_id}")
async def get_document(doc_id: str):
    row = await run_in_threadpool(db_get, doc_id)
    if not row:
        raise HTTPException(404, "document not found")
    return row


@app.put("/documents/{doc_id}")
async def update_document(doc_id: str, body: DocIn):
    row = await run_in_threadpool(db_update, doc_id, body.model_dump())
    if not row:
        raise HTTPException(404, "document not found")
    return row


@app.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    if not await run_in_threadpool(db_delete, doc_id):
        raise HTTPException(404, "document not found")
    return {"deleted": doc_id}


@app.get("/health")
async def health():
    try:
        ok = await run_in_threadpool(_es.ping)
        DB_UP.labels("elasticsearch").set(1 if ok else 0)
        return {"status": "ok" if ok else "degraded"}
    except Exception:  # noqa: BLE001
        DB_UP.labels("elasticsearch").set(0)
        return {"status": "degraded"}


async def _workload():
    while True:
        try:
            created = await run_in_threadpool(db_create, uuid.uuid4().hex[:12], {"name": f"item-{random.randint(1, 100)}", "tags": []})
            did = created["id"]
            await run_in_threadpool(db_get, did)
            await run_in_threadpool(db_search, f"item-{random.randint(1, 100)}", 5)
            if random.random() < 0.5:
                await run_in_threadpool(db_update, did, {"name": f"item-{random.randint(1, 100)}"})
            if random.random() < 0.4:
                await run_in_threadpool(db_delete, did)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(random.uniform(1.0, 3.0))


@app.on_event("startup")
async def startup():
    try:
        ok = await run_in_threadpool(_es.ping)
        DB_UP.labels("elasticsearch").set(1 if ok else 0)
        log.info("elasticsearch reachable=%s", ok)
    except Exception:  # noqa: BLE001
        DB_UP.labels("elasticsearch").set(0)
    asyncio.create_task(_workload())
