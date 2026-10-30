"""Cálculo do ranking de oportunidades.

score_final = 60% score do agente + 40% score heurístico (aderência, porte, sinais, proximidade e confiança).
A proximidade usa o tempo estimado de carro até a base mais próxima (Itu, Sorocaba ou São Paulo).
"""
from __future__ import annotations

from .config import SERVICES

_PORTE = {"grande": 1.0, "media": 0.8, "média": 0.8, "pequena": 0.5}
_BASES = {"itu", "sorocaba", "são paulo", "sao paulo"}
_CONFIANCA = {"alta": 1.0, "media": 0.7, "média": 0.7, "baixa": 0.4}


def travel_hours(lead: dict) -> float | None:
    try:
        h = lead.get("tempo_viagem_h")
        if h is not None:
            return max(0.0, float(h))
    except (TypeError, ValueError):
        pass
    return 0.0 if str(lead.get("cidade", "")).strip().lower() in _BASES else None


def proximity(lead: dict) -> float:
    """1.0 na base, caindo linearmente até 0.3 com 3,5 h de carro; 0.5 quando desconhecido."""
    h = travel_hours(lead)
    return 0.5 if h is None else max(0.3, 1.0 - 0.2 * h)


def heuristic_score(lead: dict) -> float:
    services = [s for s in lead.get("servicos_recomendados") or [] if s in SERVICES]
    aderencia = min(len(services) / 4, 1.0)
    porte = _PORTE.get(str(lead.get("porte", "")).lower(), 0.6)
    sinais = min(len(lead.get("sinais") or []) / 3, 1.0)
    cidade = proximity(lead)
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
