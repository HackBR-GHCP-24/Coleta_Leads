from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, emails, mock_data
from .auth import create_token, current_user

log = logging.getLogger("coleta_leads")
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Coleta de Leads - Prospector de TI")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    if config.SEED_DEMO_DATA and db.count_leads() == 0:
        db.upsert_leads(mock_data.generate(quantity=14, seed=42)["leads"], origem="demo")
    if not config.APP_PASSWORD:
        log.warning("APP_PASSWORD não definido: nenhum usuário foi criado.")


class LoginIn(BaseModel):
    username: str
    password: str


class StatusIn(BaseModel):
    status: str | None = None
    email: str | None = None  # "" remove os e-mails; valor substitui pelos informados manualmente


class EnrichIn(BaseModel):
    lead_ids: list[int] = Field(default_factory=list, max_length=30)  # vazio = leads sem e-mail
    modo: str | None = None


class CollectIn(BaseModel):
    bases: list[str] = Field(default_factory=list)
    cidades: list[str] = Field(default_factory=list)  # cidades específicas (texto livre, opcional)
    max_horas: float = Field(default=config.MAX_TRAVEL_HOURS, gt=0, le=config.MAX_TRAVEL_HOURS)
    servicos: list[str] = Field(default_factory=list)
    segmento: str | None = None
    quantidade: int = Field(default=5, ge=1, le=15)
    modo: str | None = None  # "mock" força dados de exemplo


@app.post("/api/auth/login")
def login(body: LoginIn):
    if not db.check_login(body.username, body.password):
        raise HTTPException(401, "Usuário ou senha inválidos")
    return {"token": create_token(body.username), "username": body.username}


@app.get("/api/me")
def me(user: str = Depends(current_user)):
    return {"username": user}


@app.get("/api/meta")
def meta(user: str = Depends(current_user)):
    return {
        "bases": config.BASES,
        "max_horas": config.MAX_TRAVEL_HOURS,
        "cidades": db.distinct_cities(),
        "servicos": config.SERVICES,
        "status": db.STATUSES,
        "modo": "foundry" if config.foundry_enabled() else "mock",
        "agent_id": config.FOUNDRY_AGENT_ID or None,
    }


@app.get("/api/leads")
def leads(cidade: str | None = None, servico: str | None = None, min_score: int = 0, status: str | None = None,
          q: str | None = None, base: str | None = None, max_horas: float | None = None,
          com_email: bool | None = None, user: str = Depends(current_user)):
    return db.list_leads(cidade, servico, min_score, status, q, base, max_horas, com_email)


@app.get("/api/leads/{lead_id}")
def lead(lead_id: int, user: str = Depends(current_user)):
    found = db.get_lead(lead_id)
    if not found:
        raise HTTPException(404, "Lead não encontrado")
    return found


@app.patch("/api/leads/{lead_id}")
def update_lead(lead_id: int, body: StatusIn, user: str = Depends(current_user)):
    if not db.get_lead(lead_id):
        raise HTTPException(404, "Lead não encontrado")
    if body.status is not None:
        if body.status not in db.STATUSES:
            raise HTTPException(400, "Status inválido")
        db.set_status(lead_id, body.status)
    if body.email is not None:
        values = [e.strip() for e in body.email.replace(";", ",").split(",") if e.strip()]
        bad = [e for e in values if not emails.valid(e)]
        if bad:
            raise HTTPException(400, f"E-mail inválido: {', '.join(bad)}")
        db.set_contacts(lead_id, [{"email": e, "tipo": "manual"} for e in values], replace=True)
    return db.get_lead(lead_id)


@app.get("/api/stats")
def stats(user: str = Depends(current_user)):
    return db.stats()


def _mock(body: CollectIn) -> dict:
    return mock_data.generate(body.bases, body.servicos, body.segmento, body.quantidade,
                              cities=body.cidades, max_hours=body.max_horas)


def _run_collect(job_id: str, body: CollectIn) -> None:
    use_foundry = body.modo != "mock" and config.foundry_enabled()
    aviso = ""
    try:
        if use_foundry:
            try:
                from .foundry_client import run_agent

                result = run_agent(body.bases, body.servicos, body.segmento, body.quantidade,
                                   body.cidades, body.max_horas)
                origem = "foundry"
            except Exception as exc:  # noqa: BLE001
                log.exception("Falha ao chamar o agente do Foundry")
                if config.APP_MODE == "foundry":
                    raise
                aviso = f"Foundry indisponível ({type(exc).__name__}: {str(exc)[:160]}); usados dados de exemplo. "
                result = _mock(body)
                origem = "demo"
        else:
            result = _mock(body)
            origem = "demo"
        novos, atualizados = db.upsert_leads(result.get("leads", []), origem)
        msg = aviso + f"{novos} novo(s), {atualizados} atualizado(s). {result.get('observacoes') or ''}".strip()
        db.finish_job(job_id, "concluido", origem, msg, novos, atualizados)
    except Exception as exc:  # noqa: BLE001
        db.finish_job(job_id, "erro", None, f"{type(exc).__name__}: {exc}"[:500])


@app.post("/api/collect")
def collect(body: CollectIn, tasks: BackgroundTasks, user: str = Depends(current_user)):
    invalid = [s for s in body.servicos if s not in config.SERVICES] + [b for b in body.bases if b not in config.BASES]
    if invalid:
        raise HTTPException(400, f"Serviços/bases inválidos: {invalid}")
    body.cidades = [c.strip()[:60] for c in body.cidades if c.strip()][:10]
    job_id = uuid.uuid4().hex
    db.create_job(job_id, body.model_dump())
    tasks.add_task(_run_collect, job_id, body)
    return {"job_id": job_id}


def _mock_emails(lead: dict) -> dict:
    """Modo demo: só sugere padrão inferido (.example) para leads fictícios; nunca para empresas reais."""
    contacts = []
    if lead.get("origem") == "demo":
        contacts = [{"email": f"contato@{mock_data._slug(lead['empresa'])}.example", "fonte": None, "tipo": "inferido"}]
    return {"contatos_email": contacts, "email_status": "ok" if contacts else "nao_encontrado"}


def _run_enrich(job_id: str, ids: list[int], use_foundry: bool) -> None:
    leads_ = db.leads_for_enrichment(ids or None)
    feitos = achados = 0
    try:
        find = _mock_emails
        if use_foundry:
            from .foundry_client import find_emails as find

        def work(lead_: dict) -> bool:
            try:
                res = find(lead_)
            except Exception:  # noqa: BLE001
                log.exception("Falha ao enriquecer lead %s", lead_["id"])
                return False
            db.set_contacts(lead_["id"], res["contatos_email"], status=res.get("email_status"))
            return bool(res["contatos_email"])

        db.job_progress(job_id, f"Buscando e-mails de {len(leads_)} lead(s)…")
        with ThreadPoolExecutor(max_workers=4) as pool:
            for ok in pool.map(work, leads_):
                feitos += 1
                achados += ok
                db.job_progress(job_id, f"Buscando e-mails… {feitos}/{len(leads_)} lead(s) processados, "
                                        f"{achados} com e-mail.", achados)
        db.finish_job(job_id, "concluido", "foundry" if use_foundry else "demo",
                      f"E-mail encontrado para {achados} de {len(leads_)} lead(s).", 0, achados)
    except Exception as exc:  # noqa: BLE001
        db.finish_job(job_id, "erro", None, f"{type(exc).__name__}: {exc}"[:500])


@app.post("/api/enrich")
def enrich(body: EnrichIn, tasks: BackgroundTasks, user: str = Depends(current_user)):
    """Busca (via agente) os e-mails de contato de leads existentes."""
    use_foundry = body.modo != "mock" and config.foundry_enabled()
    job_id = uuid.uuid4().hex
    db.create_job(job_id, {"tipo": "enriquecer_email", **body.model_dump()})
    tasks.add_task(_run_enrich, job_id, body.lead_ids, use_foundry)
    return {"job_id": job_id, "total": len(body.lead_ids or db.leads_for_enrichment())}


@app.get("/api/jobs/{job_id}")
def job(job_id: str, user: str = Depends(current_user)):
    found = db.get_job(job_id)
    if not found:
        raise HTTPException(404, "Coleta não encontrada")
    return found


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
