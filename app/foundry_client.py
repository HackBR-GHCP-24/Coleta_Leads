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


def build_prompt(bases: list[str], services: list[str], segment: str | None, quantity: int,
                 cities: list[str] | None = None, max_hours: float | None = None) -> str:
    bases = bases or config.BASES
    max_hours = max_hours or config.MAX_TRAVEL_HOURS
    parts = [
        f"Encontre {quantity} empresas (clientes finais, NÃO empresas de TI/MSPs/integradores) com potencial de "
        f"contratar um parceiro de TI, localizadas a até {max_hours:g} horas de carro de {', '.join(bases)} (SP).",
    ]
    if cities:
        parts.append(f"Concentre a busca nestas cidades: {', '.join(cities)}.")
    else:
        parts.append("Distribua os leads em pelo menos 3 cidades diferentes dentro desse raio, incluindo cidades fora das bases.")
    parts.append("Para cada empresa informe base_proxima, distancia_km e tempo_viagem_h (estimativas por rodovia).")
    if services:
        names = ", ".join(f"{k} ({SERVICES.get(k, k)})" for k in services)
        parts.append(f"Priorize empresas com necessidade destes serviços: {names}.")
    if segment:
        parts.append(f"Foque no segmento: {segment}.")
    parts.append("Responda somente com o JSON no formato definido nas suas instruções.")
    return " ".join(parts)


_GENERIC = re.compile(
    r"\b(setor|segmento)\s+geral\b|^empresas?\s+(de|do|da|em|industriais)\b|^empresas?\b.*\s(em|na|no)\s+[A-ZÀ-Ú]"
    r"|\((multinacional|não identificad\w*|confidencial)\)",
    re.I,
)


def _num(v) -> float | None:
    try:
        return round(float(str(v).replace(",", ".")), 2) if v not in (None, "") else None
    except ValueError:
        return None


def parse_response(text: str, max_hours: float | None = None) -> dict:
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
        lead["cidade"] = (lead.get("cidade") or "").strip() or None
        lead["distancia_km"] = _num(lead.get("distancia_km"))
        lead["tempo_viagem_h"] = _num(lead.get("tempo_viagem_h"))
    if max_hours:
        # tolerância de 15 min para as estimativas do modelo
        data["leads"] = [l for l in data["leads"] if l["tempo_viagem_h"] is None or l["tempo_viagem_h"] <= max_hours + 0.25]
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


def run_agent(bases: list[str], services: list[str], segment: str | None, quantity: int,
              cities: list[str] | None = None, max_hours: float | None = None) -> dict:
    max_hours = max_hours or config.MAX_TRAVEL_HOURS
    response = _get_openai().responses.create(
        input=build_prompt(bases, services, segment, quantity, cities, max_hours),
        extra_body={"agent_reference": {"name": config.FOUNDRY_AGENT_NAME, "type": "agent_reference"}},
        timeout=300,
    )
    text = response.output_text or ""
    if not text:
        raise RuntimeError("O agente não retornou texto")
    data = parse_response(text, max_hours)
    citations = _citations(response)
    _attach_sources(data, citations)
    data["fontes_pesquisa"] = citations
    data["response_id"] = response.id
    return data
