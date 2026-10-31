"""Ranking dos cards semanais (bloco 2.6). Lógica pura, sem banco.

Fonte da verdade: docs/ESPECIFICACAO.md ("Regras de negócio" e as decisões de 31/10/2026 sobre o
ranking). Os pesos, o fator de tendência e a amostra mínima vêm da configuração (app/config_ranking.py),
nunca deste arquivo. Todo número é inteiro ou Fraction: nada passa por float.
"""

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Mapping, Optional

from app import contrato

MAX_CARDS = 3  # "O dono vê no máximo 3 cards por semana"


@dataclass(frozen=True)
class ContagemSemana:
    total: int  # todas as avaliações recebidas na semana, inclusive positivas e inválidas
    mencoes: Mapping[str, int] = field(default_factory=dict)  # categoria -> avaliações com ela
    invalidas: int = 0
    negativas: int = 0


@dataclass(frozen=True)
class Card:
    categoria: str
    mencoes: int
    taxa_por_100: Fraction
    gravidade: int
    fator_tendencia: Fraction
    pontuacao: Fraction


@dataclass(frozen=True)
class TemaSemAcao:
    categoria: str
    mencoes: int


@dataclass(frozen=True)
class ResultadoRanking:
    cards: list
    outros_temas: list
    temas_sem_acao: list
    tendencia_calculada: bool
    total: int
    invalidas: int
    negativas: int


def _validar(contagem: ContagemSemana, nome: str) -> None:
    def inteiro(v):
        return isinstance(v, int) and not isinstance(v, bool) and v >= 0

    if not inteiro(contagem.total):
        raise ValueError(f"{nome}: total deve ser inteiro não negativo")
    for campo in ("invalidas", "negativas"):
        valor = getattr(contagem, campo)
        if not inteiro(valor) or valor > contagem.total:
            raise ValueError(f"{nome}: {campo} deve ser inteiro entre 0 e o total")
    for categoria, n in contagem.mencoes.items():
        if categoria not in contrato.CATEGORIAS:
            raise ValueError(f"{nome}: categoria desconhecida: {categoria}")
        if not inteiro(n) or n > contagem.total:
            raise ValueError(f"{nome}: menções de {categoria} devem estar entre 0 e o total")


def _subiu(m_atual: int, t_atual: int, m_anterior: int, t_anterior: int) -> bool:
    """m_atual/t_atual > m_anterior/t_anterior, estrito, por multiplicação cruzada de inteiros."""
    return m_atual * t_anterior > m_anterior * t_atual


def calcular_ranking(atual: ContagemSemana, anterior: Optional[ContagemSemana], config) -> ResultadoRanking:
    _validar(atual, "semana atual")
    if anterior is not None:
        _validar(anterior, "semana anterior")

    n = config.amostra_minima
    tendencia_calculada = anterior is not None and atual.total >= n and anterior.total >= n

    candidatos = []
    for categoria, gravidade in config.pesos.items():
        mencoes = atual.mencoes.get(categoria, 0)
        if mencoes == 0:
            continue
        fator = Fraction(1)
        if tendencia_calculada and _subiu(
            mencoes, atual.total, anterior.mencoes.get(categoria, 0), anterior.total
        ):
            fator = config.fator_tendencia
        candidatos.append(Card(
            categoria=categoria,
            mencoes=mencoes,
            taxa_por_100=Fraction(100 * mencoes, atual.total),
            gravidade=gravidade,
            fator_tendencia=fator,
            pontuacao=mencoes * gravidade * fator,
        ))

    # Maior pontuação; desempate: maior gravidade, mais menções, identificador em ordem alfabética.
    candidatos.sort(key=lambda c: (-c.pontuacao, -c.gravidade, -c.mencoes, c.categoria))
    return ResultadoRanking(
        cards=candidatos[:MAX_CARDS],
        outros_temas=candidatos[MAX_CARDS:],
        temas_sem_acao=[TemaSemAcao(c, atual.mencoes.get(c, 0)) for c in config.fora_do_ranking],
        tendencia_calculada=tendencia_calculada,
        total=atual.total,
        invalidas=atual.invalidas,
        negativas=atual.negativas,
    )
