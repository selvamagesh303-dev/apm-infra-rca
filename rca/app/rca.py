"""Heuristic root-cause analysis.

Given the set of currently firing alerts and the dependency topology, decide
which alerting components are *root causes* vs *symptoms*:

  A component is a ROOT CAUSE if it is alerting and none of the components it
  depends on are also alerting. Its alerting downstream dependents are SYMPTOMS
  explained by it.

Example: if `postgres` and `orders` both alert and `orders -> postgres`, then
`orders` has an alerting upstream (postgres) so it's a symptom; `postgres` has
no alerting upstream so it's flagged as the root cause, and `orders` (plus
anything depending on orders, like `gateway`) is listed as affected.
"""
from . import topology

_SEV_WEIGHT = {"critical": 3, "warning": 2, "info": 1}
_KIND_WEIGHT = {"infra": 3, "database": 2, "service": 1}


def _component_of(alert: dict) -> str | None:
    """Resolve the topology component a firing alert belongs to."""
    labels = alert.get("labels", {})
    for key in ("component", "db", "service"):
        val = labels.get(key)
        if val in topology.ALL_COMPONENTS:
            return val
    return None


def _kind_of(component: str) -> str:
    if component in topology.DATABASES:
        return "database"
    if component in topology.INFRA:
        return "infra"
    return "service"


def analyze(alerts: list[dict]) -> dict:
    # Group firing alerts by the component they implicate.
    impacted: dict[str, list[dict]] = {}
    for a in alerts:
        comp = _component_of(a)
        if comp:
            impacted.setdefault(comp, []).append(a)

    if not impacted:
        return {"status": "healthy", "incidents": [], "impacted": []}

    incidents = []
    for comp, comp_alerts in impacted.items():
        # Symptom if any component it depends on is also alerting.
        alerting_upstream = topology.upstream(comp) & impacted.keys()
        if alerting_upstream:
            continue  # explained by something deeper — not a root cause

        affected = sorted(topology.downstream(comp) & impacted.keys())
        max_sev = max((_SEV_WEIGHT.get(a["labels"].get("severity", "warning"), 2) for a in comp_alerts), default=2)
        score = _KIND_WEIGHT[_kind_of(comp)] + max_sev + 2 * len(affected)
        confidence = min(0.55 + 0.12 * len(affected) + 0.05 * (max_sev - 1), 0.98)

        incidents.append({
            "root_cause": comp,
            "kind": _kind_of(comp),
            "confidence": round(confidence, 2),
            "score": score,
            "severity": _sev_name(max_sev),
            "triggering_alerts": [_brief(a) for a in comp_alerts],
            "affected_components": affected,
            "explanation": _explain(comp, comp_alerts, affected),
        })

    incidents.sort(key=lambda i: i["score"], reverse=True)
    return {
        "status": "incident",
        "incidents": incidents,
        "impacted": sorted(impacted.keys()),
    }


def _sev_name(weight: int) -> str:
    return {3: "critical", 2: "warning", 1: "info"}.get(weight, "warning")


def _brief(alert: dict) -> dict:
    return {
        "alertname": alert.get("labels", {}).get("alertname"),
        "severity": alert.get("labels", {}).get("severity"),
        "summary": alert.get("annotations", {}).get("summary"),
    }


def _explain(comp: str, comp_alerts: list[dict], affected: list[str]) -> str:
    names = ", ".join(sorted({a["labels"].get("alertname", "?") for a in comp_alerts}))
    base = f"'{comp}' ({_kind_of(comp)}) is the likely root cause — firing: {names}."
    if affected:
        base += (
            f" {len(affected)} dependent component(s) show correlated alerts "
            f"({', '.join(affected)}), consistent with an upstream failure in '{comp}'."
        )
    else:
        base += " No downstream components are alerting yet — isolated so far."
    return base
