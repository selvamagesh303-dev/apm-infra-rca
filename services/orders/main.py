"""orders-service — full CRUD over PostgreSQL."""
import asyncio
import logging
import os
import random
import time

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from common.observability import DB_UP, setup_metrics, timed

log = logging.getLogger("orders")
DSN = os.getenv("POSTGRES_DSN", "postgresql://app:app@postgres:5432/app")

app = FastAPI(title="orders-service")
setup_metrics(app)


class OrderIn(BaseModel):
    sku: str
    qty: int = 1


def _connect():
    return psycopg2.connect(DSN, connect_timeout=3)


def _init_schema():
    for attempt in range(30):
        try:
            with _connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS orders ("
                    "id SERIAL PRIMARY KEY, sku TEXT NOT NULL, qty INT NOT NULL, "
                    "created TIMESTAMPTZ DEFAULT now())"
                )
                conn.commit()
            DB_UP.labels("postgres").set(1)
            log.info("postgres ready")
            return
        except Exception as exc:  # noqa: BLE001
            DB_UP.labels("postgres").set(0)
            log.warning("waiting for postgres (%s/30): %s", attempt + 1, exc)
            time.sleep(2)


def _row(cur):
    r = cur.fetchone()
    return dict(r) if r else None


# ---- CRUD (sync, run in threadpool) ----
def db_create(sku: str, qty: int) -> dict:
    with timed("postgres", "insert"), _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("INSERT INTO orders (sku, qty) VALUES (%s, %s) RETURNING *", (sku, qty))
            row = _row(cur)
        conn.commit()
        return row


def db_list(limit: int) -> list[dict]:
    with timed("postgres", "select"), _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM orders ORDER BY id DESC LIMIT %s", (limit,))
            return [dict(r) for r in cur.fetchall()]


def db_get(order_id: int) -> dict | None:
    with timed("postgres", "select"), _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
            return _row(cur)


def db_update(order_id: int, sku: str, qty: int) -> dict | None:
    with timed("postgres", "update"), _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("UPDATE orders SET sku=%s, qty=%s WHERE id=%s RETURNING *", (sku, qty, order_id))
            row = _row(cur)
        conn.commit()
        return row


def db_delete(order_id: int) -> bool:
    with timed("postgres", "delete"), _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM orders WHERE id = %s", (order_id,))
            deleted = cur.rowcount
        conn.commit()
        return deleted > 0


# ---- HTTP API ----
@app.post("/orders", status_code=201)
async def create_order(body: OrderIn):
    return await run_in_threadpool(db_create, body.sku, body.qty)


@app.get("/orders")
async def list_orders(limit: int = 50):
    return await run_in_threadpool(db_list, limit)


@app.get("/orders/{order_id}")
async def get_order(order_id: int):
    row = await run_in_threadpool(db_get, order_id)
    if not row:
        raise HTTPException(404, "order not found")
    return row


@app.put("/orders/{order_id}")
async def update_order(order_id: int, body: OrderIn):
    row = await run_in_threadpool(db_update, order_id, body.sku, body.qty)
    if not row:
        raise HTTPException(404, "order not found")
    return row


@app.delete("/orders/{order_id}")
async def delete_order(order_id: int):
    if not await run_in_threadpool(db_delete, order_id):
        raise HTTPException(404, "order not found")
    return {"deleted": order_id}


@app.get("/health")
async def health():
    try:
        await run_in_threadpool(db_list, 1)
        DB_UP.labels("postgres").set(1)
        return {"status": "ok"}
    except Exception:  # noqa: BLE001
        DB_UP.labels("postgres").set(0)
        return {"status": "degraded"}


async def _workload():
    """Exercise every CRUD path so all operations show up in metrics."""
    while True:
        try:
            created = await run_in_threadpool(db_create, f"SKU-{random.randint(1, 50)}", random.randint(1, 5))
            oid = created["id"]
            await run_in_threadpool(db_get, oid)
            await run_in_threadpool(db_list, 20)
            if random.random() < 0.5:
                await run_in_threadpool(db_update, oid, created["sku"], random.randint(1, 9))
            if random.random() < 0.3:
                await run_in_threadpool(db_delete, oid)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(random.uniform(1.0, 3.0))


@app.on_event("startup")
async def startup():
    await run_in_threadpool(_init_schema)
    asyncio.create_task(_workload())
