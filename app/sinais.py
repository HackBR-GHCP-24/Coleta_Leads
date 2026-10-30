"""Sinais de compra: tipos, normalização, datas e peso com decaimento por idade.

Peso efetivo = peso do tipo × decaimento, em que decaimento = 0,5 ^ (idade em dias / MEIA_VIDA_DIAS).
Sinais sem data valem SEM_DATA; sinais (não-inferência) sem URL de fonte são multiplicados por SEM_FONTE. A força dos sinais (0–1) usada no score é a soma dos pesos efetivos dividida por SATURACAO.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

TIPOS = {
    "vaga_ti": ("Vaga de TI", 0.8),
    "expansao": ("Expansão / nova unidade", 1.0),
    "incidente": ("Incidente / segurança", 1.0),
    "troca_sistema": ("Troca de sistemas / migração", 0.9),
    "noticia": ("Notícia / investimento", 0.6),
    "inferencia": ("Indício (sem evidência datada)", 0.3),
}
MEIA_VIDA_DIAS = 180
SEM_DATA = 0.35
SATURACAO = 1.2
SEM_FONTE = 0.5

_KEYWORDS = [
    ("incidente", r"ransomware|ataque|hacker|incidente|vazamento|invas|indisponib|instabilidade|ciber"),
    ("vaga_ti", r"\bvaga|contrat\w+ (de )?(analista|t[eé]cnico|dba)|analista de (suporte|infra|ti|sistemas)|t[eé]cnico de (ti|inform)"),
    ("troca_sistema", r"migra|implanta\w* (de |do |um )?(erp|sistema|sap|totvs|protheus)|novo (erp|sistema)|troca de sistema|nuvem|cloud|microsoft 365|moderniza"),
    ("expansao", r"expans|nova (unidade|f[aá]brica|filial|planta|sede)|novo (centro|cd|hospital|galp)|inaugur|amplia|investimento de r\$|constru"),
    ("noticia", r"not[ií]cia|anuncia|aquisi|fus[aã]o|investe|investimento|recebe aporte|licita"),
]


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def classify(descricao: str, has_date: bool) -> str:
    text = (descricao or "").lower()
    for tipo, pat in _KEYWORDS:
        if re.search(pat, text) or re.search(pat, _ascii(text)):
            return "inferencia" if tipo == "noticia" and not has_date else tipo
    return "noticia" if has_date else "inferencia"


def parse_date(value) -> str | None:
    """Aceita AAAA-MM-DD, AAAA-MM, AAAA, DD/MM/AAAA e MM/AAAA. Devolve ISO (dia 1 quando faltar o dia)."""
    s = str(value or "").strip()
    if not s or s.lower() in ("null", "none", "n/a", "sem data"):
        return None
    for pat, fn in (
        (r"^(\d{4})-(\d{1,2})-(\d{1,2})", lambda m: (m[1], m[2], m[3])),
        (r"^(\d{4})-(\d{1,2})$", lambda m: (m[1], m[2], 1)),
        (r"^(\d{1,2})/(\d{1,2})/(\d{4})", lambda m: (m[3], m[2], m[1])),
        (r"^(\d{1,2})/(\d{4})$", lambda m: (m[2], m[1], 1)),
        (r"^(\d{4})$", lambda m: (m[1], 7, 1)),
    ):
        m = re.match(pat, s)
        if m:
            try:
                return date(*map(int, fn(m))).isoformat()
            except ValueError:
                return None
    return None


def decay(data_iso: str | None, today: date | None = None) -> float:
    if not data_iso:
        return SEM_DATA
    today = today or date.today()
    idade = (today - date.fromisoformat(data_iso)).days
    return 1.0 if idade <= 0 else max(0.05, 0.5 ** (idade / MEIA_VIDA_DIAS))


def factor(s: dict, today: date | None = None) -> float:
    """Decaimento por idade × penalidade por falta de fonte comprovável."""
    f = decay(s.get("data"), today)
    return f if s.get("fonte") or s.get("tipo") == "inferencia" else f * SEM_FONTE


def normalize(items) -> list[dict]:
    out, seen = [], set()
    for it in items or []:
        if isinstance(it, str):
            it = {"descricao": it}
        if not isinstance(it, dict):
            continue
        desc = str(it.get("descricao") or "").strip()
        if not desc:
            continue
        key = _ascii(desc)[:120]
        if key in seen:
            continue
        seen.add(key)
        data = parse_date(it.get("data"))
        fonte = it.get("fonte") if str(it.get("fonte") or "").startswith("http") else None
        tipo = it.get("tipo") if it.get("tipo") in TIPOS else classify(desc, bool(data))
        if tipo != "inferencia" and not data and not fonte:
            tipo = "inferencia"
        out.append({"tipo": tipo, "descricao": desc[:500], "data": data, "fonte": fonte, "peso": TIPOS[tipo][1]})
    return out


def enrich(sinais: list[dict], today: date | None = None) -> list[dict]:
    """Acrescenta decaimento e peso efetivo e ordena do mais recente para o mais antigo."""
    out = []
    for s in sinais:
        d = factor(s, today)
        out.append({**s, "tipo_label": TIPOS.get(s["tipo"], (s["tipo"],))[0], "decaimento": round(d, 3),
                    "peso_efetivo": round(s["peso"] * d, 3)})
    return sorted(out, key=lambda s: (s["data"] or "0000", s["peso_efetivo"]), reverse=True)


def strength(sinais: list[dict], today: date | None = None) -> float:
    return min(1.0, sum(s["peso"] * factor(s, today) for s in sinais) / SATURACAO)
