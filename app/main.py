from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, emails, mock_data, sinais as sinais_mod, verify
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
    cnpj: str | None = None  # "" remove o CNPJ; valor informado manualmente é verificado na BrasilAPI/ReceitaWS


class VerifyIn(BaseModel):
    lead_ids: list[int] = Field(default_factory=list, max_length=100)  # vazio = leads reais ainda não verificados
    todos: bool = False  # True = reverifica todos os leads reais
    buscar_cnpj: bool = True  # pede ao agente o CNPJ dos leads que ainda não têm (modo Foundry)


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
        "verificacao": verify.enabled(),
        "tipos_sinal": {k: v[0] for k, v in sinais_mod.TIPOS.items()},
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
    if body.cnpj is not None:
        if body.cnpj.strip() and not verify.clean_cnpj(body.cnpj):
            raise HTTPException(400, "CNPJ inválido (confira os 14 dígitos)")
        data = db.get_lead(lead_id)["detalhes"]
        if verify.enabled():
            data = verify.verify_lead(data, cnpj_manual=body.cnpj.strip())
        else:
            cnpj = verify.clean_cnpj(body.cnpj)
            data["cnpj"] = verify.fmt_cnpj(cnpj) if cnpj else None
            data["origem_dados"] = {**(data.get("origem_dados") or {}), "cnpj": "manual" if cnpj else None}
        db.save_lead_data(lead_id, data)
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
        if origem == "foundry" and verify.enabled():
            db.job_progress(job_id, msg + " Verificando CNPJ e rotas…", atualizados)
            ok, total = _verify_leads(db.leads_for_verification(), buscar_cnpj=True)
            msg += f" Verificados: {ok}/{total} lead(s) (CNPJ e/ou rota)."
        if origem == "foundry":
            pend = db.leads_for_signals()
            db.job_progress(job_id, msg + f" Buscando sinais de compra com fonte de {len(pend)} lead(s)…", atualizados)
            from .foundry_client import find_signals

            def work(lead_: dict) -> int:
                try:
                    return db.add_signals(lead_["id"], find_signals(lead_))
                except Exception:  # noqa: BLE001
                    log.exception("Falha ao buscar sinais do lead %s", lead_["id"])
                    return 0

            with ThreadPoolExecutor(max_workers=4) as pool:
                n = sum(pool.map(work, pend))
            msg += f" Sinais com fonte: {n} novo(s)."
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


def _agent_cnpj(lead_: dict) -> dict:
    try:
        from .foundry_client import find_cnpj

        return find_cnpj(lead_)
    except Exception:  # noqa: BLE001
        log.exception("Falha ao buscar CNPJ do lead %s", lead_["id"])
        return {}


def _verify_leads(leads_: list[dict], job_id: str | None = None, buscar_cnpj: bool = False) -> tuple[int, int]:
    ok = 0
    if buscar_cnpj and config.foundry_enabled():
        sem_cnpj = [l for l in leads_ if not l["detalhes"].get("cnpj")]
        if job_id:
            db.job_progress(job_id, f"Buscando CNPJ de {len(sem_cnpj)} lead(s) com o agente…")
        with ThreadPoolExecutor(max_workers=4) as pool:
            for lead_, found in zip(sem_cnpj, pool.map(_agent_cnpj, sem_cnpj)):
                lead_["detalhes"].update(found)
    for i, lead_ in enumerate(leads_, 1):
        try:
            data = verify.verify_lead(lead_["detalhes"])
            db.save_lead_data(lead_["id"], data)
            origem = data.get("origem_dados") or {}
            ok += origem.get("cadastro") == "verificado" or origem.get("localizacao") == "verificado"
        except Exception:  # noqa: BLE001
            log.exception("Falha ao verificar lead %s", lead_["id"])
        if job_id:
            db.job_progress(job_id, f"Verificando CNPJ e rotas… {i}/{len(leads_)} lead(s), {ok} verificado(s).", ok)
    return ok, len(leads_)


def _run_verify(job_id: str, leads_: list[dict], buscar_cnpj: bool) -> None:
    try:
        ok, total = _verify_leads(leads_, job_id, buscar_cnpj)
        db.finish_job(job_id, "concluido", "verificacao", f"{ok} de {total} lead(s) verificados em fontes públicas.", 0, ok)
    except Exception as exc:  # noqa: BLE001
        db.finish_job(job_id, "erro", None, f"{type(exc).__name__}: {exc}"[:500])


@app.post("/api/verify")
def verify_leads(body: VerifyIn, tasks: BackgroundTasks, user: str = Depends(current_user)):
    """Verifica CNPJ (BrasilAPI/ReceitaWS) e rota de carro (Nominatim + OSRM) dos leads reais."""
    if not verify.enabled():
        raise HTTPException(400, "Verificação externa desativada (VERIFICACAO_EXTERNA=false)")
    leads_ = db.leads_for_verification(body.lead_ids or None, only_pending=not body.todos)
    job_id = uuid.uuid4().hex
    db.create_job(job_id, {"tipo": "verificar", **body.model_dump()})
    tasks.add_task(_run_verify, job_id, leads_, body.buscar_cnpj)
    return {"job_id": job_id, "total": len(leads_)}


def _mock_signals(lead_: dict) -> list[dict]:
    return []  # modo demo: os sinais fictícios já vêm na coleta


def _run_signals(job_id: str, leads_: list[dict], use_foundry: bool) -> None:
    feitos = novos = 0
    try:
        find = _mock_signals
        if use_foundry:
            from .foundry_client import find_signals as find

        def work(lead_: dict) -> int:
            try:
                return db.add_signals(lead_["id"], find(lead_))
            except Exception:  # noqa: BLE001
                log.exception("Falha ao buscar sinais do lead %s", lead_["id"])
                return 0

        db.job_progress(job_id, f"Buscando sinais de {len(leads_)} lead(s)…")
        with ThreadPoolExecutor(max_workers=4) as pool:
            for n in pool.map(work, leads_):
                feitos += 1
                novos += n
                db.job_progress(job_id, f"Buscando sinais… {feitos}/{len(leads_)} lead(s), {novos} sinal(is) datado(s).", novos)
        db.finish_job(job_id, "concluido", "foundry" if use_foundry else "demo",
                      f"{novos} sinal(is) datado(s) gravados para {len(leads_)} lead(s).", 0, novos)
    except Exception as exc:  # noqa: BLE001
        db.finish_job(job_id, "erro", None, f"{type(exc).__name__}: {exc}"[:500])


@app.post("/api/signals")
def signals(body: EnrichIn, tasks: BackgroundTasks, user: str = Depends(current_user)):
    """Busca (via agente) sinais de compra datados dos leads informados (vazio = leads reais sem sinal datado)."""
    use_foundry = body.modo != "mock" and config.foundry_enabled()
    leads_ = db.leads_for_signals(body.lead_ids or None)
    job_id = uuid.uuid4().hex
    db.create_job(job_id, {"tipo": "sinais", **body.model_dump()})
    tasks.add_task(_run_signals, job_id, leads_, use_foundry)
    return {"job_id": job_id, "total": len(leads_)}


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
