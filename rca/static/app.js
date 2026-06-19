const SVGNS = "http://www.w3.org/2000/svg";
let topo = { nodes: [], edges: [] };

async function loadTopology() {
  try {
    topo = await (await fetch("/api/topology")).json();
  } catch (e) { /* retry on next render */ }
}

function tierOf(node) {
  if (node.id === "gateway") return 0;
  if (node.kind === "service") return 1;
  if (node.kind === "database") return 2;
  return 3; // infra
}

function renderTopology(rca, alertComponents) {
  const svg = document.getElementById("topo");
  svg.innerHTML = "";
  if (!topo.nodes.length) return;

  const roots = new Set((rca.incidents || []).map((i) => i.root_cause));
  const affected = new Set((rca.incidents || []).flatMap((i) => i.affected_components || []));

  // position nodes by tier
  const tiers = {};
  topo.nodes.forEach((n) => { (tiers[tierOf(n)] = tiers[tierOf(n)] || []).push(n); });
  const pos = {};
  Object.entries(tiers).forEach(([t, nodes]) => {
    nodes.forEach((n, i) => { pos[n.id] = { x: +t * 230 + 80, y: i * 64 + 40 }; });
  });

  // edges (skip infra edges to reduce clutter)
  topo.edges.forEach((e) => {
    if (["node", "containers"].includes(e.target)) return;
    const a = pos[e.source], b = pos[e.target];
    if (!a || !b) return;
    const line = document.createElementNS(SVGNS, "line");
    line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
    line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
    line.setAttribute("stroke", "#374151"); line.setAttribute("stroke-width", "1.5");
    svg.appendChild(line);
  });

  // nodes
  topo.nodes.forEach((n) => {
    const p = pos[n.id];
    const g = document.createElementNS(SVGNS, "g");
    const c = document.createElementNS(SVGNS, "circle");
    c.setAttribute("cx", p.x); c.setAttribute("cy", p.y); c.setAttribute("r", 9);
    let fill = "#34d399", ring = null;
    if (alertComponents.has(n.id)) fill = "#f87171";
    if (affected.has(n.id)) fill = "#fbbf24";
    if (roots.has(n.id)) { fill = "#f87171"; ring = "#fca5a5"; }
    c.setAttribute("fill", fill);
    if (ring) { c.setAttribute("stroke", ring); c.setAttribute("stroke-width", "4"); }
    g.appendChild(c);
    const t = document.createElementNS(SVGNS, "text");
    t.setAttribute("x", p.x + 14); t.setAttribute("y", p.y + 4);
    t.setAttribute("fill", "#cbd5e1"); t.setAttribute("font-size", "12");
    t.textContent = n.id;
    g.appendChild(t);
    svg.appendChild(g);
  });
}

function renderRCA(rca) {
  const sec = document.getElementById("rca");
  const body = document.getElementById("rca-body");
  if (!rca || rca.status !== "incident" || !rca.incidents.length) {
    sec.className = "card rca-ok";
    body.innerHTML = '<span class="good">✓ All systems nominal — no active incidents.</span>';
    return;
  }
  sec.className = "card rca-incident";
  body.innerHTML = rca.incidents.map((i) => `
    <div class="incident">
      <span class="conf">confidence ${Math.round(i.confidence * 100)}%</span>
      <div class="root">⛳ Root cause: ${i.root_cause} <small class="muted">(${i.kind}, ${i.severity})</small></div>
      <p>${i.explanation}</p>
      <div>${i.triggering_alerts.map((a) => `<span class="chip">${a.alertname}</span>`).join("")}</div>
      ${i.affected_components.length ? `<div class="chips">Affected: ${i.affected_components.map((c) => `<span class="chip affected">${c}</span>`).join("")}</div>` : ""}
    </div>`).join("");
}

function pct(x) { return x == null ? "—" : x.toFixed(0) + "%"; }
function gauge(label, value) {
  const v = value == null ? 0 : value;
  const color = v > 90 ? "#f87171" : v > 75 ? "#fbbf24" : "#34d399";
  return `<div class="gauge"><div class="lbl">${label}</div><div class="val">${pct(value)}</div>
    <div class="bar"><div style="width:${Math.min(v,100)}%;background:${color}"></div></div></div>`;
}

function render(s) {
  document.getElementById("infra").innerHTML =
    gauge("CPU", s.infra.cpu_pct) + gauge("Memory", s.infra.memory_pct) + gauge("Disk", s.infra.disk_pct);

  document.querySelector("#db-table tbody").innerHTML = s.databases.map((d) => `
    <tr><td>${d.db}</td><td>${d.p95_ms} ms</td>
    <td class="${d.error_rate > 0 ? "bad" : ""}">${d.error_rate}</td>
    <td class="${d.reachable ? "good" : "bad"}">${d.reachable ? "yes" : "no"}</td>
    <td class="${d.exporter_up ? "good" : "bad"}">${d.exporter_up ? "up" : "down"}</td></tr>`).join("");

  document.querySelector("#svc-table tbody").innerHTML = s.services.map((v) => `
    <tr><td>${v.service}</td><td>${v.request_rate}</td><td>${v.p95_ms} ms</td>
    <td class="${v.error_rate > 0.1 ? "bad" : ""}">${(v.error_rate * 100).toFixed(1)}%</td>
    <td class="${v.up ? "good" : "bad"}">${v.up ? "✓" : "✗"}</td></tr>`).join("");

  document.getElementById("alerts").innerHTML = s.alerts.length
    ? s.alerts.map((a) => `<li class="sev-${a.severity}">● <b>${a.alertname}</b> — ${a.summary || a.component}</li>`).join("")
    : '<li class="muted">none</li>';

  renderRCA(s.rca);
  renderTopology(s.rca, new Set(s.alerts.map((a) => a.component)));
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  const badge = document.getElementById("conn");
  ws.onopen = () => { badge.textContent = "live"; badge.className = "badge online"; };
  ws.onclose = () => { badge.textContent = "reconnecting…"; badge.className = "badge offline"; setTimeout(connect, 2000); };
  ws.onmessage = (e) => render(JSON.parse(e.data));
}

loadTopology().then(connect);
