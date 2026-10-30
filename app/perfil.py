"""Perfil de cliente ideal (ICP): PMEs de ~20 a 500 funcionários sem TI interna estruturada.

Combina a evidência do agente (funcionários estimados, TI interna, grupo multinacional, terceirização) com os dados do
CNPJ (porte da Receita, capital social, matriz/filial, sócios no exterior). Leads fora do perfil não são apagados:
recebem status "fora", com os motivos, e uma penalidade no score (ver app/scoring.py).
"""
from __future__ import annotations

import re

DEFAULT = {"func_min": 20, "func_max": 500, "capital_max_mi": 50, "penalidade": 60}
LIMITES = {"func_min": (0, 10000), "func_max": (10, 100000), "capital_max_mi": (0.1, 100000), "penalidade": (0, 100)}
LABELS = {
    "func_min": "Mínimo de funcionários",
    "func_max": "Máximo de funcionários",
    "capital_max_mi": "Capital social máximo (R$ milhões)",
    "penalidade": "Penalidade fora do perfil (% do score)",
}
_SIM = {"sim", "s", "yes", "true", "1"}
_NAO = {"nao", "não", "n", "no", "false", "0"}


def _tri(value) -> tuple[str, str | None, str | None]:
    """Normaliza {status, evidencia, fonte} | bool | str → (sim|nao|desconhecido, evidencia, fonte)."""
    ev = fonte = None
    if isinstance(value, dict):
        ev = value.get("evidencia") or value.get("grupo")
        fonte = value.get("fonte") if str(value.get("fonte") or "").startswith("http") else None
        value = value.get("status")
    if isinstance(value, bool):
        return ("sim" if value else "nao"), ev, fonte
    v = str(value or "").strip().lower()
    return ("sim" if v in _SIM else "nao" if v in _NAO else "desconhecido"), ev, fonte


def _int(v) -> int | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    try:
        return int(float(str(v).replace(".", "").replace(",", "."))) if v not in (None, "") else None
    except ValueError:
        return None


def _mi(v: float) -> str:
    return f"R$ {v / 1e6:,.1f} mi".replace(",", "X").replace(".", ",").replace("X", ".") if v >= 1e6 else f"R$ {v:,.0f}".replace(",", ".")


# Só evidência de estrutura (liderança, equipe ou SOC próprio) desqualifica; uma vaga de analista/suporte isolada não.
_TI_FORTE = re.compile(
    r"\b(gerente|ger[eê]ncia|diretor|diretoria|coordenador|coordena[cç][aã]o|supervisor|head\b|manager\b|cio\b|cto\b|ciso\b|"
    r"soc\b|equipe|time de ti|departamento|[aá]rea de ti|centro de servi[cç]os|global|corporativ)",
    re.IGNORECASE,
)


def avaliar(lead: dict, cfg: dict | None = None) -> dict:
    """Retorna {status: dentro|fora, valor 0–1, motivos, alertas, excecao, manual, dados}."""
    cfg = {**DEFAULT, **(cfg or {})}
    motivos: list[str] = []
    alertas: list[str] = []
    cad = lead.get("cnpj_dados") or {}

    funcs = _int(lead.get("funcionarios_estimados"))
    if funcs is not None and funcs > cfg["func_max"]:
        motivos.append(f"≈{funcs} funcionários (acima de {cfg['func_max']:g})")
    elif funcs is not None and funcs < cfg["func_min"]:
        motivos.append(f"≈{funcs} funcionários (abaixo de {cfg['func_min']:g})")
    elif funcs is None and str(lead.get("porte") or "").lower() == "grande":
        motivos.append("porte grande segundo o agente")

    ti, ti_ev, ti_fonte = _tri(lead.get("ti_interna"))
    if ti == "sim":
        if not ti_ev or _TI_FORTE.search(ti_ev):
            motivos.append("TI interna estruturada" + (f": {ti_ev}" if ti_ev else ""))
        else:
            alertas.append(f"possível TI interna, evidência fraca (vaga operacional isolada não indica TI robusta): {ti_ev}")
    mn, mn_ev, mn_fonte = _tri(lead.get("multinacional"))
    if mn == "sim":
        motivos.append("grupo multinacional" + (f" ({mn_ev})" if mn_ev else ""))

    capital = cad.get("capital_social")
    if isinstance(capital, (int, float)) and capital >= cfg["capital_max_mi"] * 1e6:
        motivos.append(f"capital social {_mi(capital)} (Receita)")
    if str(cad.get("matriz_filial") or "").upper() == "FILIAL":
        alertas.append("CNPJ de filial (unidade de um grupo)")
    if cad.get("socio_exterior"):
        alertas.append("sócio domiciliado no exterior (Receita)")
    if str(cad.get("porte") or "").upper() == "DEMAIS" and funcs is None:
        alertas.append("porte DEMAIS na Receita (faturamento acima de EPP)")

    terc, terc_ev, terc_fonte = _tri(lead.get("terceirizacao"))
    excecao = None
    if motivos and terc == "sim" and terc_fonte:
        excecao = f"terceiriza TI: {terc_ev or 'sinal concreto'}"

    status = "fora" if motivos and not excecao else "dentro"
    if status == "fora":
        valor = 0.0
    elif excecao:
        valor = 0.4
    else:
        conhecido = (funcs is not None) + (ti != "desconhecido") + (mn != "desconhecido")
        valor = max(0.3, [0.6, 0.75, 0.9, 1.0][conhecido] - 0.15 * len(alertas))
        if ti == "nao":
            valor = min(1.0, valor + 0.05)

    manual = lead.get("perfil_manual") or {}
    if manual.get("status") in ("dentro", "fora"):
        status = manual["status"]
        valor = 1.0 if status == "dentro" else 0.0
        if manual.get("motivo"):
            (motivos if status == "fora" else alertas).insert(0, f"manual: {manual['motivo']}")

    return {
        "status": status,
        "valor": round(valor, 3),
        "motivos": motivos,
        "alertas": alertas,
        "excecao": excecao,
        "manual": bool(manual.get("status")),
        "dados": {
            "funcionarios": funcs,
            "ti_interna": {"status": ti, "evidencia": ti_ev, "fonte": ti_fonte},
            "multinacional": {"status": mn, "evidencia": mn_ev, "fonte": mn_fonte},
            "terceirizacao": {"status": terc, "evidencia": terc_ev, "fonte": terc_fonte},
            "capital_social": capital,
            "porte_receita": cad.get("porte"),
            "matriz_filial": cad.get("matriz_filial"),
        },
    }


def resumo(av: dict) -> str | None:
    """Motivo curto exibido na lista/coluna perfil_motivo."""
    if av["status"] == "fora":
        return "; ".join(av["motivos"])[:300] or "marcado manualmente"
    return av["excecao"]
