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
  const opts = (sel, items, first) => { const cur = $(sel).value; $(sel).innerHTML = `<option value="">${first}</option>` + items.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join(""); $(sel).value = cur; };
  opts("#f-cidade", m.cidades.map((c) => [c, c]), "Todas as cidades");
  opts("#f-base", m.bases.map((c) => [c, `Base ${c}`]), "Todas as bases");
  opts("#f-servico", Object.entries(m.servicos), "Todos os serviços");
  opts("#f-status", m.status.map((s) => [s, s[0].toUpperCase() + s.slice(1)]), "Todos os status");
  const chips = (sel, items, name, checked) => { $(sel).innerHTML = items.map(([v, l]) => `<label class="chip"><input type="checkbox" name="${name}" value="${esc(v)}" ${checked ? "checked" : ""}/>${esc(l)}</label>`).join(""); };
  chips("#c-bases", m.bases.map((c) => [c, c]), "base", true);
  chips("#c-servicos", Object.entries(m.servicos), "servico", false);
  $("#c-horas").max = m.max_horas; $("#c-horas").value = m.max_horas; showHoras();
}

async function reloadCities() {
  state.meta = await api("/api/meta");
  const cur = $("#f-cidade").value;
  $("#f-cidade").innerHTML = `<option value="">Todas as cidades</option>` + state.meta.cidades.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join("");
  $("#f-cidade").value = cur;
}

const fmtH = (h) => { if (h == null) return ""; if (h === 0) return "na base"; const t = Math.round(h * 60); return t < 60 ? `${t} min` : `${Math.floor(t / 60)}h${String(t % 60).padStart(2, "0")}`; };
const locLine = (l) => (l.tempo_viagem_h == null ? "distância não informada" : l.tempo_viagem_h === 0 ? `base ${l.base_proxima || ""}` : `${fmtH(l.tempo_viagem_h)}${l.distancia_km ? ` · ${Math.round(l.distancia_km)} km` : ""}${l.base_proxima ? ` de ${l.base_proxima}` : ""}`);
function showHoras() { $("#c-horas-val").textContent = `${String($("#c-horas").value).replace(".", ",")} h`; }
$("#c-horas").addEventListener("input", showHoras);

["#f-cidade", "#f-base", "#f-horas", "#f-servico", "#f-status", "#f-email"].forEach((s) => $(s).addEventListener("change", refresh));
$("#f-score").addEventListener("input", (e) => { $("#f-score-val").textContent = e.target.value; debounce(); });
$("#f-q").addEventListener("input", () => debounce());
function debounce() { clearTimeout(state.timer); state.timer = setTimeout(refresh, 250); }

async function refresh() {
  const p = new URLSearchParams();
  const add = (k, v) => v && p.set(k, v);
  add("cidade", $("#f-cidade").value); add("servico", $("#f-servico").value); add("status", $("#f-status").value);
  add("base", $("#f-base").value); add("max_horas", $("#f-horas").value); add("com_email", $("#f-email").value);
  add("q", $("#f-q").value.trim()); add("min_score", $("#f-score").value);
  const [leads, stats] = await Promise.all([api(`/api/leads?${p}`), api("/api/stats")]);
  renderKpis(stats); renderLeads(leads);
}

function renderKpis(s) {
  const top = Object.entries(s.por_servico).sort((a, b) => b[1] - a[1])[0];
  const last = s.ultima_coleta;
  const kpi = (label, value, sub = "") => `<div class="kpi"><div class="label">${label}</div><div class="value${String(value).length > 8 ? " small" : ""}">${value}</div><div class="sub">${sub}</div></div>`;
  const cities = Object.entries(s.por_cidade).sort((a, b) => b[1] - a[1]);
  $("#kpis").innerHTML = [
    kpi("Leads coletados", s.total, `${cities.length} cidade(s) · ` + cities.slice(0, 3).map(([c, n]) => `${esc(c)}: ${n}`).join(" · ")),
    kpi("Oportunidades quentes", s.quentes, "score ≥ 75"),
    kpi("Leads com e-mail", `${s.com_email}/${s.total}`, s.total ? `${Math.round((100 * s.com_email) / s.total)}% com contato` : ""),
    kpi("Score médio", s.score_medio, "ranking ponderado"),
    kpi("Serviço mais demandado", top ? esc(state.meta.servicos[top[0]] || top[0]).split(" (")[0] : "—", top ? `${top[1]} lead(s)` : ""),
    kpi("Última coleta", last ? new Date(last.created_at).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—", last ? `${esc(last.status)} · ${esc(last.origem || "")}` : "nenhuma coleta ainda"),
  ].join("");
}

const svcLabel = (k) => (state.meta.servicos[k] || k).split(" (")[0];

const EMAIL_TIPO = {
  encontrado: ["ok", "encontrado na fonte", "Publicado na página citada"],
  manual: ["manual", "manual", "Informado manualmente"],
  inferido: ["warn", "inferido", "Padrão provável, não confirmado em fonte — valide antes de usar"],
};
const emailBadge = (tipo) => { const t = EMAIL_TIPO[tipo]; return t ? `<span class="ebadge ${t[0]}" title="${t[2]}">${t[1]}</span>` : ""; };
const emailLink = (email) => `<a class="mail" href="mailto:${encodeURIComponent(email).replace("%40", "@")}">${esc(email)}</a>`;
const copyBtn = (email) => `<button type="button" class="icon-btn copy" data-copy="${esc(email)}" title="Copiar e-mail" aria-label="Copiar e-mail">⧉</button>`;
function emailCell(l) {
  if (l.email) return `<div class="email-cell">${emailLink(l.email)}${copyBtn(l.email)}</div>${emailBadge(l.email_tipo)}`;
  return `<span class="muted">${l.email_status === "nao_encontrado" ? "não encontrado" : "—"}</span>`;
}

document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-copy]");
  if (!b) return;
  e.stopPropagation();
  try { await navigator.clipboard.writeText(b.dataset.copy); toast(`Copiado: ${b.dataset.copy}`); }
  catch { toast("Não foi possível copiar"); }
}, true);

function renderLeads(leads) {
  $("#count").textContent = `${leads.length} lead(s)`;
  $("#empty").classList.toggle("hidden", leads.length > 0);
  $("#leads-body").innerHTML = leads.map((l) => `
    <tr data-id="${l.id}">
      <td class="rank">${l.rank}</td>
      <td class="company">${esc(l.empresa)}<small>${l.origem === "foundry" ? "🔎 Foundry" : "🧪 Demo"} · confiança ${esc(l.nivel_confianca || "—")}</small>${l.email ? `<small class="m-email">✉ ${emailLink(l.email)}${copyBtn(l.email)}</small>` : ""}</td>
      <td class="city">${esc(l.cidade)}<small>${esc(locLine(l))}</small></td>
      <td class="email hide-sm">${emailCell(l)}</td>
      <td class="hide-sm">${esc(l.segmento)}</td>
      <td class="hide-sm" style="text-transform:capitalize">${esc(l.porte)}</td>
      <td class="hide-md"><div class="tags">${l.servicos.slice(0, 3).map((s) => `<span class="tag">${esc(svcLabel(s))}</span>`).join("")}${l.servicos.length > 3 ? `<span class="tag">+${l.servicos.length - 3}</span>` : ""}</div></td>
      <td><div class="score"><div class="bar"><i class="tier-${l.tier}" style="width:${l.score_final}%"></i></div><b>${l.score_final}</b></div></td>
      <td class="hide-sm"><span class="pill">${esc(l.status)}</span></td>
    </tr>`).join("");
}

$("#leads-body").addEventListener("click", (e) => {
  if (e.target.closest("a, button")) return;
  const tr = e.target.closest("tr[data-id]"); if (tr) openLead(tr.dataset.id);
});

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
      <div class="fact"><span>Localização</span>${esc(l.cidade)}/${esc(d.uf || "SP")}<br><small class="muted">${esc(locLine(l))}</small></div>
      <div class="fact"><span>CNPJ</span>${esc(d.cnpj || "—")}</div>
      <div class="fact"><span>Contato sugerido</span>${esc(d.contato_sugerido?.cargo || "—")}<br><small class="muted">${esc(d.contato_sugerido?.canal || "")}</small></div>
    </div>
    <h3>E-mails de contato</h3>
    ${emailsSection(d.contatos_email, l.email_status)}
    <div class="email-edit">
      <input id="lead-email" type="text" inputmode="email" placeholder="contato@empresa.com.br (separe vários por vírgula)" value="${esc((d.contatos_email || []).map((c) => c.email).join(", "))}" />
      <button type="button" class="btn ghost small" id="lead-email-save">Salvar</button>
      <button type="button" class="btn ghost small" id="lead-email-agent">🔎 Buscar com o agente</button>
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
  $("#lead-email-save").addEventListener("click", async () => {
    try {
      await api(`/api/leads/${id}`, { method: "PATCH", body: { email: $("#lead-email").value } });
      toast("E-mail salvo"); refresh(); openLead(id);
    } catch (err) { toast("❌ " + err.message); }
  });
  $("#lead-email-agent").addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.innerHTML = `<span class="spinner"></span>Pesquisando…`;
    const job = await runEnrich([+id]);
    toast(job.message); refresh(); openLead(id);
  });
  $("#drawer").classList.remove("hidden");
}

function emailsSection(contacts, status) {
  if (!contacts || !contacts.length) {
    return `<p class="muted">${status === "nao_encontrado" ? "Nenhum e-mail público encontrado pelo agente." : "Ainda sem e-mail."} Informe manualmente ou peça ao agente.</p>`;
  }
  return `<ul class="email-list">${contacts.map((c) => {
    const u = safeUrl(c.fonte);
    return `<li>${emailLink(c.email)}${copyBtn(c.email)}${emailBadge(c.tipo)}${u ? `<a class="muted src" href="${esc(u)}" target="_blank" rel="noopener" title="${esc(u)}">fonte</a>` : ""}</li>`;
  }).join("")}</ul>`;
}

async function runEnrich(ids = []) {
  const { job_id } = await api("/api/enrich", { method: "POST", body: { lead_ids: ids } });
  let job;
  do {
    await new Promise((r) => setTimeout(r, 2000));
    job = await api(`/api/jobs/${job_id}`);
    if (!ids.length && job.message) $("#enrich-status").innerHTML = `<span class="spinner"></span>${esc(job.message)}`;
  } while (job.status === "executando");
  return job;
}

$("#btn-enrich").addEventListener("click", async (e) => {
  const btn = e.currentTarget, st = $("#enrich-status");
  btn.disabled = true; st.classList.remove("hidden");
  st.innerHTML = `<span class="spinner"></span>Agente buscando e-mails dos leads sem contato…`;
  try {
    const job = await runEnrich();
    st.textContent = (job.status === "concluido" ? "✅ " : "❌ ") + job.message;
    refresh();
  } catch (err) { st.textContent = "❌ " + err.message; }
  btn.disabled = false;
});
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
    bases: checked("base"), servicos: checked("servico"),
    cidades: $("#c-cidades").value.split(",").map((c) => c.trim()).filter(Boolean),
    max_horas: +$("#c-horas").value,
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
    await reloadCities();
    await refresh();
    toast(job.status === "concluido" ? "Coleta concluída" : "Falha na coleta");
  } catch (err) { status.textContent = "❌ " + err.message; }
  btn.disabled = false;
});

boot();
