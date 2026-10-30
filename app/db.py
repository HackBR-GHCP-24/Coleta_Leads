from __future__ import annotations

import json
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config, emails, scoring, sinais as sinais_mod
from .auth import hash_password, verify_password
from .scoring import final_score, tier, travel_hours

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    empresa TEXT NOT NULL,
    cidade TEXT,
    segmento TEXT,
    porte TEXT,
    score_agente INTEGER,
    score_final INTEGER,
    servicos TEXT,
    data TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'novo',
    origem TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    origem TEXT,
    params TEXT,
    message TEXT,
    novos INTEGER DEFAULT 0,
    atualizados INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS sinais (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    tipo TEXT NOT NULL,
    descricao TEXT NOT NULL,
    data TEXT,
    fonte TEXT,
    peso REAL NOT NULL,
    chave TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (lead_id, chave)
);
CREATE INDEX IF NOT EXISTS ix_sinais_lead ON sinais(lead_id);
CREATE TABLE IF NOT EXISTS notas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    tipo TEXT NOT NULL DEFAULT 'nota',
    texto TEXT NOT NULL,
    autor TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_notas_lead ON notas(lead_id);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_cache (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
"""

# Campos de verificação (CNPJ/rota) preservados quando o agente atualiza um lead existente
VERIFIED_KEYS = ("cnpj", "cnpj_fonte", "cnpj_dados", "rota", "origem_dados", "verificado_em", "uf")

# Pipeline do mini CRM
STATUSES = ["novo", "contatado", "reuniao", "proposta", "ganho", "perdido"]
STATUS_LABELS = {"novo": "Novo", "contatado": "Contatado", "reuniao": "Reunião", "proposta": "Proposta",
                 "ganho": "Ganho", "perdido": "Perdido"}
_STATUS_MIGRA = {"qualificado": "reuniao", "descartado": "perdido"}


def _today() -> str:
    return datetime.now().date().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conn():
    config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.DATABASE_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        _migrate(c)
        _load_weights(c)
        _recompute(c)  # o decaimento dos sinais muda com o tempo
        if config.APP_PASSWORD:
            row = c.execute("SELECT password_hash FROM users WHERE username=?", (config.APP_USERNAME,)).fetchone()
            if not row or not verify_password(config.APP_PASSWORD, row["password_hash"]):
                c.execute(
                    "INSERT OR REPLACE INTO users(username, password_hash) VALUES (?, ?)",
                    (config.APP_USERNAME, hash_password(config.APP_PASSWORD)),
                )


def _migrate(c: sqlite3.Connection) -> None:
    """Migrações idempotentes: adiciona colunas novas sem perder os leads existentes."""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(leads)")}
    if "email" not in cols:
        c.execute("ALTER TABLE leads ADD COLUMN email TEXT")
    if "email_tipo" not in cols:
        c.execute("ALTER TABLE leads ADD COLUMN email_tipo TEXT")
    for col in ("responsavel", "proxima_acao", "proxima_acao_data"):
        if col not in cols:
            c.execute(f"ALTER TABLE leads ADD COLUMN {col} TEXT")
    for antigo, novo in _STATUS_MIGRA.items():  # etapas antigas → pipeline novo
        c.execute("UPDATE leads SET status=? WHERE status=?", (novo, antigo))
    for row in c.execute("SELECT id, data FROM leads WHERE email IS NULL").fetchall():
        data = json.loads(row["data"])
        contacts = emails.normalize(data.get("contatos_email"))
        if contacts:
            data["contatos_email"] = contacts
            email, tipo = emails.primary(contacts)
            c.execute("UPDATE leads SET email=?, email_tipo=?, data=? WHERE id=?",
                      (email, tipo, json.dumps(data, ensure_ascii=False), row["id"]))
    # sinais de compra: migra os sinais guardados no JSON para a tabela própria
    for row in c.execute("SELECT id, data FROM leads WHERE id NOT IN (SELECT DISTINCT lead_id FROM sinais)").fetchall():
        _save_signals(c, row["id"], json.loads(row["data"]).get("sinais"))


def _signal_key(descricao: str) -> str:
    return _key(descricao[:120], None)


def _save_signals(c: sqlite3.Connection, lead_id: int, items) -> int:
    n = 0
    for s in sinais_mod.normalize(items):
        cur = c.execute(
            """INSERT INTO sinais(lead_id, tipo, descricao, data, fonte, peso, chave, created_at) VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(lead_id, chave) DO UPDATE SET
                 data=COALESCE(excluded.data, sinais.data), fonte=COALESCE(excluded.fonte, sinais.fonte),
                 tipo=CASE WHEN excluded.tipo != 'inferencia' THEN excluded.tipo ELSE sinais.tipo END,
                 peso=CASE WHEN excluded.tipo != 'inferencia' THEN excluded.peso ELSE sinais.peso END""",
            (lead_id, s["tipo"], s["descricao"], s["data"], s["fonte"], s["peso"], _signal_key(s["descricao"]), _now()),
        )
        n += cur.rowcount
    return n


def _signals(c: sqlite3.Connection, lead_id: int) -> list[dict]:
    rows = c.execute("SELECT id, tipo, descricao, data, fonte, peso FROM sinais WHERE lead_id=?", (lead_id,)).fetchall()
    return [dict(r) for r in rows]


def _rescore(c: sqlite3.Connection, lead_id: int, data: dict) -> int:
    score = final_score(data, _signals(c, lead_id))
    c.execute("UPDATE leads SET score_final=? WHERE id=?", (score, lead_id))
    return score


def _recompute(c: sqlite3.Connection) -> None:
    for row in c.execute("SELECT id, data FROM leads").fetchall():
        _rescore(c, row["id"], json.loads(row["data"]))


def _load_weights(c: sqlite3.Connection) -> None:
    row = c.execute("SELECT value FROM settings WHERE key='pesos'").fetchone()
    try:
        scoring.set_weights(json.loads(row["value"]) if row else {})
    except (ValueError, TypeError):
        scoring.set_weights({})


def save_weights(weights: dict) -> int:
    """Valida, persiste os pesos e recalcula o score de todos os leads (sem nova coleta). Devolve quantos mudaram."""
    valid = scoring.validate_weights(weights)
    with conn() as c:
        antes = dict(c.execute("SELECT id, score_final FROM leads").fetchall())
        c.execute("INSERT OR REPLACE INTO settings(key, value, updated_at) VALUES ('pesos', ?, ?)",
                  (json.dumps(valid), _now()))
        scoring.set_weights(valid)
        _recompute(c)
        depois = dict(c.execute("SELECT id, score_final FROM leads").fetchall())
    return sum(1 for k, v in depois.items() if antes.get(k) != v)


def add_signals(lead_id: int, items) -> int:
    """Acrescenta sinais (ex.: busca dedicada do agente) e recalcula o score. Devolve quantos foram gravados."""
    with conn() as c:
        row = c.execute("SELECT data FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not row:
            return 0
        n = _save_signals(c, lead_id, items)
        _rescore(c, lead_id, json.loads(row["data"]))
        c.execute("UPDATE leads SET updated_at=? WHERE id=?", (_now(), lead_id))
    return n


def check_login(username: str, password: str) -> bool:
    with conn() as c:
        row = c.execute("SELECT password_hash FROM users WHERE username=?", (username,)).fetchone()
    return bool(row) and verify_password(password, row["password_hash"])


def _key(empresa: str, cidade: str | None) -> str:
    raw = f"{empresa}|{cidade or ''}".lower()
    raw = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    return "".join(ch for ch in raw if ch.isalnum() or ch == "|")


def upsert_leads(leads: list[dict], origem: str) -> tuple[int, int]:
    novos = atualizados = 0
    now = _now()
    with conn() as c:
        for lead in leads:
            empresa = (lead.get("empresa") or "").strip()
            if not empresa:
                continue
            key = _key(empresa, lead.get("cidade"))
            exists = c.execute("SELECT id, data FROM leads WHERE key=?", (key,)).fetchone()
            old = json.loads(exists["data"]) if exists else {}
            old_contacts = old.get("contatos_email")
            if (old.get("origem_dados") or {}).get("cnpj") == "manual" or (old.get("cnpj") and not lead.get("cnpj")):
                for k in VERIFIED_KEYS:
                    if k in old and (k != "uf" or not lead.get("uf")):
                        lead[k] = old[k]
                rota = old.get("rota")
                if rota:  # mantém a rota verificada; o novo valor da IA fica como estimativa
                    lead["estimativa_ia"] = {k: lead.get(k) for k in ("base_proxima", "distancia_km", "tempo_viagem_h")}
                    lead["base_proxima"], lead["distancia_km"], lead["tempo_viagem_h"] = rota["base"], rota["distancia_km"], rota["tempo_h"]
            # mantém e-mails já conhecidos (inclusive os informados manualmente) ao atualizar o lead
            contacts = emails.normalize(list(emails.normalize(lead.get("contatos_email"))) + list(old_contacts or []))
            lead["contatos_email"] = contacts
            if not contacts and not lead.get("email_status"):
                lead["email_status"] = "nao_encontrado"
            email, email_tipo = emails.primary(contacts)
            score = final_score(lead)
            values = (
                empresa, lead.get("cidade"), lead.get("segmento"), lead.get("porte"),
                int(lead.get("score_oportunidade") or 0), score,
                json.dumps(lead.get("servicos_recomendados") or []),
                json.dumps(lead, ensure_ascii=False), origem, now, email, email_tipo,
            )
            if exists:
                c.execute(
                    """UPDATE leads SET empresa=?, cidade=?, segmento=?, porte=?, score_agente=?, score_final=?,
                       servicos=?, data=?, origem=?, updated_at=?, email=?, email_tipo=? WHERE id=?""",
                    (*values, exists["id"]),
                )
                atualizados += 1
                lead_id = exists["id"]
            else:
                lead_id = c.execute(
                    """INSERT INTO leads(empresa, cidade, segmento, porte, score_agente, score_final, servicos, data,
                       origem, updated_at, email, email_tipo, key, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (*values, key, now),
                ).lastrowid
                novos += 1
            _save_signals(c, lead_id, lead.get("sinais"))
            _rescore(c, lead_id, lead)
    return novos, atualizados


def set_contacts(lead_id: int, contacts: list[dict], replace: bool = False, status: str | None = None) -> bool:
    """Grava e-mails de um lead. replace=True substitui a lista (edição manual); senão mescla."""
    with conn() as c:
        row = c.execute("SELECT data FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not row:
            return False
        data = json.loads(row["data"])
        merged = emails.normalize(contacts if replace else list(contacts) + list(data.get("contatos_email") or []))
        data["contatos_email"] = merged
        data["email_status"] = "ok" if merged else (status if status and status != "ok" else "nao_encontrado")
        email, tipo = emails.primary(merged)
        c.execute("UPDATE leads SET data=?, email=?, email_tipo=?, updated_at=? WHERE id=?",
                  (json.dumps(data, ensure_ascii=False), email, tipo, _now(), lead_id))
        _rescore(c, lead_id, data)  # e-mail disponível é um fator do score
    return True


def save_lead_data(lead_id: int, data: dict) -> bool:
    """Regrava o JSON do lead (ex.: após verificação) e recalcula o score."""
    with conn() as c:
        cur = c.execute("UPDATE leads SET data=?, updated_at=? WHERE id=?",
                        (json.dumps(data, ensure_ascii=False), _now(), lead_id))
        if cur.rowcount:
            _rescore(c, lead_id, data)
    return cur.rowcount > 0


def leads_for_verification(ids: list[int] | None = None, only_pending: bool = True) -> list[dict]:
    with conn() as c:
        if ids:
            rows = c.execute(f"SELECT * FROM leads WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
        else:
            rows = c.execute("SELECT * FROM leads WHERE origem != 'demo' ORDER BY score_final DESC").fetchall()
    leads = [_row_to_lead(r, full=True) for r in rows]
    if only_pending and not ids:
        leads = [l for l in leads if not l["detalhes"].get("verificado_em")]
    return leads


def leads_for_signals(ids: list[int] | None = None) -> list[dict]:
    with conn() as c:
        if ids:
            rows = c.execute(f"SELECT * FROM leads WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
        else:
            rows = c.execute("""SELECT * FROM leads WHERE origem != 'demo' AND id NOT IN
                                (SELECT lead_id FROM sinais WHERE data IS NOT NULL AND fonte IS NOT NULL)
                                ORDER BY score_final DESC""").fetchall()
    return [_row_to_lead(r, full=True) for r in rows]


def cache_get(kind: str, key: str):
    with conn() as c:
        row = c.execute("SELECT value FROM api_cache WHERE kind=? AND key=?", (kind, key)).fetchone()
    return json.loads(row["value"]) if row else None


def cache_set(kind: str, key: str, value) -> None:
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO api_cache(kind, key, value, created_at) VALUES (?,?,?,?)",
                  (kind, key, json.dumps(value, ensure_ascii=False), _now()))


def leads_for_enrichment(ids: list[int] | None = None) -> list[dict]:
    with conn() as c:
        if ids:
            rows = c.execute(f"SELECT * FROM leads WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
        else:
            rows = c.execute("SELECT * FROM leads WHERE email IS NULL ORDER BY score_final DESC").fetchall()
    return [_row_to_lead(r, full=True) for r in rows]


def _row_to_lead(row: sqlite3.Row, full: bool = False) -> dict:
    data = json.loads(row["data"])
    out = {
        "id": row["id"],
        "empresa": row["empresa"],
        "cidade": row["cidade"],
        "segmento": row["segmento"],
        "porte": row["porte"],
        "score_agente": row["score_agente"],
        "score_final": row["score_final"],
        "tier": tier(row["score_final"]),
        "servicos": json.loads(row["servicos"] or "[]"),
        "status": row["status"],
        "responsavel": row["responsavel"],
        "proxima_acao": row["proxima_acao"],
        "proxima_acao_data": row["proxima_acao_data"],
        "acao_vencida": bool(row["proxima_acao_data"] and row["proxima_acao_data"] < _today()
                             and row["status"] not in ("ganho", "perdido")),
        "origem": row["origem"],
        "nivel_confianca": data.get("nivel_confianca"),
        "necessidades": data.get("necessidades") or [],
        "base_proxima": data.get("base_proxima") or (row["cidade"] if travel_hours(data) == 0 else None),
        "distancia_km": data.get("distancia_km"),
        "tempo_viagem_h": travel_hours(data),
        "email": row["email"],
        "email_tipo": row["email_tipo"],
        "email_status": data.get("email_status"),
        "cnpj": data.get("cnpj"),
        "origem_dados": data.get("origem_dados") or {},
        "situacao_cadastral": (data.get("cnpj_dados") or {}).get("situacao"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if full:
        out["detalhes"] = data
    return out


def list_leads(cidade=None, servico=None, min_score=0, status=None, q=None, base=None, max_horas=None,
               com_email=None, responsavel=None, acao=None) -> list[dict]:
    sql = "SELECT * FROM leads WHERE score_final >= ?"
    args: list = [min_score]
    if responsavel:
        sql += " AND responsavel = ?"
        args.append(responsavel)
    if acao == "vencida":
        sql += " AND proxima_acao_data < ? AND status NOT IN ('ganho', 'perdido')"
        args.append(_today())
    elif acao == "agendada":
        sql += " AND proxima_acao_data IS NOT NULL AND status NOT IN ('ganho', 'perdido')"
    if com_email is True:
        sql += " AND email IS NOT NULL"
    elif com_email is False:
        sql += " AND email IS NULL"
    if cidade:
        sql += " AND cidade = ?"
        args.append(cidade)
    if status:
        sql += " AND status = ?"
        args.append(status)
    if servico:
        sql += " AND servicos LIKE ?"
        args.append(f'%"{servico}"%')
    if q:
        sql += " AND (empresa LIKE ? OR segmento LIKE ? OR data LIKE ? OR responsavel LIKE ?)"
        args += [f"%{q}%"] * 4
    sql += " ORDER BY score_final DESC, updated_at DESC"
    with conn() as c:
        rows = c.execute(sql, args).fetchall()
        resumo = {r[0]: (r[1], r[2]) for r in c.execute(
            "SELECT lead_id, MAX(data), SUM(data IS NOT NULL AND fonte IS NOT NULL) FROM sinais GROUP BY lead_id")}
    leads = [_row_to_lead(r) for r in rows]
    for l in leads:
        l["ultimo_sinal"], l["sinais_datados"] = resumo.get(l["id"], (None, 0))
    if base:
        leads = [l for l in leads if l["base_proxima"] == base]
    if max_horas is not None:
        leads = [l for l in leads if l["tempo_viagem_h"] is not None and l["tempo_viagem_h"] <= max_horas]
    for i, lead in enumerate(leads, 1):
        lead["rank"] = i
    return leads


def get_lead(lead_id: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        sinais = _signals(c, lead_id) if row else []
        notas = [dict(r) for r in c.execute(
            "SELECT id, tipo, texto, autor, created_at FROM notas WHERE lead_id=? ORDER BY id DESC", (lead_id,))]
    if not row:
        return None
    out = _row_to_lead(row, full=True)
    out["sinais"] = sinais_mod.enrich(sinais)
    out["forca_sinais"] = round(sinais_mod.strength(sinais), 3)
    out["score_detalhe"] = scoring.breakdown(out["detalhes"], sinais)
    out["notas"] = notas
    return out


def _log(c: sqlite3.Connection, lead_id: int, tipo: str, texto: str, autor: str | None) -> None:
    c.execute("INSERT INTO notas(lead_id, tipo, texto, autor, created_at) VALUES (?,?,?,?,?)",
              (lead_id, tipo, texto, autor, _now()))


def set_status(lead_id: int, status: str, autor: str | None = None) -> bool:
    with conn() as c:
        row = c.execute("SELECT status FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not row:
            return False
        if row["status"] != status:
            c.execute("UPDATE leads SET status=?, updated_at=? WHERE id=?", (status, _now(), lead_id))
            _log(c, lead_id, "etapa", f"{STATUS_LABELS.get(row['status'], row['status'])} → {STATUS_LABELS[status]}", autor)
    return True


def set_crm(lead_id: int, fields: dict, autor: str | None = None) -> None:
    """Atualiza responsável e próxima ação (None = não altera; "" = limpa) e registra no histórico."""
    with conn() as c:
        row = c.execute("SELECT responsavel, proxima_acao, proxima_acao_data FROM leads WHERE id=?", (lead_id,)).fetchone()
        for col, valor in fields.items():
            if valor is None:
                continue
            valor = valor.strip() or None
            if valor == row[col]:
                continue
            c.execute(f"UPDATE leads SET {col}=?, updated_at=? WHERE id=?", (valor, _now(), lead_id))
            if col == "responsavel":
                _log(c, lead_id, "responsavel", f"Responsável: {valor or '— (removido)'}", autor)
            elif col == "proxima_acao":
                _log(c, lead_id, "acao", f"Próxima ação: {valor or '— (concluída/removida)'}", autor)
            else:
                data = datetime.fromisoformat(valor).strftime("%d/%m/%Y") if valor else "sem data"
                _log(c, lead_id, "acao", f"Data da próxima ação: {data}", autor)


def add_note(lead_id: int, texto: str, autor: str | None) -> dict:
    with conn() as c:
        _log(c, lead_id, "nota", texto, autor)
        c.execute("UPDATE leads SET updated_at=? WHERE id=?", (_now(), lead_id))
        return dict(c.execute("SELECT id, tipo, texto, autor, created_at FROM notas WHERE id=last_insert_rowid()").fetchone())


def responsaveis() -> list[str]:
    with conn() as c:
        return [r[0] for r in c.execute("SELECT DISTINCT responsavel FROM leads WHERE responsavel IS NOT NULL ORDER BY 1")]


def stats() -> dict:
    with conn() as c:
        total = c.execute("SELECT COUNT(*) n, COALESCE(AVG(score_final),0) avg FROM leads").fetchone()
        quentes = c.execute("SELECT COUNT(*) FROM leads WHERE score_final >= 75").fetchone()[0]
        com_email = c.execute("SELECT COUNT(*) FROM leads WHERE email IS NOT NULL").fetchone()[0]
        vencidas = c.execute("SELECT COUNT(*) FROM leads WHERE proxima_acao_data < ? AND status NOT IN ('ganho','perdido')",
                             (_today(),)).fetchone()[0]
        agendadas = c.execute("SELECT COUNT(*) FROM leads WHERE proxima_acao_data IS NOT NULL AND status NOT IN ('ganho','perdido')").fetchone()[0]
        por_cidade = {r[0]: r[1] for r in c.execute("SELECT cidade, COUNT(*) FROM leads GROUP BY cidade")}
        por_status = {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM leads GROUP BY status")}
        servicos: dict[str, int] = {}
        for (s,) in c.execute("SELECT servicos FROM leads"):
            for k in json.loads(s or "[]"):
                servicos[k] = servicos.get(k, 0) + 1
        ultima = c.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 1").fetchone()
    return {
        "total": total["n"],
        "score_medio": round(total["avg"]),
        "quentes": quentes,
        "com_email": com_email,
        "acoes_vencidas": vencidas,
        "acoes_agendadas": agendadas,
        "por_cidade": por_cidade,
        "por_status": por_status,
        "por_servico": servicos,
        "ultima_coleta": dict(ultima) if ultima else None,
    }


def count_leads() -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]


def distinct_cities() -> list[str]:
    with conn() as c:
        return [r[0] for r in c.execute("SELECT DISTINCT cidade FROM leads WHERE cidade IS NOT NULL ORDER BY cidade")]


def create_job(job_id: str, params: dict) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO jobs(id, status, params, created_at) VALUES (?, 'executando', ?, ?)",
            (job_id, json.dumps(params, ensure_ascii=False), _now()),
        )


def finish_job(job_id: str, status: str, origem: str | None, message: str, novos=0, atualizados=0) -> None:
    with conn() as c:
        c.execute(
            "UPDATE jobs SET status=?, origem=?, message=?, novos=?, atualizados=?, finished_at=? WHERE id=?",
            (status, origem, message, novos, atualizados, _now(), job_id),
        )


def job_progress(job_id: str, message: str, atualizados: int = 0) -> None:
    with conn() as c:
        c.execute("UPDATE jobs SET message=?, atualizados=? WHERE id=?", (message, atualizados, job_id))


def get_job(job_id: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None
