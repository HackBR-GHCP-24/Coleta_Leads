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
  $("#btn-verify").classList.toggle("hidden", !state.meta.verificacao);
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

const VB = {
  verificado: ["ver", "✓ verificado"], ia: ["ia", "≈ estimado pela IA"], manual: ["man", "✎ manual"],
  nao_encontrado: ["bad", "não encontrado na Receita"],
};
const vbadge = (k, title = "") => (VB[k] ? `<span class="vbadge ${VB[k][0]}" title="${esc(title)}">${VB[k][1]}</span>` : "");
const locBadge = (l) => (l.origem === "demo" ? "" : vbadge(l.origem_dados?.localizacao || "ia", l.origem_dados?.localizacao === "verificado" ? "Rota de carro calculada (OSRM/OpenStreetMap, sem trânsito)" : "Distância estimada pelo agente"));
const cnpjBadge = (l) => (l.cnpj && l.origem_dados?.cadastro === "verificado" ? `<span class="vbadge ver" title="CNPJ ${esc(l.cnpj)} conferido na Receita Federal${l.situacao_cadastral ? ` · ${esc(l.situacao_cadastral)}` : ""}">✓ CNPJ</span>` : "");

function renderLeads(leads) {
  $("#count").textContent = `${leads.length} lead(s)`;
  $("#empty").classList.toggle("hidden", leads.length > 0);
  $("#leads-body").innerHTML = leads.map((l) => `
    <tr data-id="${l.id}">
      <td class="rank">${l.rank}</td>
      <td class="company">${esc(l.empresa)}<small>${l.origem === "foundry" ? "🔎 Foundry" : "🧪 Demo"} · confiança ${esc(l.nivel_confianca || "—")}${cnpjBadge(l)}</small>${l.email ? `<small class="m-email">✉ ${emailLink(l.email)}${copyBtn(l.email)}</small>` : ""}</td>
      <td class="city">${esc(l.cidade)}<small>${esc(locLine(l))}${locBadge(l)}</small></td>
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
      <div class="fact"><span>Localização ${locBadge(l)}</span>${esc(l.cidade)}/${esc(d.uf || "SP")}<br><small class="muted">${esc(locLine(l))}</small>${locIa(d)}</div>
      <div class="fact"><span>CNPJ ${d.origem_dados?.cadastro === "verificado" ? vbadge("verificado", `Conferido na Receita (${d.cnpj_dados?.fonte || ""}); ${d.origem_dados?.cnpj === "manual" ? "informado manualmente" : "encontrado pelo agente"}`) : vbadge(d.origem_dados?.cnpj, d.origem_dados?.cnpj === "ia" ? "CNPJ informado pelo agente, ainda não conferido" : "CNPJ informado manualmente")}</span>${esc(d.cnpj || "—")}${d.cnpj_fonte && safeUrl(d.cnpj_fonte) ? ` <a class="muted" href="${esc(safeUrl(d.cnpj_fonte))}" target="_blank" rel="noopener">fonte</a>` : ""}</div>
      <div class="fact"><span>Contato sugerido</span>${esc(d.contato_sugerido?.cargo || "—")}<br><small class="muted">${esc(d.contato_sugerido?.canal || "")}</small></div>
    </div>
    <h3>Dados cadastrais ${d.origem_dados?.cadastro ? vbadge(d.origem_dados.cadastro) : l.origem === "demo" ? "" : vbadge("ia", "Sem CNPJ verificado: porte e segmento são estimativas da IA")}</h3>
    ${cadastro(d, l)}
    <div class="email-edit">
      <input id="lead-cnpj" type="text" inputmode="numeric" placeholder="CNPJ 00.000.000/0000-00" value="${esc(d.cnpj || "")}" />
      <button type="button" class="btn ghost small" id="lead-cnpj-save">Salvar e verificar</button>
      ${state.meta.verificacao && l.origem !== "demo" ? `<button type="button" class="btn ghost small" id="lead-verify">✓ Reverificar${d.cnpj ? "" : " (busca CNPJ com o agente)"}</button>` : ""}
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
  $("#lead-cnpj-save").addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.innerHTML = `<span class="spinner"></span>Consultando…`;
    try {
      await api(`/api/leads/${id}`, { method: "PATCH", body: { cnpj: $("#lead-cnpj").value } });
      toast("CNPJ salvo"); refresh(); openLead(id);
    } catch (err) { toast("❌ " + err.message); e.target.disabled = false; e.target.textContent = "Salvar e verificar"; }
  });
  $("#lead-verify")?.addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.innerHTML = `<span class="spinner"></span>Verificando…`;
    const job = await runJob("/api/verify", { lead_ids: [+id] });
    toast(job.message); refresh(); openLead(id);
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

function locIa(d) {
  const e = d.estimativa_ia;
  if (!d.rota || !e || e.tempo_viagem_h == null) return "";
  return `<br><small class="muted" title="Estimativa original do agente">IA estimou: ${esc(fmtH(e.tempo_viagem_h))}${e.distancia_km ? ` · ${Math.round(e.distancia_km)} km` : ""}${e.base_proxima ? ` de ${esc(e.base_proxima)}` : ""}</small>`;
}

function cadastro(d, l) {
  const c = d.cnpj_dados;
  if (!c) {
    const msg = d.origem_dados?.cadastro === "nao_encontrado" ? "CNPJ não encontrado na BrasilAPI/ReceitaWS." :
      d.cnpj_invalido ? `O agente informou um CNPJ inválido (${esc(d.cnpj_invalido)}) — descartado.` :
      l.origem === "demo" ? "Lead de demonstração (sem consulta externa)." : "Sem CNPJ verificado. Informe o CNPJ ou peça ao agente para buscá-lo.";
    return `<p class="muted">${msg}</p>`;
  }
  const dt = c.abertura ? new Date(c.abertura + "T00:00").toLocaleDateString("pt-BR") : "—";
  const dif = c.municipio && l.cidade && c.municipio.localeCompare(l.cidade, "pt-BR", { sensitivity: "base" }) !== 0;
  const row = (k, v) => `<dt>${k}</dt><dd>${v || "—"}</dd>`;
  return `<dl class="cad">
    ${row("Razão social", esc(c.razao_social))}${c.nome_fantasia ? row("Nome fantasia", esc(c.nome_fantasia)) : ""}
    ${row("CNPJ", `${esc(c.cnpj)}${c.matriz_filial ? ` · ${esc(c.matriz_filial)}` : ""}`)}
    ${row("Situação", `<b style="color:${/ATIVA/i.test(c.situacao || "") ? "var(--ok)" : "#ff6b6b"}">${esc(c.situacao)}</b>`)}
    ${row("CNAE", `${esc(c.cnae || "")} ${esc(c.cnae_descricao || "")}`)}
    ${row("Porte (Receita)", esc(c.porte))}
    ${row("Abertura", dt)}
    ${row("Endereço", `${esc(c.endereco)} · ${esc(c.municipio)}/${esc(c.uf)}${c.cep ? ` · CEP ${esc(c.cep)}` : ""}${dif ? ` <span class="vbadge ia" title="O CNPJ é de outra unidade (ex.: matriz); a rota usa a cidade do lead">endereço em outra cidade</span>` : ""}`)}
    ${row("Fonte", `${esc(c.fonte)} · ${c.consultado_em ? new Date(c.consultado_em).toLocaleString("pt-BR") : ""}`)}
  </dl>`;
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

async function runJob(path, body, onProgress) {
  const { job_id } = await api(path, { method: "POST", body });
  let job;
  do {
    await new Promise((r) => setTimeout(r, 2000));
    job = await api(`/api/jobs/${job_id}`);
    if (onProgress && job.message) onProgress(job.message);
  } while (job.status === "executando");
  return job;
}
const runEnrich = (ids = []) => runJob("/api/enrich", { lead_ids: ids },
  ids.length ? null : (m) => { $("#enrich-status").innerHTML = `<span class="spinner"></span>${esc(m)}`; });

$("#btn-verify").addEventListener("click", async (e) => {
  const btn = e.currentTarget, st = $("#enrich-status");
  btn.disabled = true; st.classList.remove("hidden");
  st.innerHTML = `<span class="spinner"></span>Verificando CNPJ (Receita) e rotas de carro dos leads…`;
  try {
    const job = await runJob("/api/verify", { lead_ids: [] }, (m) => { st.innerHTML = `<span class="spinner"></span>${esc(m)}`; });
    st.textContent = (job.status === "concluido" ? "✅ " : "❌ ") + job.message;
    refresh();
  } catch (err) { st.textContent = "❌ " + err.message; }
  btn.disabled = false;
});

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
