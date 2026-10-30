"""Teste rápido do agente no Foundry: envia um pedido de prospecção e imprime os leads.

Uso: python agent/test_agent.py Sorocaba 3
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.foundry_client import run_agent  # noqa: E402

if __name__ == "__main__":
    city = sys.argv[1] if len(sys.argv) > 1 else "Sorocaba"
    qty = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    result = run_agent(cities=[city], services=[], segment=None, quantity=qty)
    print(json.dumps(result, indent=2, ensure_ascii=False))
