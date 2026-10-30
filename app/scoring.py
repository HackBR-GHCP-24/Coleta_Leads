"""Cálculo do ranking de oportunidades.

score_final = 60% score do agente + 40% score heurístico (aderência, porte, sinais, cidade e confiança).
"""
from __future__ import annotations

from .config import SERVICES

_PORTE = {"grande": 1.0, "media": 0.8, "média": 0.8, "pequena": 0.5}
_CIDADE = {"itu": 1.0, "sorocaba": 1.0, "são paulo": 0.7, "sao paulo": 0.7}
_CONFIANCA = {"alta": 1.0, "media": 0.7, "média": 0.7, "baixa": 0.4}


def heuristic_score(lead: dict) -> float:
    services = [s for s in lead.get("servicos_recomendados") or [] if s in SERVICES]
    aderencia = min(len(services) / 4, 1.0)
    porte = _PORTE.get(str(lead.get("porte", "")).lower(), 0.6)
    sinais = min(len(lead.get("sinais") or []) / 3, 1.0)
    cidade = _CIDADE.get(str(lead.get("cidade", "")).lower(), 0.5)
    confianca = _CONFIANCA.get(str(lead.get("nivel_confianca", "")).lower(), 0.6)
    return 100 * (0.35 * aderencia + 0.2 * porte + 0.25 * sinais + 0.1 * cidade + 0.1 * confianca)


def final_score(lead: dict) -> int:
    try:
        agent = float(lead.get("score_oportunidade") or 0)
    except (TypeError, ValueError):
        agent = 0.0
    agent = max(0.0, min(agent, 100.0))
    return round(0.6 * agent + 0.4 * heuristic_score(lead))


def tier(score: int) -> str:
    if score >= 75:
        return "quente"
    if score >= 55:
        return "morno"
    return "frio"
