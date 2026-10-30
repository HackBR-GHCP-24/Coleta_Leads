"""Cálculo do ranking de oportunidades: explicável e com pesos ajustáveis.

score_final = Σ (peso do fator × valor do fator 0–1) ÷ Σ pesos × 100. Fatores: avaliação do agente, aderência aos serviços
(ponderada pelo peso de cada serviço), sinais de compra (tipo e idade, ver app/sinais.py), porte, distância até a base mais
próxima, e-mail disponível, confiança e aderência ao perfil de cliente ideal (PME sem TI interna, ver app/perfil.py).
Leads fora do perfil perdem um percentual configurável do score (penalidade). Os pesos ficam na tabela settings e podem ser alterados na tela de configuração.
"""
from __future__ import annotations

import copy

from . import perfil as perfil_mod
from . import sinais as sinais_mod
from .config import SERVICES

_PORTE = {"media": 1.0, "média": 1.0, "pequena": 0.8, "grande": 0.2}  # foco em PMEs
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
    "perfil": "Perfil de cliente ideal (PME sem TI interna)",
}
ADERENCIA_SATURACAO = 3.0  # soma dos pesos dos serviços aderentes que vale 100%
DEFAULT_WEIGHTS = {
    "servicos": {k: 1.0 for k in SERVICE_GROUPS},
    "fatores": {"agente": 50, "aderencia": 15, "sinais": 12, "porte": 8, "distancia": 6, "email": 5, "confianca": 4,
                "perfil": 15},
    "perfil": dict(perfil_mod.DEFAULT),
}
LIMITES = {"servicos": (0.0, 3.0), "fatores": (0.0, 100.0), "perfil": perfil_mod.LIMITES}
_weights = copy.deepcopy(DEFAULT_WEIGHTS)


def get_weights() -> dict:
    return copy.deepcopy(_weights)


def validate_weights(w: dict) -> dict:
    """Completa com os padrões e valida os limites. Lança ValueError em caso de valor inválido."""
    out = copy.deepcopy(DEFAULT_WEIGHTS)
    for grupo, lim in LIMITES.items():
        for k, v in ((w or {}).get(grupo) or {}).items():
            if k not in out[grupo]:
                raise ValueError(f"Peso desconhecido: {grupo}.{k}")
            lo, hi = lim[k] if isinstance(lim, dict) else lim
            v = float(v)
            if not lo <= v <= hi:
                raise ValueError(f"{grupo}.{k} deve estar entre {lo:g} e {hi:g}")
            out[grupo][k] = round(v, 2)
    if out["perfil"]["func_min"] >= out["perfil"]["func_max"]:
        raise ValueError("O mínimo de funcionários deve ser menor que o máximo")
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
    pcfg = w.get("perfil") or perfil_mod.DEFAULT
    av = perfil_mod.avaliar(lead, pcfg)
    funcs = av["dados"]["funcionarios"]
    if funcs is not None:
        porte_val = 1.0 if pcfg["func_min"] <= funcs <= pcfg["func_max"] else 0.2
        porte_txt = f"{porte or 'porte'} · ≈{funcs} funcionários ({'dentro' if porte_val == 1 else 'fora'} da faixa {pcfg['func_min']:g}–{pcfg['func_max']:g})"
    else:
        porte_val, porte_txt = _PORTE.get(porte, 0.6), (porte or "porte não informado") + " (sem estimativa de funcionários)"
    if av["status"] == "fora":
        perfil_txt = "fora do perfil: " + "; ".join(av["motivos"] or ["marcado manualmente"])
    elif av["excecao"]:
        perfil_txt = "exceção — " + av["excecao"]
    else:
        perfil_txt = "dentro do perfil" + (" (manual)" if av["manual"] else "") + (
            " · " + "; ".join(av["alertas"]) if av["alertas"] else "")
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
        "porte": (porte_val, porte_txt),
        "distancia": (proximity(lead), "não informada" if h is None else "na base" if h == 0 else f"{_fmt_h(h)} de carro até {base}"),
        "email": (_EMAIL.get(email_tipo, 0.0), {"encontrado": "e-mail encontrado na fonte", "manual": "e-mail informado manualmente",
                                                "inferido": "e-mail inferido (meio ponto)"}.get(email_tipo, "sem e-mail")),
        "confianca": (_CONFIANCA.get(confianca, 0.6), confianca or "não informada"),
        "perfil": (av["valor"], perfil_txt),
    }
    total = sum(w["fatores"].values()) or 1
    fatores = []
    for k, label in FACTORS.items():
        valor, detalhe = valores[k]
        peso = w["fatores"][k]
        fatores.append({"id": k, "label": label, "peso": peso, "peso_pct": round(100 * peso / total, 1),
                        "valor": round(valor, 3), "pontos": round(100 * valor * peso / total, 1), "detalhe": detalhe})
    bruto = sum(100 * valores[k][0] * w["fatores"][k] / total for k in FACTORS)
    penalidade = None
    if av["status"] == "fora":
        pct = pcfg["penalidade"]
        penalidade = {"pct": pct, "pontos": round(bruto * pct / 100, 1), "motivos": av["motivos"] or ["marcado manualmente"]}
        bruto -= bruto * pct / 100
    return {"score": round(bruto), "fatores": fatores, "servicos": servicos, "penalidade": penalidade, "perfil": av}


def final_score(lead: dict, sinais: list[dict] | None = None) -> int:
    return breakdown(lead, sinais)["score"]


def tier(score: int) -> str:
    if score >= 75:
        return "quente"
    if score >= 55:
        return "morno"
    return "frio"
