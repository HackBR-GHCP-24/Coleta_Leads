"""Normalização dos e-mails de contato dos leads.

Cada contato: {"email": str, "fonte": url | None, "tipo": "encontrado" | "inferido" | "manual"}.
- encontrado: publicado na fonte (site oficial, página de contato etc.)
- inferido: padrão provável (ex.: contato@dominio) NÃO confirmado em fonte; precisa de validação
- manual: informado pelo usuário no app
"""
from __future__ import annotations

import re

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
TIPOS = ("encontrado", "manual", "inferido")
_PRIORIDADE = {t: i for i, t in enumerate(TIPOS)}


def valid(email: str | None) -> bool:
    return bool(email) and bool(EMAIL_RE.match(email.strip()))


def normalize(contacts) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    if isinstance(contacts, (str, dict)):
        contacts = [contacts]
    for c in contacts or []:
        if isinstance(c, str):
            c = {"email": c}
        if not isinstance(c, dict):
            continue
        email = str(c.get("email") or "").strip().strip("<>").removeprefix("mailto:").lower()
        if not valid(email) or email in seen:
            continue
        fonte = c.get("fonte")
        fonte = fonte.split()[0].rstrip(".,;)") if isinstance(fonte, str) and fonte.startswith("http") else None
        tipo = str(c.get("tipo") or "").lower()
        if tipo not in TIPOS:
            tipo = "encontrado" if fonte else "inferido"
        if tipo == "encontrado" and not fonte:
            tipo = "inferido"  # sem fonte verificável não pode ser considerado "encontrado"
        seen.add(email)
        out.append({"email": email, "fonte": fonte, "tipo": tipo})
    return sorted(out, key=lambda x: _PRIORIDADE[x["tipo"]])


def primary(contacts: list[dict]) -> tuple[str | None, str | None]:
    return (contacts[0]["email"], contacts[0]["tipo"]) if contacts else (None, None)


def _host(url: str | None) -> str:
    host = (url or "").split("//")[-1].split("/")[0].split(":")[0].lower()
    return host.removeprefix("www.")


def _same_org(domain: str, host: str) -> bool:
    return bool(host) and (domain == host or domain.endswith("." + host) or host.endswith("." + domain))


def company_only(contacts: list[dict], site: str | None, limit: int = 5) -> list[dict]:
    """Descarta e-mails de terceiros (agências, fornecedores): o domínio deve ser o do site ou da página-fonte."""
    site_host = _host(site)
    out = []
    for c in contacts:
        domain = c["email"].split("@")[1]
        hosts = [h for h in (site_host, _host(c.get("fonte"))) if h]
        if not hosts or any(_same_org(domain, h) for h in hosts):
            out.append(c)
    return out[:limit]
