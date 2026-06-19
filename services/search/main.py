"""search-service — backed by Elasticsearch."""
import asyncio
import logging
import os
import random

from elasticsearch import Elasticsearch
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from common.observability import DB_UP, setup_metrics, timed

log = logging.getLogger("search")
URL = os.getenv("ELASTICSEARCH_URL", "http://elasticsearch:9200")
INDEX = "catalog"

app = FastAPI(title="search-service")
setup_metrics(app)
_es = Elasticsearch(URL, request_timeout=3, retry_on_timeout=False)


def _index(doc_id: str, name: str):
    with timed("elasticsearch", "index"):
        _es.index(index=INDEX, id=doc_id, document={"name": name})


def _search(term: str):
    with timed("elasticsearch", "search"):
        res = _es.search(index=INDEX, query={"match": {"name": term}}, size=5)
        return res["hits"]["total"]["value"]


@app.get("/search")
async def search(q: str = "item"):
    try:
        hits = await run_in_threadpool(_search, q)
        return {"query": q, "hits": hits}
    except Exception:  # noqa: BLE001
        return {"query": q, "hits": 0, "status": "degraded"}


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
            n = random.randint(1, 100)
            await run_in_threadpool(_index, str(n), f"item-{n}")
            await run_in_threadpool(_search, f"item-{random.randint(1, 100)}")
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
