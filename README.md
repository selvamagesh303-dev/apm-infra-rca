# Infra + APM Monitoring with Root Cause Analysis

Full-stack observability for a microservice system backed by **5 different
databases**, with infrastructure metrics and an **automated Root Cause Analysis
(RCA) engine** that correlates alerts across the service→database topology to
name the *root cause* instead of dumping a wall of symptoms.

```
 6 microservices (FastAPI, OpenTelemetry)        ┌───────────┐
   gateway → orders   → PostgreSQL               │  Jaeger   │ ◀─ traces (OTLP)
           → catalog  → MySQL                     └───────────┘
           → profiles → MongoDB         ┌──────────────┐
           → sessions → Redis    ─────▶ │  Prometheus  │ ──alerts──┐
           → search   → Elasticsearch   └──────────────┘           ▼
 DB exporters ×5 + node-exporter + cAdvisor ──────┘        ┌─────────────────┐
                                                            │  RCA backend    │
                                                            │  (FastAPI)      │──▶ Dashboard
                                                            │  correlation    │    + topology
                                                            └─────────────────┘    + RCA panel
```

## What it monitors

| Layer | Components | Via |
|---|---|---|
| **Databases** | PostgreSQL, MySQL, MongoDB, Redis, Elasticsearch | Prometheus exporter per DB + per-service `db_query_*` metrics |
| **Services** | gateway, orders, catalog, profiles, sessions, search | OpenTelemetry traces (→ Jaeger) + HTTP metrics (→ Prometheus) |
| **Infrastructure** | host CPU / memory / disk, per-container usage | node-exporter + cAdvisor |

### CRUD APIs

Each DB-backed service exposes a full REST CRUD resource (Pydantic-validated).
Every operation is timed into `db_query_duration_seconds{db,operation}` and traced.

| Service | DB | Resource | Endpoints |
|---|---|---|---|
| orders | PostgreSQL | `/orders` | `POST` · `GET` (list) · `GET /{id}` · `PUT /{id}` · `DELETE /{id}` |
| catalog | MySQL | `/products` | `POST` · `GET` · `GET /{id}` · `PUT /{id}` · `DELETE /{id}` |
| profiles | MongoDB | `/profiles` | `POST` · `GET` · `GET /{user_id}` · `PUT /{user_id}` · `DELETE /{user_id}` |
| sessions | Redis | `/sessions` | `POST` · `GET` · `GET /{id}` · `PUT /{id}` · `DELETE /{id}` |
| search | Elasticsearch | `/documents` | `POST` · `GET?q=` · `GET /{id}` · `PUT /{id}` · `DELETE /{id}` |

Example (ports are host-mapped per service — orders 8091, catalog 8092, profiles
8093, sessions 8094, search 8095):
```bash
curl -X POST localhost:8091/orders -H 'content-type: application/json' -d '{"sku":"SKU-1","qty":3}'
curl localhost:8091/orders            # list
curl localhost:8091/orders/1          # read
curl -X PUT localhost:8091/orders/1 -H 'content-type: application/json' -d '{"sku":"SKU-1","qty":9}'
curl -X DELETE localhost:8091/orders/1
```
Interactive docs per service at `/docs` (e.g. http://localhost:8091/docs).

## The RCA engine

Prometheus evaluates alert rules (`prometheus/alerts.yml`). The RCA backend reads
the **firing** alerts and walks the dependency graph (`rca/app/topology.py`):

> A component whose alert has **no alerting upstream dependency** is a **root
> cause**; its alerting downstream dependents are **symptoms** explained by it.

So if PostgreSQL fails, you don't get 3 separate red alerts (postgres, orders,
gateway) — you get **one incident: "root cause = postgres, affected: orders,
gateway"** with a confidence score and the supporting alerts. See
[`scripts/fault-injection.md`](scripts/fault-injection.md) to trigger it live.

## Quick start

```bash
docker compose up --build
```

The services self-generate load, so data flows immediately. Open:

| UI | URL |
|---|---|
| **Dashboard + RCA** | http://localhost:8000 |
| Prometheus (alerts/targets) | http://localhost:9090 |
| Jaeger (traces) | http://localhost:16686 |

Then trigger an incident to watch RCA work:
```bash
docker compose stop postgres     # ~1 min later the dashboard names postgres as root cause
docker compose start postgres    # incident auto-resolves
```

## Layout

```
.
├── services/                 # 6 microservices (one parametrized image)
│   ├── Dockerfile            #   opentelemetry-instrument uvicorn ${SERVICE}.main:app
│   ├── common/observability.py
│   └── gateway|orders|catalog|profiles|sessions|search/
├── rca/                      # RCA backend + served dashboard
│   ├── app/{prometheus,topology,rca,main}.py
│   └── static/               #   vanilla-JS dashboard (topology + RCA panel)
├── prometheus/{prometheus.yml,alerts.yml}
├── otel/otel-collector-config.yaml
├── exporters/mysqld-exporter.cnf
├── scripts/                  # load generator + fault-injection guide
└── docker-compose.yml
```

## Notes
- Metrics: services expose Prometheus `/metrics` (HTTP + custom `db_query_*`);
  traces go via OTLP to the collector → Jaeger. (`OTEL_METRICS_EXPORTER=none` so
  the two paths don't overlap.)
- Elasticsearch runs single-node with security disabled (demo settings).
- node-exporter / cAdvisor report the Docker (WSL2) VM on Docker Desktop.

## License

MIT
