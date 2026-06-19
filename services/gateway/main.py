"""gateway-service — public entry point that fans out to the DB-backed services.

A single /checkout call touches all five services (and therefore all five
databases), producing one distributed trace and exercising the whole topology —
which is what makes the RCA correlation meaningful.
"""
import asyncio
import logging
import os
import random

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from common.observability import setup_metrics

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

log = logging.getLogger("gateway")

SERVICES = {
    "orders": os.getenv("ORDERS_URL", "http://orders:8000"),
    "catalog": os.getenv("CATALOG_URL", "http://catalog:8000"),
    "profiles": os.getenv("PROFILES_URL", "http://profiles:8000"),
    "sessions": os.getenv("SESSIONS_URL", "http://sessions:8000"),
    "search": os.getenv("SEARCH_URL", "http://search:8000"),
}

app = FastAPI(title="gateway-service")
setup_metrics(app)
_client = httpx.AsyncClient(timeout=8.0)


async def _call(method: str, url: str, json: dict | None = None):
    try:
        resp = await _client.request(method, url, json=json)
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


@app.post("/checkout/{user_id}")
async def checkout(user_id: str):
    sku = f"SKU-{random.randint(1, 50)}"
    results = await asyncio.gather(
        _call("POST", f"{SERVICES['sessions']}/sessions", {"session_id": user_id, "user": user_id}),
        _call("GET", f"{SERVICES['profiles']}/profiles/{user_id}"),
        _call("GET", f"{SERVICES['catalog']}/products?limit=5"),
        _call("POST", f"{SERVICES['orders']}/orders", {"sku": sku, "qty": random.randint(1, 5)}),
        _call("GET", f"{SERVICES['search']}/documents?q=item-{random.randint(1, 100)}"),
    )
    keys = ["session", "profile", "catalog", "order", "search"]
    return {"user_id": user_id, **dict(zip(keys, results))}


@app.get("/health")
async def health():
    return {"status": "ok", "downstreams": list(SERVICES)}


@app.api_route("/admin/api/{service}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def admin_proxy(service: str, path: str, request: Request):
    """Same-origin proxy so the admin UI can do CRUD against any service without CORS."""
    base = SERVICES.get(service)
    if not base:
        return Response(content='{"error":"unknown service"}', status_code=404, media_type="application/json")
    body = await request.body()
    try:
        resp = await _client.request(
            request.method,
            f"{base}/{path}",
            params=dict(request.query_params),
            content=body or None,
            headers={"content-type": request.headers.get("content-type", "application/json")} if body else None,
        )
        return Response(content=resp.content, status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "application/json"))
    except Exception as exc:  # noqa: BLE001
        return Response(content=f'{{"error":"{exc}"}}', status_code=502, media_type="application/json")


async def _workload():
    while True:
        try:
            await checkout(f"user-{random.randint(1, 100)}")
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(random.uniform(0.8, 2.0))


@app.on_event("startup")
async def startup():
    asyncio.create_task(_workload())


# CRUD admin console (served same-origin; routes above take precedence).
app.mount("/admin", StaticFiles(directory=STATIC_DIR, html=True), name="admin")
