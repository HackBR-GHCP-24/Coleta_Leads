"""Cálculo do ranking de oportunidades: explicável e com pesos ajustáveis.

score_final = Σ (peso do fator × valor do fator 0–1) ÷ Σ pesos × 100. Fatores: avaliação do agente, aderência aos serviços
(ponderada pelo peso de cada serviço), sinais de compra (tipo e idade, ver app/sinais.py), porte, distância até a base mais
próxima, e-mail disponível e confiança. Os pesos ficam na tabela settings e podem ser alterados na tela de configuração.
"""
from __future__ import annotations

import copy

from . import sinais as sinais_mod
from .config import SERVICES

_PORTE = {"grande": 1.0, "media": 0.8, "média": 0.8, "pequena": 0.5}
_BASES = {"itu", "sorocaba", "são paulo", "sao paulo"}
_CONFIANCA = {"alta": 1.0, "media": 0.7, "média": 0.7, "baixa": 0.4}
_EMAIL = {"encontrado": 1.0, "manual": 1.0, "inferido": 0.5}

# Grupos de serviço com peso ajustável (0–3) e os serviços do agente que cada um cobre
SERVICE_GROUPS = {
    "acronis": ("Acronis", ["acronis"]),
    "modern_work": ("Modern Work / serviços gerenciados", ["modern_work", "servicos_gerenciados"]),
    "residentes": ("Residentes N1/N2", ["residentes"]),
    "banco_dados": ("Banco de dados", ["banco_dados"]),
    "redes_fortigate": ("Redes / Fortigate", ["redes_fortigate"]),
    "windows_ad": ("Windows Server / AD", ["windows_ad"]),
    "infra_cabeada": ("Infraestrutura cabeada", ["infra_cabeada"]),
}
FACTORS = {
    "agente": "Avaliação do agente (IA)",
    "aderencia": "Aderência aos serviços",
    "sinais": "Sinais de compra",
    "porte": "Porte",
    "distancia": "Distância até a base",
    "email": "E-mail disponível",
    "confianca": "Confiança dos dados",
}
ADERENCIA_SATURACAO = 3.0  # soma dos pesos dos serviços aderentes que vale 100%
DEFAULT_WEIGHTS = {
    "servicos": {k: 1.0 for k in SERVICE_GROUPS},
    "fatores": {"agente": 50, "aderencia": 15, "sinais": 12, "porte": 8, "distancia": 6, "email": 5, "confianca": 4},
}
LIMITES = {"servicos": (0.0, 3.0), "fatores": (0.0, 100.0)}
_weights = copy.deepcopy(DEFAULT_WEIGHTS)


def get_weights() -> dict:
    return copy.deepcopy(_weights)


def validate_weights(w: dict) -> dict:
    """Completa com os padrões e valida os limites. Lança ValueError em caso de valor inválido."""
    out = copy.deepcopy(DEFAULT_WEIGHTS)
    for grupo, (lo, hi) in LIMITES.items():
        for k, v in ((w or {}).get(grupo) or {}).items():
            if k not in out[grupo]:
                raise ValueError(f"Peso desconhecido: {grupo}.{k}")
            v = float(v)
            if not lo <= v <= hi:
                raise ValueError(f"{grupo}.{k} deve estar entre {lo:g} e {hi:g}")
            out[grupo][k] = round(v, 2)
    if not any(out["fatores"].values()):
        raise ValueError("Pelo menos um fator precisa ter peso maior que zero")
    return out


def set_weights(w: dict) -> None:
    global _weights
    _weights = validate_weights(w)


def service_group(service: str) -> str | None:
    return next((g for g, (_, svcs) in SERVICE_GROUPS.items() if service in svcs), None)


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


def _fmt_h(h: float) -> str:
    m = round(h * 60)
    return f"{m} min" if m < 60 else f"{m // 60}h{m % 60:02d}"


def breakdown(lead: dict, sinais: list[dict] | None = None, weights: dict | None = None) -> dict:
    """Decompõe o score por fator (valor 0–1, peso e pontos) e por serviço."""
    w = weights or _weights
    sinais = sinais if sinais is not None else sinais_mod.normalize(lead.get("sinais"))
    recomendados = {s for s in lead.get("servicos_recomendados") or [] if s in SERVICES}

    servicos, soma = [], 0.0
    for g, (label, svcs) in SERVICE_GROUPS.items():
        match = bool(recomendados.intersection(svcs))
        peso = w["servicos"][g]
        soma += peso if match else 0
        servicos.append({"id": g, "label": label, "peso": peso, "recomendado": match})
    aderencia = min(soma / ADERENCIA_SATURACAO, 1.0)

    try:
        agente = max(0.0, min(float(lead.get("score_oportunidade") or 0), 100.0))
    except (TypeError, ValueError):
        agente = 0.0
    porte = str(lead.get("porte") or "").lower()
    h = travel_hours(lead)
    contatos = lead.get("contatos_email") or []
    email_tipo = contatos[0].get("tipo") if contatos and isinstance(contatos[0], dict) else None
    confianca = str(lead.get("nivel_confianca") or "").lower()
    datados = sum(1 for s in sinais if s.get("data") and s.get("fonte"))
    base = lead.get("base_proxima") or "base"

    valores = {
        "agente": (agente / 100, f"nota {agente:.0f}/100 dada pelo agente"),
        "aderencia": (aderencia, ", ".join(f"{s['label']} ×{s['peso']:g}" for s in servicos if s["recomendado"])
                      or "nenhum serviço do portfólio recomendado"),
        "sinais": (sinais_mod.strength(sinais), f"{len(sinais)} sinal(is), {datados} datado(s) com fonte"),
        "porte": (_PORTE.get(porte, 0.6), porte or "porte não informado"),
        "distancia": (proximity(lead), "não informada" if h is None else "na base" if h == 0 else f"{_fmt_h(h)} de carro até {base}"),
        "email": (_EMAIL.get(email_tipo, 0.0), {"encontrado": "e-mail encontrado na fonte", "manual": "e-mail informado manualmente",
                                                "inferido": "e-mail inferido (meio ponto)"}.get(email_tipo, "sem e-mail")),
        "confianca": (_CONFIANCA.get(confianca, 0.6), confianca or "não informada"),
    }
    total = sum(w["fatores"].values()) or 1
    fatores = []
    for k, label in FACTORS.items():
        valor, detalhe = valores[k]
        peso = w["fatores"][k]
        fatores.append({"id": k, "label": label, "peso": peso, "peso_pct": round(100 * peso / total, 1),
                        "valor": round(valor, 3), "pontos": round(100 * valor * peso / total, 1), "detalhe": detalhe})
    score = round(sum(100 * valores[k][0] * w["fatores"][k] / total for k in FACTORS))
    return {"score": score, "fatores": fatores, "servicos": servicos}


def final_score(lead: dict, sinais: list[dict] | None = None) -> int:
    return breakdown(lead, sinais)["score"]


def tier(score: int) -> str:
    if score >= 75:
        return "quente"
    if score >= 55:
        return "morno"
    return "frio"
