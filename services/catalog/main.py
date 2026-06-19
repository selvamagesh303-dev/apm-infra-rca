"""catalog-service — backed by MySQL."""
import asyncio
import logging
import os
import random
import time

import pymysql
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from common.observability import DB_UP, setup_metrics, timed

log = logging.getLogger("catalog")
HOST = os.getenv("MYSQL_HOST", "mysql")
USER = os.getenv("MYSQL_USER", "app")
PASSWORD = os.getenv("MYSQL_PASSWORD", "app")
DB = os.getenv("MYSQL_DB", "app")

app = FastAPI(title="catalog-service")
setup_metrics(app)


def _connect():
    return pymysql.connect(host=HOST, user=USER, password=PASSWORD, database=DB, connect_timeout=3)


def _init_schema():
    for attempt in range(30):
        try:
            conn = _connect()
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS products ("
                    "id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(128), price DECIMAL(10,2))"
                )
            conn.commit()
            conn.close()
            DB_UP.labels("mysql").set(1)
            log.info("mysql ready")
            return
        except Exception as exc:  # noqa: BLE001
            DB_UP.labels("mysql").set(0)
            log.warning("waiting for mysql (%s/30): %s", attempt + 1, exc)
            time.sleep(2)


def _lookup(name: str):
    with timed("mysql", "select"):
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, price FROM products WHERE name=%s LIMIT 1", (name,))
                return cur.fetchone()
        finally:
            conn.close()


def _upsert(name: str, price: float):
    with timed("mysql", "insert"):
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO products (name, price) VALUES (%s, %s)", (name, price))
            conn.commit()
        finally:
            conn.close()


@app.get("/catalog/{name}")
async def get_product(name: str):
    row = await run_in_threadpool(_lookup, name)
    return {"found": bool(row), "product": row}


@app.get("/health")
async def health():
    try:
        await run_in_threadpool(_lookup, "ping")
        DB_UP.labels("mysql").set(1)
        return {"status": "ok"}
    except Exception:  # noqa: BLE001
        DB_UP.labels("mysql").set(0)
        return {"status": "degraded"}


async def _workload():
    while True:
        try:
            await run_in_threadpool(_upsert, f"item-{random.randint(1, 100)}", round(random.uniform(1, 999), 2))
            await run_in_threadpool(_lookup, f"item-{random.randint(1, 100)}")
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(random.uniform(1.0, 3.0))


@app.on_event("startup")
async def startup():
    await run_in_threadpool(_init_schema)
    asyncio.create_task(_workload())
