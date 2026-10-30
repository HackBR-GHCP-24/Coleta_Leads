"""Dados de exemplo (empresas FICTÍCIAS) para o modo demo/mock."""
from __future__ import annotations

import random

from .config import BASES, SERVICES

# Cidades fictícias de exemplo no raio de atendimento: cidade -> (base mais próxima, km, horas de carro aproximadas)
REGIAO = {
    "Itu": ("Itu", 0, 0.0), "Sorocaba": ("Sorocaba", 0, 0.0), "São Paulo": ("São Paulo", 0, 0.0),
    "Salto": ("Itu", 9, 0.2), "Indaiatuba": ("Itu", 25, 0.4), "Jundiaí": ("Itu", 55, 0.8),
    "Campinas": ("Itu", 60, 0.9), "Piracicaba": ("Itu", 110, 1.5), "Votorantim": ("Sorocaba", 10, 0.3),
    "Tatuí": ("Sorocaba", 55, 0.8), "Itapetininga": ("Sorocaba", 100, 1.3), "Botucatu": ("Sorocaba", 150, 1.9),
    "Barueri": ("São Paulo", 30, 0.6), "Guarulhos": ("São Paulo", 20, 0.5), "Santos": ("São Paulo", 75, 1.2),
    "São José dos Campos": ("São Paulo", 95, 1.3), "Bauru": ("Sorocaba", 250, 3.0), "Ribeirão Preto": ("Itu", 270, 3.3),
}

_PREFIXOS = ["Alfa", "Vértice", "Horizonte", "Nova", "Prisma", "Atlas", "Delta", "Orion", "Sigma", "Âncora",
             "Monte", "Vale", "Ponte", "Rota", "Lumen", "Terra", "Polo", "Fênix", "Cedro", "Aurora"]
_SEGMENTOS = {
    "Indústria metalúrgica": ["Metalúrgica", "Usinagem"],
    "Logística e transportes": ["Logística", "Transportes"],
    "Saúde (clínicas e laboratórios)": ["Clínica", "Diagnósticos"],
    "Educação": ["Colégio", "Faculdade"],
    "Varejo e atacado": ["Atacadista", "Comércio"],
    "Agronegócio": ["Agro", "Cooperativa"],
    "Escritório de contabilidade": ["Contábil", "Assessoria Contábil"],
    "Construção civil": ["Construtora", "Engenharia"],
    "Indústria alimentícia": ["Alimentos", "Laticínios"],
    "Autopeças": ["Autopeças", "Componentes Automotivos"],
}
_SINAIS = [
    ("Vaga aberta para Analista de Suporte N1/N2", ["residentes", "servicos_gerenciados"]),
    ("Anúncio de nova unidade fabril na região", ["infra_cabeada", "redes_fortigate", "windows_ad"]),
    ("Notícia de incidente de ransomware no setor", ["acronis", "servicos_gerenciados"]),
    ("Migração planejada para Microsoft 365", ["modern_work", "windows_ad"]),
    ("Vaga para DBA SQL Server temporário", ["banco_dados"]),
    ("Expansão de filiais com necessidade de VPN/SD-WAN", ["redes_fortigate"]),
    ("Adequação à LGPD e auditoria de backup", ["acronis", "banco_dados"]),
    ("Servidores Windows Server 2012 fora de suporte", ["windows_ad", "servicos_gerenciados"]),
    ("Reforma do escritório e novo layout de rede", ["infra_cabeada"]),
    ("Crescimento de 30% no quadro de funcionários", ["modern_work", "residentes"]),
]
_NECESSIDADES = {
    "acronis": "Backup e recuperação de desastres com proteção contra ransomware",
    "servicos_gerenciados": "Monitoramento 24x7 e gestão de TI com SLA",
    "modern_work": "Colaboração e gestão de identidades com Microsoft 365",
    "residentes": "Suporte presencial ao usuário (N1/N2)",
    "banco_dados": "Gestão, tuning e backup de bancos de dados do ERP",
    "redes_fortigate": "Segurança de perímetro e conectividade entre filiais",
    "windows_ad": "Modernização de servidores e do Active Directory",
    "infra_cabeada": "Infraestrutura de rede estruturada para novas instalações",
}
_CARGOS = ["Gerente de TI", "Diretor Administrativo-Financeiro", "Coordenador de Infraestrutura", "CEO / Sócio"]


def _lead(rng: random.Random, cidade: str | None = None, servicos_foco: list[str] | None = None,
          segmento: str | None = None) -> dict:
    seg = segmento if segmento in _SEGMENTOS else rng.choice(list(_SEGMENTOS))
    cidade = cidade or rng.choice(BASES)
    base, km, horas = REGIAO.get(cidade, (None, None, None))
    nome = f"{rng.choice(_PREFIXOS)} {rng.choice(_SEGMENTOS.get(seg, ['Serviços']))} (exemplo)"
    sinais = rng.sample(_SINAIS, rng.randint(1, 3))
    servicos = {s for _, ss in sinais for s in ss}
    if servicos_foco:
        servicos.update(rng.sample(servicos_foco, min(len(servicos_foco), 2)))
    servicos = [s for s in SERVICES if s in servicos]
    porte = rng.choices(["pequena", "media", "grande"], weights=[3, 4, 2])[0]
    funcs = {"pequena": rng.randint(20, 99), "media": rng.randint(100, 499), "grande": rng.randint(500, 3000)}[porte]
    score = min(98, 35 + 8 * len(servicos) + 6 * len(sinais) + {"pequena": 0, "media": 6, "grande": 10}[porte]
                + (5 if (horas or 0) <= 1 else 0) + rng.randint(-8, 8))
    return {
        "empresa": nome,
        "cnpj": None,
        "site": None,
        "cidade": cidade,
        "uf": "SP",
        "base_proxima": base,
        "distancia_km": km,
        "tempo_viagem_h": horas,
        "segmento": seg,
        "porte": porte,
        "funcionarios_estimados": funcs,
        "necessidades": [_NECESSIDADES[s] for s in servicos],
        "sinais": [{"descricao": d, "fonte": None, "data": None} for d, _ in sinais],
        "servicos_recomendados": servicos,
        "justificativa": (
            f"Empresa de porte {porte} do segmento {seg.lower()} em {cidade}, com sinais de "
            f"{', '.join(d.lower() for d, _ in sinais)}. Alta aderência a {len(servicos)} serviço(s) do portfólio."
        ),
        "score_oportunidade": max(score, 20),
        "nivel_confianca": "baixa",
        "contato_sugerido": {"cargo": rng.choice(_CARGOS), "canal": "LinkedIn da empresa / site institucional"},
        "fontes": [],
        "observacao": "Dado fictício gerado no modo demonstração.",
    }


def generate(bases: list[str] | None = None, services: list[str] | None = None, segment: str | None = None,
             quantity: int = 5, seed: int | None = None, cities: list[str] | None = None,
             max_hours: float | None = None) -> dict:
    rng = random.Random(seed)
    bases = bases or BASES
    if not cities:
        cities = [c for c, (b, _, h) in REGIAO.items() if b in bases and (max_hours is None or h <= max_hours)] or bases
    leads = [_lead(rng, rng.choice(cities), services, segment) for _ in range(quantity)]
    leads.sort(key=lambda x: x["score_oportunidade"], reverse=True)
    return {"leads": leads, "observacoes": "Modo demonstração: empresas fictícias geradas localmente."}
