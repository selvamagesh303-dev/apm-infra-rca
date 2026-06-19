"""Shared metrics + DB-timing helpers for all microservices.

Each service exposes /metrics (HTTP server metrics via the instrumentator) plus
custom DB metrics (`db_query_duration_seconds`, `db_query_errors_total`) labelled
by database and operation. Prometheus scrapes these; the RCA engine reads them to
distinguish "the service is slow" from "the service's database is slow".
"""
import logging
import time
from contextlib import contextmanager

from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds",
    "Database query duration in seconds",
    ["db", "operation"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
DB_QUERY_ERRORS = Counter(
    "db_query_errors_total", "Database query errors", ["db", "operation"]
)
DB_UP = Gauge("service_db_up", "1 if the service's database is reachable", ["db"])


@contextmanager
def timed(db: str, operation: str):
    """Time a DB operation and record duration + errors."""
    start = time.perf_counter()
    try:
        yield
    except Exception:
        DB_QUERY_ERRORS.labels(db, operation).inc()
        raise
    finally:
        DB_QUERY_DURATION.labels(db, operation).observe(time.perf_counter() - start)


def setup_metrics(app) -> None:
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
