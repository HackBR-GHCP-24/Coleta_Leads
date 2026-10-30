"""Verificação de dados com fontes públicas (substitui estimativas da IA quando possível).

- CNPJ: BrasilAPI (https://brasilapi.com.br), com fallback para ReceitaWS (limite de 3 consultas/min).
- Rota: geocodificação com Nominatim/OpenStreetMap (1 req/s) e rota de carro com OSRM (servidor público de demonstração).
  O tempo do OSRM não considera trânsito.
Os resultados são guardados em cache no SQLite para respeitar os limites de uso dessas APIs.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import config

log = logging.getLogger("coleta_leads.verify")
_UA = "ColetaLeads-demo/1.0 (prospeccao B2B; github.com/HackBR-GHCP-24/Coleta_Leads)"
_lock = threading.Lock()
_last_call: dict[str, float] = {}
_INTERVAL = {"nominatim": 1.1, "osrm": 0.5, "brasilapi": 0.4, "receitaws": 21.0}

# Coordenadas (lat, lon) do centro de cada base de atendimento
BASE_COORDS = {"Itu": (-23.2642, -47.2992), "Sorocaba": (-23.5015, -47.4526), "São Paulo": (-23.5505, -46.6333)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _throttle(service: str) -> None:
    with _lock:
        wait = _INTERVAL[service] - (time.monotonic() - _last_call.get(service, 0))
        if wait > 0:
            time.sleep(wait)
        _last_call[service] = time.monotonic()


def _get(service: str, url: str) -> dict | list:
    _throttle(service)
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def _cache(kind: str, key: str, fn):
    from . import db

    hit = db.cache_get(kind, key)
    if hit is not None:
        return hit
    value = fn()
    if value is not None:
        db.cache_set(kind, key, value)
    return value


# ---------------------------------------------------------------- CNPJ
def clean_cnpj(value) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) == 14 and valid_cnpj(digits) else None


def valid_cnpj(d: str) -> bool:
    if len(d) != 14 or len(set(d)) == 1:
        return False

    def dv(base: str) -> str:
        pesos = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2][-len(base):]
        r = sum(int(x) * p for x, p in zip(base, pesos)) % 11
        return "0" if r < 2 else str(11 - r)

    return dv(d[:12]) == d[12] and dv(d[:13]) == d[13]


def fmt_cnpj(d: str) -> str:
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def _money(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _brasilapi(cnpj: str) -> dict:
    d = _get("brasilapi", f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}")
    end = " ".join(x for x in [d.get("descricao_tipo_de_logradouro"), d.get("logradouro")] if x)
    return {
        "razao_social": d.get("razao_social"),
        "nome_fantasia": d.get("nome_fantasia") or None,
        "cnae": str(d.get("cnae_fiscal") or "") or None,
        "cnae_descricao": d.get("cnae_fiscal_descricao"),
        "porte": d.get("porte"),
        "situacao": d.get("descricao_situacao_cadastral"),
        "matriz_filial": d.get("descricao_identificador_matriz_filial"),
        "capital_social": _money(d.get("capital_social")),
        "socio_exterior": any(str(q.get("codigo_pais") or "") not in ("", "105", "None") or
                              "exterior" in str(q.get("qualificacao_socio") or "").lower() for q in d.get("qsa") or []),
        "endereco": " ".join(x for x in [end, d.get("numero"), "-", d.get("bairro")] if x).strip(" -"),
        "cep": d.get("cep"),
        "municipio": (d.get("municipio") or "").title() or None,
        "uf": d.get("uf"),
        "abertura": d.get("data_inicio_atividade"),
        "fonte": "BrasilAPI",
    }


def _receitaws(cnpj: str) -> dict:
    d = _get("receitaws", f"https://receitaws.com.br/v1/cnpj/{cnpj}")
    if d.get("status") == "ERROR":
        raise ValueError(d.get("message") or "CNPJ não encontrado")
    ativ = (d.get("atividade_principal") or [{}])[0]
    abertura = d.get("abertura") or ""
    if re.match(r"\d{2}/\d{2}/\d{4}", abertura):
        abertura = "-".join(reversed(abertura.split("/")))
    return {
        "razao_social": d.get("nome"),
        "nome_fantasia": d.get("fantasia") or None,
        "cnae": re.sub(r"\D", "", ativ.get("code") or "") or None,
        "cnae_descricao": ativ.get("text"),
        "porte": d.get("porte"),
        "situacao": d.get("situacao"),
        "matriz_filial": d.get("tipo"),
        "capital_social": _money(d.get("capital_social")),
        "socio_exterior": any("exterior" in str(q.get("qual") or "").lower() or q.get("pais_origem")
                              for q in d.get("qsa") or []),
        "endereco": " ".join(x for x in [d.get("logradouro"), d.get("numero"), "-", d.get("bairro")] if x).strip(" -"),
        "cep": re.sub(r"\D", "", d.get("cep") or "") or None,
        "municipio": (d.get("municipio") or "").title() or None,
        "uf": d.get("uf"),
        "abertura": abertura or None,
        "fonte": "ReceitaWS",
    }


def lookup_cnpj(cnpj: str) -> dict | None:
    def fetch():
        for fn in (_brasilapi, _receitaws):
            try:
                out = fn(cnpj)
                if out.get("razao_social"):
                    return out
            except Exception as exc:  # noqa: BLE001
                log.warning("%s falhou para %s: %s", fn.__name__, cnpj, exc)
        return None

    data = _cache("cnpj2", cnpj, fetch)  # v2: inclui capital social e sócios no exterior
    if data:
        data = {**data, "cnpj": fmt_cnpj(cnpj)}
    return data


# ---------------------------------------------------------------- Rotas
def geocode(cidade: str, uf: str | None) -> tuple[float, float] | None:
    q = f"{cidade}, {uf or 'SP'}, Brasil"

    def fetch():
        res = _get("nominatim", "https://nominatim.openstreetmap.org/search?format=json&limit=1&countrycodes=br&"
                   + urllib.parse.urlencode({"q": q}))
        return [float(res[0]["lat"]), float(res[0]["lon"])] if res else None

    out = _cache("geo", q.lower(), fetch)
    return tuple(out) if out else None


def _route(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float] | None:
    key = f"{a[0]:.4f},{a[1]:.4f};{b[0]:.4f},{b[1]:.4f}"

    def fetch():
        res = _get("osrm", f"https://router.project-osrm.org/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}?overview=false")
        if res.get("code") != "Ok" or not res.get("routes"):
            return None
        r = res["routes"][0]
        return [r["distance"] / 1000, r["duration"] / 3600]

    out = _cache("route", key, fetch)
    return tuple(out) if out else None


def nearest_base(cidade: str, uf: str | None) -> dict | None:
    if not cidade:
        return None
    for base in BASE_COORDS:
        if cidade.strip().lower() == base.lower():
            return {"base": base, "distancia_km": 0.0, "tempo_h": 0.0, "fonte": "base de atendimento", "consultado_em": _now()}
    dest = geocode(cidade, uf)
    if not dest:
        return None
    best = None
    for base, coords in BASE_COORDS.items():
        r = _route(coords, dest)
        if r and (best is None or r[1] < best["tempo_h"]):
            best = {"base": base, "distancia_km": round(r[0]), "tempo_h": round(r[1], 2)}
    if best:
        best.update({"fonte": "OSRM / OpenStreetMap", "consultado_em": _now()})
    return best


# ---------------------------------------------------------------- Lead
def verify_lead(data: dict, cnpj_manual: str | None = None) -> dict:
    """Retorna os dados do lead com CNPJ e rota verificados (quando possível). Não altera o original."""
    data = dict(data)
    origem = dict(data.get("origem_dados") or {})
    if cnpj_manual is not None:  # "" remove o CNPJ informado
        cnpj = clean_cnpj(cnpj_manual)
        if not cnpj:
            data["cnpj"] = None
        origem["cnpj"] = "manual" if cnpj else None
    else:
        cnpj = clean_cnpj(data.get("cnpj"))
        if cnpj and origem.get("cnpj") != "manual":
            origem["cnpj"] = "ia"
    if cnpj:
        data["cnpj"] = fmt_cnpj(cnpj)
        info = lookup_cnpj(cnpj)
        if info:
            data["cnpj_dados"] = {**info, "consultado_em": _now()}
            origem["cadastro"] = "verificado"
        else:
            data.pop("cnpj_dados", None)
            origem["cadastro"] = "nao_encontrado"
    else:
        if data.get("cnpj"):
            data["cnpj_invalido"] = data["cnpj"]  # dígitos verificadores não conferem
            data["cnpj"] = None
        data.pop("cnpj_dados", None)
        origem.pop("cadastro", None)
        origem.pop("cnpj", None)

    if "estimativa_ia" not in data:
        data["estimativa_ia"] = {k: data.get(k) for k in ("base_proxima", "distancia_km", "tempo_viagem_h")}
    try:
        rota = nearest_base(data.get("cidade") or "", data.get("uf"))
    except Exception as exc:  # noqa: BLE001
        log.warning("Rota indisponível para %s: %s", data.get("cidade"), exc)
        rota = None
    if rota:
        data["rota"] = rota
        data["base_proxima"], data["distancia_km"], data["tempo_viagem_h"] = rota["base"], rota["distancia_km"], rota["tempo_h"]
        origem["localizacao"] = "verificado"
    else:
        origem.setdefault("localizacao", "ia")
    data["origem_dados"] = origem
    data["verificado_em"] = _now()
    return data


def enabled() -> bool:
    return config.VERIFICACAO_EXTERNA
