"""Consultas de leitura para o resumo semanal (bloco 2.6).

Fonte da verdade: docs/ESPECIFICACAO.md ("`mencoes`", "Avaliações críticas" e a linha de cobertura).
"""

import json
from collections import Counter
from datetime import datetime, timezone

from app.ranking import ContagemSemana

NOTA_NEGATIVA_MAXIMA = 2  # negativa = nota <= 2 (decisão de 31/10/2026 sobre o ranking)


def _utc_texto(instante: datetime, nome: str) -> str:
    if not isinstance(instante, datetime) or instante.utcoffset() is None:
        raise ValueError(f"{nome} precisa ter fuso")
    return instante.astimezone(timezone.utc).isoformat()


def contar_semana(conn, inicio_utc: datetime, fim_utc: datetime) -> ContagemSemana:
    """Contagens das avaliações com inicio <= recebida_em < fim.

    recebida_em está gravado em UTC com isoformat(); os limites são convertidos para o mesmo formato,
    e a comparação por texto segue a ordem dos instantes (ver app/semana.py). Uma avaliação conta uma vez
    por categoria; as críticas contam normalmente nas suas categorias operacionais.
    """
    inicio, fim = _utc_texto(inicio_utc, "inicio_utc"), _utc_texto(fim_utc, "fim_utc")
    if inicio_utc >= fim_utc:
        raise ValueError("o intervalo precisa ter início antes do fim")
    linhas = conn.execute(
        "SELECT categorias, nota, status_classificacao FROM avaliacoes "
        "WHERE recebida_em >= ? AND recebida_em < ?",
        (inicio, fim),
    ).fetchall()
    mencoes = Counter()
    invalidas = negativas = 0
    for categorias, nota, status in linhas:
        mencoes.update(set(json.loads(categorias)))
        invalidas += status == "invalida"
        negativas += nota <= NOTA_NEGATIVA_MAXIMA
    return ContagemSemana(total=len(linhas), mencoes=dict(mencoes), invalidas=invalidas, negativas=negativas)
