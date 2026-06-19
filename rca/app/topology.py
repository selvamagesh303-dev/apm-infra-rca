"""Service / database / infra dependency topology.

`DEPENDS_ON[x]` = the components x directly relies on. The RCA engine walks this
graph: a component whose alert has no alerting upstream dependency is a likely
root cause; its alerting downstream dependents are treated as symptoms.
"""

SERVICES = ["gateway", "orders", "catalog", "profiles", "sessions", "search"]
DATABASES = ["postgres", "mysql", "mongodb", "redis", "elasticsearch"]
INFRA = ["node", "containers"]

DEPENDS_ON: dict[str, list[str]] = {
    "gateway": ["orders", "catalog", "profiles", "sessions", "search"],
    "orders": ["postgres"],
    "catalog": ["mysql"],
    "profiles": ["mongodb"],
    "sessions": ["redis"],
    "search": ["elasticsearch"],
}
# Everything runs on the host/containers, so all components depend on infra.
for _c in SERVICES + DATABASES:
    DEPENDS_ON.setdefault(_c, [])
    DEPENDS_ON[_c] = DEPENDS_ON[_c] + ["node", "containers"]
for _i in INFRA:
    DEPENDS_ON.setdefault(_i, [])

ALL_COMPONENTS = SERVICES + DATABASES + INFRA


def _reverse() -> dict[str, list[str]]:
    rev: dict[str, list[str]] = {c: [] for c in ALL_COMPONENTS}
    for src, deps in DEPENDS_ON.items():
        for d in deps:
            rev.setdefault(d, []).append(src)
    return rev


DEPENDENTS = _reverse()  # who depends on X (consumers)


def _transitive(graph: dict[str, list[str]], start: str) -> set[str]:
    seen: set[str] = set()
    stack = list(graph.get(start, []))
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(graph.get(n, []))
    return seen


def upstream(component: str) -> set[str]:
    """All components this one (transitively) depends on."""
    return _transitive(DEPENDS_ON, component)


def downstream(component: str) -> set[str]:
    """All components that (transitively) depend on this one."""
    return _transitive(DEPENDENTS, component)


def graph() -> dict:
    """Topology for the dashboard's dependency view."""
    nodes = []
    kind = {**{s: "service" for s in SERVICES},
            **{d: "database" for d in DATABASES},
            **{i: "infra" for i in INFRA}}
    for c in ALL_COMPONENTS:
        nodes.append({"id": c, "kind": kind[c]})
    edges = [{"source": s, "target": t} for s, deps in DEPENDS_ON.items() for t in deps]
    return {"nodes": nodes, "edges": edges}
