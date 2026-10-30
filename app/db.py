from __future__ import annotations

import json
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config, emails
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
"""

STATUSES = ["novo", "contatado", "qualificado", "proposta", "descartado"]


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
    for row in c.execute("SELECT id, data FROM leads WHERE email IS NULL").fetchall():
        data = json.loads(row["data"])
        contacts = emails.normalize(data.get("contatos_email"))
        if contacts:
            data["contatos_email"] = contacts
            email, tipo = emails.primary(contacts)
            c.execute("UPDATE leads SET email=?, email_tipo=?, data=? WHERE id=?",
                      (email, tipo, json.dumps(data, ensure_ascii=False), row["id"]))


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
            old_contacts = json.loads(exists["data"]).get("contatos_email") if exists else None
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
            else:
                c.execute(
                    """INSERT INTO leads(empresa, cidade, segmento, porte, score_agente, score_final, servicos, data,
                       origem, updated_at, email, email_tipo, key, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (*values, key, now),
                )
                novos += 1
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
    return True


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
        "origem": row["origem"],
        "nivel_confianca": data.get("nivel_confianca"),
        "necessidades": data.get("necessidades") or [],
        "base_proxima": data.get("base_proxima") or (row["cidade"] if travel_hours(data) == 0 else None),
        "distancia_km": data.get("distancia_km"),
        "tempo_viagem_h": travel_hours(data),
        "email": row["email"],
        "email_tipo": row["email_tipo"],
        "email_status": data.get("email_status"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if full:
        out["detalhes"] = data
    return out


def list_leads(cidade=None, servico=None, min_score=0, status=None, q=None, base=None, max_horas=None,
               com_email=None) -> list[dict]:
    sql = "SELECT * FROM leads WHERE score_final >= ?"
    args: list = [min_score]
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
        sql += " AND (empresa LIKE ? OR segmento LIKE ? OR data LIKE ?)"
        args += [f"%{q}%"] * 3
    sql += " ORDER BY score_final DESC, updated_at DESC"
    with conn() as c:
        rows = c.execute(sql, args).fetchall()
    leads = [_row_to_lead(r) for r in rows]
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
    return _row_to_lead(row, full=True) if row else None


def set_status(lead_id: int, status: str) -> bool:
    with conn() as c:
        cur = c.execute("UPDATE leads SET status=?, updated_at=? WHERE id=?", (status, _now(), lead_id))
    return cur.rowcount > 0


def stats() -> dict:
    with conn() as c:
        total = c.execute("SELECT COUNT(*) n, COALESCE(AVG(score_final),0) avg FROM leads").fetchone()
        quentes = c.execute("SELECT COUNT(*) FROM leads WHERE score_final >= 75").fetchone()[0]
        com_email = c.execute("SELECT COUNT(*) FROM leads WHERE email IS NOT NULL").fetchone()[0]
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
