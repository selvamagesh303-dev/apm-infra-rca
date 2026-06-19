# Demoing the RCA engine

The services self-generate load, so metrics flow as soon as the stack is up.
To see RCA actually pin a **root cause**, inject a fault into a dependency and
watch the dashboard: the failing component turns red with a ring, the services
that depend on it turn amber ("affected"), and the RCA panel names the root cause.

### Kill a database (root cause = the DB, symptoms = its service + gateway)
```bash
docker compose stop postgres      # orders depends on postgres
# Within ~1 min: ServiceDBUnreachable/HighDBErrorRate fire for postgres,
# HighServiceErrorRate fires for orders (and gateway sees errors).
# RCA reports: root cause = postgres; affected = orders, gateway.
docker compose start postgres     # incident auto-resolves
```

### Saturate Redis connections
```bash
docker compose exec redis redis-cli CONFIG SET maxclients 5
```

### Make Elasticsearch unhealthy
```bash
docker compose stop elasticsearch   # search-service degrades; ES alerts fire
```

Why it works: the RCA engine walks the service→DB dependency graph. A component
whose alert has **no alerting upstream dependency** is flagged as the root cause;
its alerting downstream dependents are reported as symptoms — so a single DB
outage is summarized as one incident instead of a wall of correlated alerts.
