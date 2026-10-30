"""Provisiona (cria uma nova versão) do agente "Prospector de TI" no Azure AI Foundry (Agents v2).

Uso:
    az login
    python agent/provision_agent.py                 # cria/atualiza o agente (nova versão)
    python agent/provision_agent.py --list          # apenas lista deployments e conexões
    python agent/provision_agent.py --no-web-search # cria sem a ferramenta de pesquisa web

Autenticação via Entra ID (DefaultAzureCredential). Nenhuma chave é usada ou gravada.
O agente é registrado em agent/agent_info.json e no .env (FOUNDRY_AGENT_NAME / FOUNDRY_AGENT_ID).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

AGENT_NAME = os.getenv("FOUNDRY_AGENT_NAME") or "prospector-ti-itu-sorocaba-sp"
PREFERRED_MODELS = ["gpt-4.1", "gpt-4o", "gpt-5", "gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini"]


def get_client():
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        sys.exit("Defina FOUNDRY_PROJECT_ENDPOINT no .env")
    return AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())


def list_deployments(client) -> list[dict]:
    return [
        {"name": d.name, "model": getattr(d, "model_name", None), "version": getattr(d, "model_version", None)}
        for d in client.deployments.list()
    ]


def choose_deployment(deployments: list[dict]) -> str:
    wanted = os.getenv("FOUNDRY_MODEL_DEPLOYMENT")
    if wanted:
        return wanted
    for pref in PREFERRED_MODELS:
        for d in deployments:
            if (d["model"] or "").lower() == pref or (d["name"] or "").lower() == pref:
                return d["name"]
    for d in deployments:
        if (d["model"] or "").lower().startswith("gpt"):
            return d["name"]
    sys.exit("Nenhum deployment de chat encontrado. Defina FOUNDRY_MODEL_DEPLOYMENT no .env.")


def find_bing_connection(client):
    wanted = os.getenv("FOUNDRY_BING_CONNECTION_NAME")
    try:
        if wanted:
            return client.connections.get(name=wanted)
        for c in client.connections.list():
            kind = str(getattr(c, "type", "")).lower()
            if "bing" in kind or "bing" in c.name.lower():
                return c
    except Exception as exc:  # noqa: BLE001
        print(f"[aviso] não foi possível listar conexões: {exc}")
    return None


def build_tools(bing, web_search: bool) -> tuple[list, str]:
    from azure.ai.projects import models as m

    if bing:
        tool = m.BingGroundingTool(
            bing_grounding=m.BingGroundingSearchToolParameters(
                search_configurations=[m.BingGroundingSearchConfiguration(project_connection_id=bing.id, market="pt-BR")]
            )
        )
        return [tool], f"bing_grounding ({bing.name})"
    if web_search:
        loc = m.WebSearchApproximateLocation(country="BR", region="São Paulo", city="Sorocaba")
        return [m.WebSearchTool(user_location=loc, search_context_size="medium")], "web_search"
    return [], "nenhuma"


def update_env(key: str, value: str) -> None:
    env_path = ROOT / ".env"
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    line = f"{key}={value}"
    if re.search(rf"^{key}=.*$", text, flags=re.M):
        text = re.sub(rf"^{key}=.*$", line, text, flags=re.M)
    else:
        text += ("" if text.endswith("\n") or not text else "\n") + line + "\n"
    env_path.write_text(text, encoding="utf-8")


def create_agent(client, model: str, tools: list, tool_label: str):
    from azure.ai.projects.models import PromptAgentDefinition

    instructions = (ROOT / "agent" / "instructions.md").read_text(encoding="utf-8")
    definition = PromptAgentDefinition(model=model, instructions=instructions, tools=tools or None, temperature=0.3)
    return client.agents.create_version(
        agent_name=AGENT_NAME,
        definition=definition,
        description="Prospecção B2B de empresas em Itu, Sorocaba e São Paulo que precisam de parceiro de TI.",
        metadata={"projeto": "Coleta_Leads", "pesquisa_web": tool_label},
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="apenas lista deployments e conexões")
    parser.add_argument("--no-web-search", action="store_true", help="não habilita pesquisa web")
    args = parser.parse_args()

    client = get_client()
    deployments = list_deployments(client)
    print("Deployments disponíveis:")
    for d in deployments:
        print(f"  - {d['name']} ({d['model']} {d['version'] or ''})")
    bing = find_bing_connection(client)
    print(f"Conexão Bing Grounding: {bing.name if bing else 'NÃO encontrada'}")
    if args.list:
        return

    model = choose_deployment(deployments)
    tools, tool_label = build_tools(bing, not args.no_web_search)
    try:
        agent = create_agent(client, model, tools, tool_label)
    except Exception as exc:  # noqa: BLE001
        if not tools:
            raise
        print(f"[aviso] falha ao criar com a ferramenta '{tool_label}': {exc}\nTentando sem pesquisa web...")
        tool_label = "nenhuma"
        agent = create_agent(client, model, [], tool_label)

    print(f"Agente criado/atualizado: {agent.name} v{agent.version} (id: {agent.id})")
    info = {
        "agent_name": agent.name,
        "agent_id": agent.id,
        "agent_version": agent.version,
        "model_deployment": model,
        "pesquisa_web": tool_label,
        "project_endpoint": os.getenv("FOUNDRY_PROJECT_ENDPOINT"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (ROOT / "agent" / "agent_info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    update_env("FOUNDRY_AGENT_NAME", agent.name)
    update_env("FOUNDRY_AGENT_ID", agent.id)
    print(json.dumps(info, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
