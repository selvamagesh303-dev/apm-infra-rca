"""Async Prometheus HTTP API client (instant queries + active alerts)."""
import logging

import httpx

from .config import settings

log = logging.getLogger("rca.prometheus")


async def _get(path: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.get(f"{settings.prometheus_url}{path}", params=params or {})
        resp.raise_for_status()
        return resp.json()


async def query(promql: str) -> list[dict]:
    """Run an instant query. Returns [{metric: {...labels}, value: float}]."""
    try:
        data = await _get("/api/v1/query", {"query": promql})
        out = []
        for r in data.get("data", {}).get("result", []):
            try:
                out.append({"metric": r["metric"], "value": float(r["value"][1])})
            except (KeyError, ValueError):
                continue
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("query failed (%s): %s", promql, exc)
        return []


async def active_alerts() -> list[dict]:
    """Return currently firing alerts with their labels + annotations."""
    try:
        data = await _get("/api/v1/alerts")
        alerts = data.get("data", {}).get("alerts", [])
        return [a for a in alerts if a.get("state") == "firing"]
    except Exception as exc:  # noqa: BLE001
        log.warning("alerts fetch failed: %s", exc)
        return []
