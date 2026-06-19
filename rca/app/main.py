"""Real-time infra/APM monitoring + RCA backend.

Polls Prometheus for alerts and metric summaries, runs the heuristic RCA engine,
pushes live snapshots over WebSocket, and serves the dashboard UI.
"""
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from . import prometheus, rca, topology
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("rca")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")


def _by_label(rows: list[dict], label: str) -> dict[str, float]:
    out = {}
    for r in rows:
        key = r["metric"].get(label)
        if key is not None:
            out[key] = round(r["value"], 3)
    return out


async def summarize_services() -> list[dict]:
    rate, p95, err, up = await asyncio.gather(
        prometheus.query("sum by (service) (rate(http_requests_total[1m]))"),
        prometheus.query("histogram_quantile(0.95, sum by (service, le) (rate(http_request_duration_seconds_bucket[1m])))"),
        prometheus.query(
            "sum by (service) (rate(http_requests_total{status=\"5xx\"}[2m])) "
            "/ sum by (service) (rate(http_requests_total[2m]))"
        ),
        prometheus.query('up{job="microservices"}'),
    )
    rate, p95, err, up = _by_label(rate, "service"), _by_label(p95, "service"), _by_label(err, "service"), _by_label(up, "service")
    return [
        {
            "service": s,
            "request_rate": rate.get(s, 0.0),
            "p95_ms": round(p95.get(s, 0.0) * 1000, 1),
            "error_rate": err.get(s, 0.0),
            "up": bool(up.get(s, 0)),
        }
        for s in topology.SERVICES
    ]


async def summarize_databases() -> list[dict]:
    p95, err, dbup, expup = await asyncio.gather(
        prometheus.query("histogram_quantile(0.95, sum by (db, le) (rate(db_query_duration_seconds_bucket[1m])))"),
        prometheus.query("sum by (db) (rate(db_query_errors_total[2m]))"),
        prometheus.query("max by (db) (service_db_up)"),
        prometheus.query('up{job="db-exporters"}'),
    )
    p95, err, dbup, expup = _by_label(p95, "db"), _by_label(err, "db"), _by_label(dbup, "db"), _by_label(expup, "db")
    return [
        {
            "db": d,
            "p95_ms": round(p95.get(d, 0.0) * 1000, 1),
            "error_rate": err.get(d, 0.0),
            "reachable": bool(dbup.get(d, 0)),
            "exporter_up": bool(expup.get(d, 0)),
        }
        for d in topology.DATABASES
    ]


async def summarize_infra() -> dict:
    cpu, mem, disk = await asyncio.gather(
        prometheus.query("100 - (avg(rate(node_cpu_seconds_total{mode=\"idle\"}[1m])) * 100)"),
        prometheus.query("(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100"),
        prometheus.query(
            "(1 - (node_filesystem_avail_bytes{mountpoint=\"/\"} / node_filesystem_size_bytes{mountpoint=\"/\"})) * 100"
        ),
    )
    val = lambda rows: round(rows[0]["value"], 1) if rows else None  # noqa: E731
    return {"cpu_pct": val(cpu), "memory_pct": val(mem), "disk_pct": val(disk)}


async def build_snapshot() -> dict:
    alerts = await prometheus.active_alerts()
    services, databases, infra = await asyncio.gather(
        summarize_services(), summarize_databases(), summarize_infra()
    )
    return {
        "type": "snapshot",
        "services": services,
        "databases": databases,
        "infra": infra,
        "alerts": [
            {
                "alertname": a.get("labels", {}).get("alertname"),
                "severity": a.get("labels", {}).get("severity"),
                "component": a.get("labels", {}).get("component") or a.get("labels", {}).get("db") or a.get("labels", {}).get("service"),
                "summary": a.get("annotations", {}).get("summary"),
            }
            for a in alerts
        ],
        "rca": rca.analyze(alerts),
    }


class Hub:
    def __init__(self):
        self.conns: set[WebSocket] = set()
        self.latest: dict | None = None

    async def broadcast(self, msg: dict):
        data = json.dumps(msg)
        for ws in list(self.conns):
            try:
                await ws.send_text(data)
            except Exception:  # noqa: BLE001
                self.conns.discard(ws)


hub = Hub()


async def _poller():
    while True:
        try:
            hub.latest = await build_snapshot()
            await hub.broadcast(hub.latest)
        except Exception:  # noqa: BLE001
            log.exception("poll failed")
        await asyncio.sleep(settings.poll_interval_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_poller())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Infra/APM Monitoring + RCA", version="1.0.0", lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"status": "ok", "prometheus": settings.prometheus_url}


@app.get("/api/topology")
async def get_topology():
    return topology.graph()


@app.get("/api/snapshot")
async def get_snapshot():
    return hub.latest or await build_snapshot()


@app.get("/api/rca")
async def get_rca():
    return rca.analyze(await prometheus.active_alerts())


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    hub.conns.add(websocket)
    try:
        if hub.latest:
            await websocket.send_text(json.dumps(hub.latest))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.conns.discard(websocket)
    except Exception:  # noqa: BLE001
        hub.conns.discard(websocket)


# Serve the dashboard UI (mounted last so /api and /ws take precedence).
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
