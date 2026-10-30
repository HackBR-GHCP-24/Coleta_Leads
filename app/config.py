from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "sim"}


FOUNDRY_PROJECT_ENDPOINT = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "").strip()
FOUNDRY_AGENT_NAME = os.getenv("FOUNDRY_AGENT_NAME", "").strip()
FOUNDRY_AGENT_ID = os.getenv("FOUNDRY_AGENT_ID", "").strip()
APP_MODE = os.getenv("APP_MODE", "auto").strip().lower()
APP_USERNAME = os.getenv("APP_USERNAME", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
JWT_SECRET = os.getenv("JWT_SECRET") or secrets.token_urlsafe(48)
JWT_EXPIRES_MINUTES = int(os.getenv("JWT_EXPIRES_MINUTES", "480"))
DATABASE_PATH = ROOT / os.getenv("DATABASE_PATH", "data/leads.db")
SEED_DEMO_DATA = _bool("SEED_DEMO_DATA", True)

CITIES = ["Itu", "Sorocaba", "São Paulo"]
SERVICES = {
    "acronis": "Acronis (backup, DR e cibersegurança)",
    "servicos_gerenciados": "Serviços gerenciados",
    "modern_work": "Modern Work Microsoft 365",
    "residentes": "Residentes N1/N2",
    "banco_dados": "Gerenciamento de banco de dados",
    "redes_fortigate": "Redes e Fortigate",
    "windows_ad": "Windows Server e Active Directory",
    "infra_cabeada": "Infraestrutura cabeada",
}


def foundry_enabled() -> bool:
    return APP_MODE != "mock" and bool(FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_AGENT_NAME)
