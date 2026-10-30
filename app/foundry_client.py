"""Cliente do agente publicado no Azure AI Foundry (Agents v2 / Responses API)."""
from __future__ import annotations

import json
import re

from . import config
from .config import SERVICES

_openai = None


def _get_openai():
    global _openai
    if _openai is None:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        project = AIProjectClient(
            endpoint=config.FOUNDRY_PROJECT_ENDPOINT,
            credential=DefaultAzureCredential(exclude_interactive_browser_credential=True),
        )
        _openai = project.get_openai_client()
    return _openai


def build_prompt(cities: list[str], services: list[str], segment: str | None, quantity: int) -> str:
    cities = cities or config.CITIES
    parts = [
        f"Encontre {quantity} empresas (clientes finais, NÃO empresas de TI/MSPs/integradores) com potencial de "
        f"contratar um parceiro de TI nas cidades: {', '.join(cities)} (SP).",
    ]
    if services:
        names = ", ".join(f"{k} ({SERVICES.get(k, k)})" for k in services)
        parts.append(f"Priorize empresas com necessidade destes serviços: {names}.")
    if segment:
        parts.append(f"Foque no segmento: {segment}.")
    parts.append("Responda somente com o JSON no formato definido nas suas instruções.")
    return " ".join(parts)


_GENERIC = re.compile(r"\b(setor|segmento)\s+geral\b|^empresas\s+(de|do|da|em|industriais)\b", re.I)


def parse_response(text: str) -> dict:
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.S)
    candidate = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
    data = json.loads(candidate)
    if isinstance(data, list):
        data = {"leads": data}
    data["leads"] = [l for l in data.get("leads", []) if l.get("empresa") and not _GENERIC.search(l["empresa"])]
    for lead in data["leads"]:
        site = (lead.get("site") or "").strip()
        if site and not site.startswith("http") and "." in site and " " not in site:
            lead["site"] = "https://" + site
        lead["servicos_recomendados"] = [s for s in lead.get("servicos_recomendados") or [] if s in SERVICES]
    return data


def _citations(response) -> list[dict]:
    out = []
    for item in getattr(response, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            for ann in getattr(part, "annotations", None) or []:
                url = getattr(ann, "url", None)
                if url and url not in {c["url"] for c in out}:
                    out.append({"url": url, "title": getattr(ann, "title", "") or ""})
    return out


def _attach_sources(data: dict, citations: list[dict]) -> None:
    for lead in data.get("leads", []):
        fontes = [f for f in lead.get("fontes") or [] if isinstance(f, str) and f.startswith("http")]
        if not fontes and citations:
            first = (lead.get("empresa") or "").split()[0].lower() if lead.get("empresa") else ""
            fontes = [c["url"] for c in citations if first and first in (c["url"] + c["title"]).lower()]
        lead["fontes"] = fontes
        for s in lead.get("sinais") or []:
            if s.get("fonte") and not str(s["fonte"]).startswith("http"):
                s["fonte"] = fontes[0] if fontes else None


def run_agent(cities: list[str], services: list[str], segment: str | None, quantity: int) -> dict:
    response = _get_openai().responses.create(
        input=build_prompt(cities, services, segment, quantity),
        extra_body={"agent_reference": {"name": config.FOUNDRY_AGENT_NAME, "type": "agent_reference"}},
        timeout=300,
    )
    text = response.output_text or ""
    if not text:
        raise RuntimeError("O agente não retornou texto")
    data = parse_response(text)
    citations = _citations(response)
    _attach_sources(data, citations)
    data["fontes_pesquisa"] = citations
    data["response_id"] = response.id
    return data
