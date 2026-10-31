"""Testes da semana (bloco 2.6).

Fonte da verdade: docs/ESPECIFICACAO.md ("`mencoes`": a semana é de segunda 00:00, inclusive, até a
segunda seguinte 00:00, exclusive, em America/Sao_Paulo; o resumo de segunda às 8h cobre a semana que
terminou).
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import semana

SP = ZoneInfo("America/Sao_Paulo")
MENOS3 = timezone(timedelta(hours=-3))
UM_MICRO = timedelta(microseconds=1)


def _local(*args):
    return datetime(*args, tzinfo=SP)


# ---------- início da semana ----------

def test_segunda_00h_menos3_pertence_a_semana_nova():
    assert semana.inicio_da_semana(datetime(2026, 10, 26, 0, 0, tzinfo=MENOS3)) == _local(2026, 10, 26)


def test_um_microssegundo_antes_pertence_a_semana_anterior():
    instante = datetime(2026, 10, 26, 0, 0, tzinfo=MENOS3) - UM_MICRO
    assert semana.inicio_da_semana(instante) == _local(2026, 10, 19)


def test_instante_em_utc_que_cai_em_outro_dia_local():
    # segunda 02:00 UTC = domingo 23:00 em São Paulo: semana anterior
    assert semana.inicio_da_semana(datetime(2026, 10, 26, 2, 0, tzinfo=timezone.utc)) == _local(2026, 10, 19)
    # segunda 03:00 UTC = segunda 00:00 em São Paulo: semana nova
    assert semana.inicio_da_semana(datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)) == _local(2026, 10, 26)


def test_meio_da_semana_e_domingo_a_noite():
    assert semana.inicio_da_semana(_local(2026, 10, 29, 15, 30)) == _local(2026, 10, 26)
    assert semana.inicio_da_semana(_local(2026, 11, 1, 23, 59, 59, 999999)) == _local(2026, 10, 26)


def test_instante_sem_fuso_e_recusado():
    with pytest.raises(ValueError):
        semana.inicio_da_semana(datetime(2026, 10, 26, 12, 0))


# ---------- intervalo UTC ----------

def test_intervalo_utc_da_semana():
    inicio, fim = semana.intervalo_utc(_local(2026, 10, 26))
    assert inicio == datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)
    assert fim == datetime(2026, 11, 2, 3, 0, tzinfo=timezone.utc)
    assert inicio.utcoffset() == timedelta(0) and fim.utcoffset() == timedelta(0)


def test_intervalo_utc_aceita_qualquer_instante_da_semana():
    assert semana.intervalo_utc(_local(2026, 10, 29, 15, 0)) == semana.intervalo_utc(_local(2026, 10, 26))


def test_semanas_consecutivas_se_encostam_sem_buraco_nem_sobreposicao():
    _, fim = semana.intervalo_utc(_local(2026, 10, 19))
    inicio, _ = semana.intervalo_utc(_local(2026, 10, 26))
    assert fim == inicio


def test_texto_utc_do_banco_compara_certo_inclusive_com_microssegundos():
    """O banco grava isoformat() em UTC. A comparação por texto precisa bater com a de instantes."""
    inicio, fim = semana.intervalo_utc(_local(2026, 10, 26))
    li, lf = inicio.isoformat(), fim.isoformat()
    casos = [
        (inicio, True),
        (inicio - UM_MICRO, False),
        (inicio + UM_MICRO, True),
        (fim - UM_MICRO, True),
        (fim, False),
        (fim + UM_MICRO, False),
        (inicio + timedelta(days=3, hours=5, microseconds=123456), True),
    ]
    for instante, dentro in casos:
        texto = instante.astimezone(timezone.utc).isoformat()
        assert (li <= texto < lf) is dentro, texto
        assert (inicio <= instante < fim) is dentro


# ---------- última semana completa ----------

@pytest.mark.parametrize("agora", [
    _local(2026, 10, 26, 7, 59),  # segunda às 07:59
    _local(2026, 10, 26, 8, 0),  # segunda às 08:00 (hora do resumo)
    _local(2026, 10, 26, 0, 0),  # exatamente segunda 00:00
    _local(2026, 11, 1, 12, 0),  # domingo
    _local(2026, 11, 1, 23, 59, 59, 999999),  # fim do domingo
])
def test_ultima_semana_completa(agora):
    assert semana.ultima_semana_completa(agora) == _local(2026, 10, 19)


def test_ultima_semana_completa_no_domingo_antes_da_segunda_00h_ainda_e_a_anterior():
    domingo = _local(2026, 10, 25, 23, 59, 59, 999999)
    assert semana.ultima_semana_completa(domingo) == _local(2026, 10, 12)


def test_ultima_semana_completa_com_agora_em_utc():
    # segunda 10:59 UTC = segunda 07:59 em São Paulo
    assert semana.ultima_semana_completa(datetime(2026, 10, 26, 10, 59, tzinfo=timezone.utc)) == _local(2026, 10, 19)


def test_relogio_injetavel():
    assert semana.ultima_semana_completa(relogio=lambda: _local(2026, 10, 26, 8, 0)) == _local(2026, 10, 19)


def test_inicio_da_semana_e_sempre_segunda_00h_em_sao_paulo():
    for dias in range(0, 400, 13):
        instante = _local(2026, 1, 1, 12) + timedelta(days=dias, minutes=dias * 7)
        inicio = semana.inicio_da_semana(instante)
        assert inicio.weekday() == 0
        assert (inicio.hour, inicio.minute, inicio.second, inicio.microsecond) == (0, 0, 0, 0)
        assert inicio <= instante < inicio + timedelta(days=7)
