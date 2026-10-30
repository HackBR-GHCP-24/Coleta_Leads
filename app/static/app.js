const $ = (s) => document.querySelector(s);
const state = { token: localStorage.getItem("token"), meta: null, timer: null };

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : null);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401 && !path.includes("/auth/login")) { logout(); throw new Error("Sessão expirada"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Erro ${res.status}`);
  return data;
}

function toast(msg, ms = 4000) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), ms);
}

// ---------- Auth ----------
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  try {
    const r = await api("/api/auth/login", { method: "POST", body: { username: $("#login-user").value, password: $("#login-pass").value } });
    state.token = r.token; localStorage.setItem("token", r.token); localStorage.setItem("user", r.username);
    await boot();
  } catch (err) { $("#login-error").textContent = err.message; }
});

function logout() {
  state.token = null; localStorage.removeItem("token");
  $("#app-view").classList.add("hidden"); $("#login-view").classList.remove("hidden");
}
$("#btn-logout").addEventListener("click", logout);

// ---------- Boot ----------
async function boot() {
  if (!state.token) { logout(); return; }
  try { state.meta = await api("/api/meta"); } catch { return; }
  $("#login-view").classList.add("hidden"); $("#app-view").classList.remove("hidden");
  $("#user-name").textContent = localStorage.getItem("user") || "";
  const badge = $("#mode-badge");
  badge.textContent = state.meta.modo === "foundry" ? "● Agente Foundry conectado" : "● Modo demonstração";
  badge.className = `badge ${state.meta.modo}`;
  badge.title = state.meta.agent_id ? `Agent ID: ${state.meta.agent_id}` : "Configure FOUNDRY_AGENT_ID para usar o agente";
  $("#c-mock").checked = state.meta.modo !== "foundry";
  fillFilters();
  await refresh();
}

function fillFilters() {
  const m = state.meta;
  const opts = (sel, items, first) => { $(sel).innerHTML = `<option value="">${first}</option>` + items.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join(""); };
  opts("#f-cidade", m.cidades.map((c) => [c, c]), "Todas as cidades");
  opts("#f-servico", Object.entries(m.servicos), "Todos os serviços");
  opts("#f-status", m.status.map((s) => [s, s[0].toUpperCase() + s.slice(1)]), "Todos os status");
  const chips = (sel, items, name, checked) => { $(sel).innerHTML = items.map(([v, l]) => `<label class="chip"><input type="checkbox" name="${name}" value="${esc(v)}" ${checked ? "checked" : ""}/>${esc(l)}</label>`).join(""); };
  chips("#c-cidades", m.cidades.map((c) => [c, c]), "cidade", true);
  chips("#c-servicos", Object.entries(m.servicos), "servico", false);
}

["#f-cidade", "#f-servico", "#f-status"].forEach((s) => $(s).addEventListener("change", refresh));
$("#f-score").addEventListener("input", (e) => { $("#f-score-val").textContent = e.target.value; debounce(); });
$("#f-q").addEventListener("input", () => debounce());
function debounce() { clearTimeout(state.timer); state.timer = setTimeout(refresh, 250); }

async function refresh() {
  const p = new URLSearchParams();
  const add = (k, v) => v && p.set(k, v);
  add("cidade", $("#f-cidade").value); add("servico", $("#f-servico").value); add("status", $("#f-status").value);
  add("q", $("#f-q").value.trim()); add("min_score", $("#f-score").value);
  const [leads, stats] = await Promise.all([api(`/api/leads?${p}`), api("/api/stats")]);
  renderKpis(stats); renderLeads(leads);
}

function renderKpis(s) {
  const top = Object.entries(s.por_servico).sort((a, b) => b[1] - a[1])[0];
  const last = s.ultima_coleta;
  const kpi = (label, value, sub = "") => `<div class="kpi"><div class="label">${label}</div><div class="value${String(value).length > 8 ? " small" : ""}">${value}</div><div class="sub">${sub}</div></div>`;
  $("#kpis").innerHTML = [
    kpi("Leads coletados", s.total, Object.entries(s.por_cidade).map(([c, n]) => `${esc(c)}: ${n}`).join(" · ")),
    kpi("Oportunidades quentes", s.quentes, "score ≥ 75"),
    kpi("Score médio", s.score_medio, "ranking ponderado"),
    kpi("Serviço mais demandado", top ? esc(state.meta.servicos[top[0]] || top[0]).split(" (")[0] : "—", top ? `${top[1]} lead(s)` : ""),
    kpi("Última coleta", last ? new Date(last.created_at).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—", last ? `${esc(last.status)} · ${esc(last.origem || "")}` : "nenhuma coleta ainda"),
  ].join("");
}

const svcLabel = (k) => (state.meta.servicos[k] || k).split(" (")[0];

function renderLeads(leads) {
  $("#count").textContent = `${leads.length} lead(s)`;
  $("#empty").classList.toggle("hidden", leads.length > 0);
  $("#leads-body").innerHTML = leads.map((l) => `
    <tr data-id="${l.id}">
      <td class="rank">${l.rank}</td>
      <td class="company">${esc(l.empresa)}<small>${l.origem === "foundry" ? "🔎 Foundry" : "🧪 Demo"} · confiança ${esc(l.nivel_confianca || "—")}</small></td>
      <td>${esc(l.cidade)}</td>
      <td class="hide-sm">${esc(l.segmento)}</td>
      <td class="hide-sm" style="text-transform:capitalize">${esc(l.porte)}</td>
      <td class="hide-md"><div class="tags">${l.servicos.slice(0, 3).map((s) => `<span class="tag">${esc(svcLabel(s))}</span>`).join("")}${l.servicos.length > 3 ? `<span class="tag">+${l.servicos.length - 3}</span>` : ""}</div></td>
      <td><div class="score"><div class="bar"><i class="tier-${l.tier}" style="width:${l.score_final}%"></i></div><b>${l.score_final}</b></div></td>
      <td class="hide-sm"><span class="pill">${esc(l.status)}</span></td>
    </tr>`).join("");
}

$("#leads-body").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-id]"); if (tr) openLead(tr.dataset.id); });

// ---------- Detalhe ----------
async function openLead(id) {
  const l = await api(`/api/leads/${id}`);
  const d = l.detalhes;
  const site = safeUrl(d.site);
  const list = (arr, fn = esc) => (arr && arr.length ? `<ul>${arr.map((x) => `<li>${fn(x)}</li>`).join("")}</ul>` : `<p class="muted">—</p>`);
  const sinal = (s) => { const u = safeUrl(s.fonte); return `${esc(s.descricao)}${s.data ? ` <span class="muted">(${esc(s.data)})</span>` : ""}${u ? ` — <a href="${esc(u)}" target="_blank" rel="noopener">fonte</a>` : ""}`; };
  const fonte = (u) => (safeUrl(u) ? `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a>` : esc(u));
  $("#drawer-content").innerHTML = `
    <span class="pill ${l.tier}">${l.tier}</span>
    <h2>${esc(l.empresa)}</h2>
    <p class="muted">${esc(l.segmento)} · ${esc(l.cidade)}/${esc(d.uf || "SP")}${site ? ` · <a href="${esc(site)}" target="_blank" rel="noopener">site</a>` : ""}</p>
    <div class="facts">
      <div class="fact"><span>Score final (ranking)</span><div class="big-score">${l.score_final}</div></div>
      <div class="fact"><span>Score do agente</span><div class="big-score">${l.score_agente}</div></div>
      <div class="fact"><span>Porte</span>${esc(l.porte)}${d.funcionarios_estimados ? ` · ~${esc(d.funcionarios_estimados)} func.` : ""}</div>
      <div class="fact"><span>Confiança</span>${esc(d.nivel_confianca || "—")}</div>
      <div class="fact"><span>CNPJ</span>${esc(d.cnpj || "—")}</div>
      <div class="fact"><span>Contato sugerido</span>${esc(d.contato_sugerido?.cargo || "—")}<br><small class="muted">${esc(d.contato_sugerido?.canal || "")}</small></div>
    </div>
    <h3>Status do lead</h3>
    <select id="lead-status">${state.meta.status.map((s) => `<option ${s === l.status ? "selected" : ""}>${s}</option>`).join("")}</select>
    <h3>Serviços recomendados</h3>
    <div class="tags">${l.servicos.map((s) => `<span class="tag">${esc(state.meta.servicos[s] || s)}</span>`).join("") || "—"}</div>
    <h3>Justificativa</h3><p>${esc(d.justificativa)}</p>
    <h3>Necessidades identificadas</h3>${list(d.necessidades)}
    <h3>Sinais</h3>${list(d.sinais, sinal)}
    <h3>Fontes</h3>${list(d.fontes, fonte)}
    ${d.observacao ? `<p class="muted">${esc(d.observacao)}</p>` : ""}
    <p class="muted"><small>Origem: ${esc(l.origem)} · atualizado em ${new Date(l.updated_at).toLocaleString("pt-BR")}</small></p>`;
  $("#lead-status").addEventListener("change", async (e) => {
    await api(`/api/leads/${id}`, { method: "PATCH", body: { status: e.target.value } });
    toast("Status atualizado"); refresh();
  });
  $("#drawer").classList.remove("hidden");
}
$("#drawer").addEventListener("click", (e) => { if (e.target.hasAttribute("data-close")) $("#drawer").classList.add("hidden"); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer").classList.add("hidden"); });

// ---------- Coleta ----------
const dlg = $("#collect-dialog");
$("#btn-collect").addEventListener("click", () => { $("#collect-status").classList.add("hidden"); dlg.showModal(); });
$("#collect-cancel").addEventListener("click", () => dlg.close());

$("#collect-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const checked = (n) => [...document.querySelectorAll(`input[name=${n}]:checked`)].map((i) => i.value);
  const body = {
    cidades: checked("cidade"), servicos: checked("servico"),
    segmento: $("#c-segmento").value.trim() || null,
    quantidade: Math.min(15, Math.max(1, +$("#c-qtd").value || 5)),
    modo: $("#c-mock").checked ? "mock" : null,
  };
  const status = $("#collect-status"), btn = $("#collect-submit");
  status.classList.remove("hidden"); btn.disabled = true;
  status.innerHTML = `<span class="spinner"></span>${body.modo ? "Gerando leads de demonstração…" : "Agente do Foundry pesquisando empresas… isso pode levar até alguns minutos."}`;
  try {
    const { job_id } = await api("/api/collect", { method: "POST", body });
    let job;
    do { await new Promise((r) => setTimeout(r, 2000)); job = await api(`/api/jobs/${job_id}`); } while (job.status === "executando");
    status.textContent = (job.status === "concluido" ? "✅ " : "❌ ") + job.message;
    await refresh();
    toast(job.status === "concluido" ? "Coleta concluída" : "Falha na coleta");
  } catch (err) { status.textContent = "❌ " + err.message; }
  btn.disabled = false;
});

boot();
