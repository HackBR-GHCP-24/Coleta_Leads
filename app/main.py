from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, mock_data
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
    status: str


class CollectIn(BaseModel):
    cidades: list[str] = Field(default_factory=list)
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
        "cidades": config.CITIES,
        "servicos": config.SERVICES,
        "status": db.STATUSES,
        "modo": "foundry" if config.foundry_enabled() else "mock",
        "agent_id": config.FOUNDRY_AGENT_ID or None,
    }


@app.get("/api/leads")
def leads(cidade: str | None = None, servico: str | None = None, min_score: int = 0, status: str | None = None,
          q: str | None = None, user: str = Depends(current_user)):
    return db.list_leads(cidade, servico, min_score, status, q)


@app.get("/api/leads/{lead_id}")
def lead(lead_id: int, user: str = Depends(current_user)):
    found = db.get_lead(lead_id)
    if not found:
        raise HTTPException(404, "Lead não encontrado")
    return found


@app.patch("/api/leads/{lead_id}")
def update_lead(lead_id: int, body: StatusIn, user: str = Depends(current_user)):
    if body.status not in db.STATUSES:
        raise HTTPException(400, "Status inválido")
    if not db.set_status(lead_id, body.status):
        raise HTTPException(404, "Lead não encontrado")
    return db.get_lead(lead_id)


@app.get("/api/stats")
def stats(user: str = Depends(current_user)):
    return db.stats()


def _run_collect(job_id: str, body: CollectIn) -> None:
    use_foundry = body.modo != "mock" and config.foundry_enabled()
    aviso = ""
    try:
        if use_foundry:
            try:
                from .foundry_client import run_agent

                result = run_agent(body.cidades, body.servicos, body.segmento, body.quantidade)
                origem = "foundry"
            except Exception as exc:  # noqa: BLE001
                log.exception("Falha ao chamar o agente do Foundry")
                if config.APP_MODE == "foundry":
                    raise
                aviso = f"Foundry indisponível ({type(exc).__name__}: {str(exc)[:160]}); usados dados de exemplo. "
                result = mock_data.generate(body.cidades, body.servicos, body.segmento, body.quantidade)
                origem = "demo"
        else:
            result = mock_data.generate(body.cidades, body.servicos, body.segmento, body.quantidade)
            origem = "demo"
        novos, atualizados = db.upsert_leads(result.get("leads", []), origem)
        msg = aviso + f"{novos} novo(s), {atualizados} atualizado(s). {result.get('observacoes') or ''}".strip()
        db.finish_job(job_id, "concluido", origem, msg, novos, atualizados)
    except Exception as exc:  # noqa: BLE001
        db.finish_job(job_id, "erro", None, f"{type(exc).__name__}: {exc}"[:500])


@app.post("/api/collect")
def collect(body: CollectIn, tasks: BackgroundTasks, user: str = Depends(current_user)):
    invalid = [s for s in body.servicos if s not in config.SERVICES]
    if invalid:
        raise HTTPException(400, f"Serviços inválidos: {invalid}")
    job_id = uuid.uuid4().hex
    db.create_job(job_id, body.model_dump())
    tasks.add_task(_run_collect, job_id, body)
    return {"job_id": job_id}


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
