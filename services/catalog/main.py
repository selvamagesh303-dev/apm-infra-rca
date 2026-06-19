"""catalog-service — full CRUD over MySQL."""
import asyncio
import logging
import os
import random
import time

import pymysql
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from common.observability import DB_UP, setup_metrics, timed

log = logging.getLogger("catalog")
HOST = os.getenv("MYSQL_HOST", "mysql")
USER = os.getenv("MYSQL_USER", "app")
PASSWORD = os.getenv("MYSQL_PASSWORD", "app")
DB = os.getenv("MYSQL_DB", "app")

app = FastAPI(title="catalog-service")
setup_metrics(app)


class ProductIn(BaseModel):
    name: str
    price: float


def _connect():
    return pymysql.connect(
        host=HOST, user=USER, password=PASSWORD, database=DB,
        connect_timeout=3, cursorclass=pymysql.cursors.DictCursor, autocommit=True,
    )


def _init_schema():
    for attempt in range(30):
        try:
            conn = _connect()
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS products ("
                    "id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(128) NOT NULL, "
                    "price DECIMAL(10,2) NOT NULL)"
                )
            conn.close()
            DB_UP.labels("mysql").set(1)
            log.info("mysql ready")
            return
        except Exception as exc:  # noqa: BLE001
            DB_UP.labels("mysql").set(0)
            log.warning("waiting for mysql (%s/30): %s", attempt + 1, exc)
            time.sleep(2)


def db_create(name: str, price: float) -> dict:
    with timed("mysql", "insert"):
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO products (name, price) VALUES (%s, %s)", (name, price))
                pid = cur.lastrowid
            return {"id": pid, "name": name, "price": price}
        finally:
            conn.close()


def db_list(limit: int) -> list[dict]:
    with timed("mysql", "select"):
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, price FROM products ORDER BY id DESC LIMIT %s", (limit,))
                return cur.fetchall()
        finally:
            conn.close()


def db_get(pid: int) -> dict | None:
    with timed("mysql", "select"):
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, price FROM products WHERE id=%s", (pid,))
                return cur.fetchone()
        finally:
            conn.close()


def db_update(pid: int, name: str, price: float) -> bool:
    with timed("mysql", "update"):
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE products SET name=%s, price=%s WHERE id=%s", (name, price, pid))
                return cur.rowcount > 0
        finally:
            conn.close()


def db_delete(pid: int) -> bool:
    with timed("mysql", "delete"):
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM products WHERE id=%s", (pid,))
                return cur.rowcount > 0
        finally:
            conn.close()


@app.post("/products", status_code=201)
async def create_product(body: ProductIn):
    return await run_in_threadpool(db_create, body.name, body.price)


@app.get("/products")
async def list_products(limit: int = 50):
    return await run_in_threadpool(db_list, limit)


@app.get("/products/{pid}")
async def get_product(pid: int):
    row = await run_in_threadpool(db_get, pid)
    if not row:
        raise HTTPException(404, "product not found")
    return row


@app.put("/products/{pid}")
async def update_product(pid: int, body: ProductIn):
    if not await run_in_threadpool(db_update, pid, body.name, body.price):
        raise HTTPException(404, "product not found")
    return {"id": pid, **body.model_dump()}


@app.delete("/products/{pid}")
async def delete_product(pid: int):
    if not await run_in_threadpool(db_delete, pid):
        raise HTTPException(404, "product not found")
    return {"deleted": pid}


@app.get("/health")
async def health():
    try:
        await run_in_threadpool(db_list, 1)
        DB_UP.labels("mysql").set(1)
        return {"status": "ok"}
    except Exception:  # noqa: BLE001
        DB_UP.labels("mysql").set(0)
        return {"status": "degraded"}


async def _workload():
    while True:
        try:
            created = await run_in_threadpool(db_create, f"item-{random.randint(1, 100)}", round(random.uniform(1, 999), 2))
            pid = created["id"]
            await run_in_threadpool(db_get, pid)
            await run_in_threadpool(db_list, 20)
            if random.random() < 0.5:
                await run_in_threadpool(db_update, pid, created["name"], round(random.uniform(1, 999), 2))
            if random.random() < 0.3:
                await run_in_threadpool(db_delete, pid)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(random.uniform(1.0, 3.0))


@app.on_event("startup")
async def startup():
    await run_in_threadpool(_init_schema)
    asyncio.create_task(_workload())
