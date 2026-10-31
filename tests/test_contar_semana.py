"""Testes da contagem da semana no banco (bloco 2.6), com dados montados à mão.

Fonte da verdade: docs/ESPECIFICACAO.md ("`mencoes`", "Avaliações críticas", linha de cobertura).
"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import banco, consultas, semana

SP = ZoneInfo("America/Sao_Paulo")
SEGUNDA = datetime(2026, 10, 26, tzinfo=SP)
UM_MICRO = timedelta(microseconds=1)


@pytest.fixture
def conn(tmp_path):
    with closing(banco.abrir(tmp_path / "radar.db")) as c:
        yield c


_n = 0


def _grava(conn, quando, nota=1, categorias=(), criticidade=(), status="ok"):
    global _n
    _n += 1
    banco.gravar_avaliacao(
        conn, id_externo=f"ext-{_n}", texto="texto", nota=nota, recebida_em=quando.isoformat(),
        categorias=list(categorias), criticidade=list(criticidade), status_classificacao=status,
    )


def _contar(conn, inicio_local=SEGUNDA):
    return consultas.contar_semana(conn, *semana.intervalo_utc(inicio_local))


def test_contagem_montada_a_mao(conn):
    d = SEGUNDA + timedelta(hours=12)
    _grava(conn, d, nota=1, categorias=["atraso", "embalagem"])
    _grava(conn, d, nota=2, categorias=["atraso"])
    _grava(conn, d, nota=3, categorias=["entregador"])
    _grava(conn, d, nota=5)  # positiva, sem categoria
    _grava(conn, d, nota=4)
    _grava(conn, d, nota=1, status="invalida")
    _grava(conn, d, nota=1, categorias=["item_faltando"], criticidade=["alergia"])  # crítica
    c = _contar(conn)
    assert c.total == 7
    assert c.mencoes == {"atraso": 2, "embalagem": 1, "entregador": 1, "item_faltando": 1}
    assert c.invalidas == 1
    assert c.negativas == 4  # notas 1, 2, 1 (inválida) e 1 (crítica)


def test_negativa_e_nota_menor_ou_igual_a_2(conn):
    d = SEGUNDA + timedelta(days=1)
    for nota in (0, 1, 2, 3, 4, 5):
        _grava(conn, d, nota=nota)
    assert _contar(conn).negativas == 3


def test_avaliacao_critica_conta_nas_mencoes_das_categorias_operacionais(conn):
    d = SEGUNDA + timedelta(days=2)
    _grava(conn, d, categorias=["qualidade_temperatura"], criticidade=["saude"])
    _grava(conn, d, categorias=["qualidade_temperatura"])
    _grava(conn, d, categorias=[], criticidade=["cobranca_indevida"])
    c = _contar(conn)
    assert c.mencoes == {"qualidade_temperatura": 2}
    assert c.total == 3


def test_uma_avaliacao_conta_uma_vez_por_categoria(conn):
    _grava(conn, SEGUNDA + timedelta(hours=1), categorias=["atraso", "embalagem", "atendimento"])
    assert _contar(conn).mencoes == {"atraso": 1, "embalagem": 1, "atendimento": 1}


def test_categoria_repetida_gravada_por_fora_conta_uma_vez(conn):
    # a camada do banco já recusa repetição; aqui a linha é escrita por SQL direto para provar a regra
    _grava(conn, SEGUNDA + timedelta(hours=1), categorias=["atraso"])
    conn.execute("UPDATE avaliacoes SET categorias = ?", ('["atraso", "atraso"]',))
    assert _contar(conn).mencoes == {"atraso": 1}


def test_intervalo_invertido_ou_vazio_e_recusado(conn):
    inicio, fim = semana.intervalo_utc(SEGUNDA)
    with pytest.raises(ValueError):
        consultas.contar_semana(conn, fim, inicio)
    with pytest.raises(ValueError):
        consultas.contar_semana(conn, inicio, inicio)


def test_fronteiras_da_semana_no_banco(conn):
    _grava(conn, SEGUNDA, categorias=["atraso"])  # segunda 00:00 -03:00: semana nova
    _grava(conn, SEGUNDA - UM_MICRO, categorias=["embalagem"])  # um microssegundo antes: anterior
    _grava(conn, SEGUNDA + UM_MICRO, categorias=["atendimento"])
    proxima = SEGUNDA + timedelta(days=7)
    _grava(conn, proxima - UM_MICRO, categorias=["item_faltando"])  # último instante da semana
    _grava(conn, proxima, categorias=["entregador"])  # já é a próxima
    # domingo 23:30 local escrito em UTC (segunda 02:30Z): semana anterior
    _grava(conn, datetime(2026, 10, 26, 2, 30, tzinfo=timezone.utc), categorias=["qualidade_temperatura"])
    atual = _contar(conn)
    assert atual.mencoes == {"atraso": 1, "atendimento": 1, "item_faltando": 1}
    assert atual.total == 3
    anterior = _contar(conn, SEGUNDA - timedelta(days=7))
    assert anterior.mencoes == {"embalagem": 1, "qualidade_temperatura": 1}
    assert _contar(conn, proxima).mencoes == {"entregador": 1}


def test_semana_sem_avaliacoes(conn):
    c = _contar(conn)
    assert (c.total, c.mencoes, c.invalidas, c.negativas) == (0, {}, 0, 0)


def test_intervalo_precisa_de_fuso(conn):
    with pytest.raises(ValueError):
        consultas.contar_semana(conn, datetime(2026, 10, 26, 3), datetime(2026, 11, 2, 3))
