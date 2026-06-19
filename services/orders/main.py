"""orders-service — backed by PostgreSQL."""
import asyncio
import logging
import os
import random

import psycopg2
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from common.observability import DB_UP, setup_metrics, timed

log = logging.getLogger("orders")
DSN = os.getenv("POSTGRES_DSN", "postgresql://app:app@postgres:5432/app")

app = FastAPI(title="orders-service")
setup_metrics(app)


def _connect():
    return psycopg2.connect(DSN, connect_timeout=3)


def _init_schema():
    for attempt in range(30):
        try:
            with _connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS orders ("
                    "id SERIAL PRIMARY KEY, sku TEXT, qty INT, created TIMESTAMP DEFAULT now())"
                )
                conn.commit()
            DB_UP.labels("postgres").set(1)
            log.info("postgres ready")
            return
        except Exception as exc:  # noqa: BLE001
            DB_UP.labels("postgres").set(0)
            log.warning("waiting for postgres (%s/30): %s", attempt + 1, exc)
            __import__("time").sleep(2)


def _create_order(sku: str, qty: int) -> int:
    with timed("postgres", "insert"):
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO orders (sku, qty) VALUES (%s, %s) RETURNING id", (sku, qty))
            oid = cur.fetchone()[0]
            conn.commit()
            return oid


def _count_orders() -> int:
    with timed("postgres", "select"):
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM orders")
            return cur.fetchone()[0]


@app.post("/orders/{sku}")
async def create_order(sku: str, qty: int = 1):
    oid = await run_in_threadpool(_create_order, sku, qty)
    return {"order_id": oid, "sku": sku, "qty": qty}


@app.get("/orders/count")
async def count_orders():
    return {"orders": await run_in_threadpool(_count_orders)}


@app.get("/health")
async def health():
    try:
        await run_in_threadpool(_count_orders)
        DB_UP.labels("postgres").set(1)
        return {"status": "ok"}
    except Exception:  # noqa: BLE001
        DB_UP.labels("postgres").set(0)
        return {"status": "degraded"}


async def _workload():
    while True:
        try:
            await run_in_threadpool(_create_order, f"SKU-{random.randint(1, 50)}", random.randint(1, 5))
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(random.uniform(1.0, 3.0))


@app.on_event("startup")
async def startup():
    await run_in_threadpool(_init_schema)
    asyncio.create_task(_workload())
