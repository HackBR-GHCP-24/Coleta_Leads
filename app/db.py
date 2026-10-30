from __future__ import annotations

import json
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config
from .auth import hash_password, verify_password
from .scoring import final_score, tier

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
        if config.APP_PASSWORD:
            row = c.execute("SELECT password_hash FROM users WHERE username=?", (config.APP_USERNAME,)).fetchone()
            if not row or not verify_password(config.APP_PASSWORD, row["password_hash"]):
                c.execute(
                    "INSERT OR REPLACE INTO users(username, password_hash) VALUES (?, ?)",
                    (config.APP_USERNAME, hash_password(config.APP_PASSWORD)),
                )


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
            score = final_score(lead)
            values = (
                empresa, lead.get("cidade"), lead.get("segmento"), lead.get("porte"),
                int(lead.get("score_oportunidade") or 0), score,
                json.dumps(lead.get("servicos_recomendados") or []),
                json.dumps(lead, ensure_ascii=False), origem, now,
            )
            exists = c.execute("SELECT id FROM leads WHERE key=?", (key,)).fetchone()
            if exists:
                c.execute(
                    """UPDATE leads SET empresa=?, cidade=?, segmento=?, porte=?, score_agente=?, score_final=?,
                       servicos=?, data=?, origem=?, updated_at=? WHERE id=?""",
                    (*values, exists["id"]),
                )
                atualizados += 1
            else:
                c.execute(
                    """INSERT INTO leads(empresa, cidade, segmento, porte, score_agente, score_final, servicos, data,
                       origem, updated_at, key, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (*values, key, now),
                )
                novos += 1
    return novos, atualizados


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
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if full:
        out["detalhes"] = data
    return out


def list_leads(cidade=None, servico=None, min_score=0, status=None, q=None) -> list[dict]:
    sql = "SELECT * FROM leads WHERE score_final >= ?"
    args: list = [min_score]
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
        "por_cidade": por_cidade,
        "por_status": por_status,
        "por_servico": servicos,
        "ultima_coleta": dict(ultima) if ultima else None,
    }


def count_leads() -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]


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


def get_job(job_id: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None
