"""Semana do resumo (bloco 2.6).

Fonte da verdade: docs/ESPECIFICACAO.md ("`mencoes`"): a semana vai de segunda 00:00 (inclusive) até a
segunda seguinte 00:00 (exclusive), no fuso America/Sao_Paulo, pelo campo recebida_em. O resumo de
segunda às 8h cobre a semana que terminou.

O banco grava as datas em UTC com isoformat() (+00:00). Os limites devolvidos aqui também são UTC; com
os dois lados no mesmo formato, a comparação por texto no SQLite segue a ordem dos instantes, inclusive
com microssegundos (".ffffff" vem depois de "+00:00" na ordem dos caracteres, como deve).
"""

from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")


def _agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def _com_fuso(instante: datetime) -> datetime:
    if not isinstance(instante, datetime) or instante.utcoffset() is None:
        raise ValueError("o instante precisa ter fuso")
    return instante


def inicio_da_semana(instante: datetime) -> datetime:
    """Segunda-feira 00:00, em America/Sao_Paulo, da semana que contém o instante."""
    local = _com_fuso(instante).astimezone(FUSO)
    segunda = local.date() - timedelta(days=local.weekday())
    return datetime.combine(segunda, time(0), FUSO)


def intervalo_utc(instante: datetime) -> tuple:
    """(início, fim) da semana do instante, em UTC: [segunda 00:00, segunda seguinte 00:00)."""
    inicio = inicio_da_semana(instante)
    fim = datetime.combine(inicio.date() + timedelta(days=7), time(0), FUSO)  # por data: imune a fuso variável
    return inicio.astimezone(timezone.utc), fim.astimezone(timezone.utc)


def ultima_semana_completa(agora: Optional[datetime] = None, *, relogio=_agora_utc) -> datetime:
    """Início da semana que terminou na última segunda 00:00 (a do resumo de segunda às 8h)."""
    atual = inicio_da_semana(agora if agora is not None else relogio())
    return datetime.combine(atual.date() - timedelta(days=7), time(0), FUSO)
