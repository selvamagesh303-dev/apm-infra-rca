const RESOURCES = {
  orders: {
    label: "Orders", service: "orders", path: "orders", id: "id", idUserProvided: false,
    createFields: [{ k: "sku", t: "text" }, { k: "qty", t: "number" }],
    editFields: [{ k: "sku", t: "text" }, { k: "qty", t: "number" }],
    cols: ["id", "sku", "qty", "created"],
  },
  products: {
    label: "Products", service: "catalog", path: "products", id: "id", idUserProvided: false,
    createFields: [{ k: "name", t: "text" }, { k: "price", t: "number" }],
    editFields: [{ k: "name", t: "text" }, { k: "price", t: "number" }],
    cols: ["id", "name", "price"],
  },
  profiles: {
    label: "Profiles", service: "profiles", path: "profiles", id: "user_id", idUserProvided: true,
    createFields: [{ k: "user_id", t: "text" }, { k: "name", t: "text" }],
    editFields: [{ k: "name", t: "text" }],
    cols: ["user_id", "name", "visits"],
  },
  sessions: {
    label: "Sessions", service: "sessions", path: "sessions", id: "session_id", idUserProvided: true,
    createFields: [{ k: "session_id", t: "text" }, { k: "user", t: "text" }, { k: "data", t: "text" }],
    editFields: [{ k: "user", t: "text" }, { k: "data", t: "text" }],
    cols: ["session_id", "user", "data"],
  },
  documents: {
    label: "Documents", service: "search", path: "documents", id: "id", idUserProvided: false,
    createFields: [{ k: "name", t: "text" }, { k: "tags", t: "text", array: true }],
    editFields: [{ k: "name", t: "text" }, { k: "tags", t: "text", array: true }],
    cols: ["id", "name", "tags"],
  },
};

let current = "orders";
let editingId = null;

function api(service, sub, method = "GET", body) {
  return fetch(`/admin/api/${service}/${sub}`, {
    method,
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
}

function msg(text, ok) {
  const m = document.getElementById("msg");
  m.textContent = text;
  m.className = "msg " + (ok ? "ok" : "err");
}

function coerce(field, raw) {
  if (field.array) return raw ? raw.split(",").map((s) => s.trim()).filter(Boolean) : [];
  if (field.t === "number") return raw === "" ? null : Number(raw);
  return raw;
}

function renderTabs() {
  document.getElementById("tabs").innerHTML = Object.keys(RESOURCES)
    .map((k) => `<button data-k="${k}" class="${k === current ? "active" : ""}">${RESOURCES[k].label}</button>`)
    .join("");
  document.querySelectorAll("#tabs button").forEach((b) =>
    b.addEventListener("click", () => { current = b.dataset.k; editingId = null; renderAll(); }));
}

function renderForm() {
  const r = RESOURCES[current];
  const fields = editingId !== null ? r.editFields : r.createFields;
  document.getElementById("form-title").textContent = editingId !== null ? `Edit ${r.id}=${editingId}` : `Create ${r.label}`;
  const form = document.getElementById("crud-form");
  form.innerHTML =
    fields.map((f) => `
      <div class="field">
        <label>${f.k}${f.array ? " (comma-sep)" : ""}</label>
        <input name="${f.k}" type="${f.t}" ${f.t === "number" ? 'step="any"' : ""} />
      </div>`).join("") +
    `<button class="btn" type="submit">${editingId !== null ? "Update" : "Create"}</button>` +
    (editingId !== null ? `<button class="btn secondary" type="button" id="cancel">Cancel</button>` : "");

  form.onsubmit = onSubmit;
  if (editingId !== null) document.getElementById("cancel").onclick = () => { editingId = null; renderAll(); };
}

async function onSubmit(e) {
  e.preventDefault();
  const r = RESOURCES[current];
  const fields = editingId !== null ? r.editFields : r.createFields;
  const payload = {};
  fields.forEach((f) => { payload[f.k] = coerce(f, e.target[f.k].value); });
  try {
    const resp = editingId !== null
      ? await api(r.service, `${r.path}/${encodeURIComponent(editingId)}`, "PUT", payload)
      : await api(r.service, r.path, "POST", payload);
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.status);
    msg(editingId !== null ? "Updated ✓" : "Created ✓", true);
    editingId = null;
    renderAll();
  } catch (err) { msg("Error: " + err.message, false); }
}

async function loadList() {
  const r = RESOURCES[current];
  const thead = document.querySelector("#table thead");
  const tbody = document.querySelector("#table tbody");
  document.getElementById("list-title").textContent = `${r.label} (${r.service})`;
  thead.innerHTML = `<tr>${r.cols.map((c) => `<th>${c}</th>`).join("")}<th>actions</th></tr>`;
  try {
    const resp = await api(r.service, `${r.path}?limit=50`);
    const rows = await resp.json();
    tbody.innerHTML = rows.length
      ? rows.map((row) => `<tr>
          ${r.cols.map((c) => `<td>${fmt(row[c])}</td>`).join("")}
          <td>
            <button class="btn secondary" data-edit='${JSON.stringify(row).replace(/'/g, "&#39;")}'>Edit</button>
            <button class="btn danger" data-del="${row[r.id]}">Delete</button>
          </td></tr>`).join("")
      : `<tr><td colspan="${r.cols.length + 1}" class="muted">no rows</td></tr>`;
    tbody.querySelectorAll("[data-edit]").forEach((b) =>
      b.addEventListener("click", () => startEdit(JSON.parse(b.dataset.edit))));
    tbody.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", () => del(b.dataset.del)));
  } catch (err) { tbody.innerHTML = `<tr><td class="muted">load failed: ${err.message}</td></tr>`; }
}

function fmt(v) { return Array.isArray(v) ? v.join(", ") : v == null ? "" : v; }

function startEdit(row) {
  const r = RESOURCES[current];
  editingId = row[r.id];
  renderForm();
  r.editFields.forEach((f) => {
    const el = document.querySelector(`#crud-form [name="${f.k}"]`);
    if (el) el.value = Array.isArray(row[f.k]) ? row[f.k].join(", ") : (row[f.k] ?? "");
  });
}

async function del(id) {
  const r = RESOURCES[current];
  if (!confirm(`Delete ${r.id}=${id}?`)) return;
  try {
    const resp = await api(r.service, `${r.path}/${encodeURIComponent(id)}`, "DELETE");
    if (!resp.ok) throw new Error(resp.status);
    msg("Deleted ✓", true);
    if (String(editingId) === String(id)) editingId = null;
    renderAll();
  } catch (err) { msg("Delete failed: " + err.message, false); }
}

function renderAll() { renderTabs(); renderForm(); loadList(); }

document.getElementById("refresh").addEventListener("click", loadList);
renderAll();
