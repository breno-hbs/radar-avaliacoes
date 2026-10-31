"""Ponta a ponta do ranking com as 85 avaliações (bloco 2.6). Dados SIMULADOS.

Sem chamar a API: a "classificação" é o gabarito de data/*.json, gravado direto no banco. As datas são as
do simulador (semente 42, 4 semanas a partir de 05/10/2026). O resultado esperado foi calculado à mão a
partir das contagens por semana abaixo, contadas do gabarito, e está escrito literalmente.

Contagens (total; menções; negativas = nota <= 2):
  semana 1 (05/10): 21; atendimento 4, atraso 4, embalagem 1, entregador 3, item_faltando 4, qt 2; neg 12
  semana 2 (12/10): 21; atendimento 1, atraso 3, embalagem 2, entregador 3, item_faltando 3, qt 4; neg 14
  semana 3 (19/10): 21; atendimento 4, atraso 4, embalagem 5, entregador 2, item_faltando 6, qt 6; neg 15
  semana 4 (26/10): 22; atendimento 2, atraso 4, embalagem 5, entregador 2, item_faltando 6, qt 1; neg 14
"""

import json
import sys
from contextlib import closing
from datetime import date, datetime, timedelta
from fractions import Fraction as F
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))

import simulador  # noqa: E402

from app import banco, config_ranking, consultas, ranking, semana  # noqa: E402

SP = ZoneInfo("America/Sao_Paulo")
SEGUNDAS = [datetime(2026, 10, 5, tzinfo=SP) + timedelta(weeks=i) for i in range(4)]


@pytest.fixture(scope="module")
def contagens(tmp_path_factory):
    gabarito = {}
    for nome in simulador.ARQUIVOS:
        for item in json.loads((RAIZ / "data" / nome).read_text(encoding="utf-8")):
            gabarito[item["id_externo"]] = item
    cronograma = simulador.montar_cronograma(
        simulador.carregar_avaliacoes(), semente=42, semanas=4, semana_inicio=date(2026, 10, 5)
    )
    caminho = tmp_path_factory.mktemp("ponta") / "radar.db"
    with closing(banco.abrir(caminho)) as conn:
        for corpo in cronograma:
            g = gabarito[corpo["id_externo"]]
            banco.gravar_avaliacao(
                conn, id_externo=corpo["id_externo"], texto=corpo["texto"], nota=corpo["nota"],
                recebida_em=corpo["recebida_em"], categorias=g["categorias"], criticidade=g["criticidade"],
                status_classificacao="ok",
            )
        return [consultas.contar_semana(conn, *semana.intervalo_utc(s)) for s in SEGUNDAS]


@pytest.fixture(scope="module")
def cfg():
    return config_ranking.carregar_config()


def _resumo(r):
    return (
        [(c.categoria, c.mencoes, c.fator_tendencia, c.pontuacao) for c in r.cards],
        [(c.categoria, c.pontuacao) for c in r.outros_temas],
        [(t.categoria, t.mencoes) for t in r.temas_sem_acao],
        r.tendencia_calculada,
    )


def test_contagens_por_semana_conferidas_por_fora_de_semana_e_consultas(contagens):
    """Recontagem independente: dia local do recebida_em (o simulador gera 11:00-22:59, longe da
    meia-noite) e o gabarito, sem usar app/semana.py nem app/consultas.py."""
    from collections import Counter

    gabarito = {}
    for nome in simulador.ARQUIVOS:
        for item in json.loads((RAIZ / "data" / nome).read_text(encoding="utf-8")):
            gabarito[item["id_externo"]] = item
    cronograma = simulador.montar_cronograma(
        simulador.carregar_avaliacoes(), semente=42, semanas=4, semana_inicio=date(2026, 10, 5)
    )
    por_semana = [Counter() for _ in range(4)]
    totais = [0] * 4
    for corpo in cronograma:
        indice = (datetime.fromisoformat(corpo["recebida_em"]).date() - date(2026, 10, 5)).days // 7
        totais[indice] += 1
        por_semana[indice].update(set(gabarito[corpo["id_externo"]]["categorias"]))
    assert totais == [c.total for c in contagens]
    assert [dict(m) for m in por_semana] == [c.mencoes for c in contagens]
    # e os valores literais do docstring
    assert por_semana[0] == {"atendimento": 4, "atraso": 4, "embalagem": 1, "entregador": 3,
                             "item_faltando": 4, "qualidade_temperatura": 2}
    assert por_semana[1] == {"atendimento": 1, "atraso": 3, "embalagem": 2, "entregador": 3,
                             "item_faltando": 3, "qualidade_temperatura": 4}
    assert por_semana[2] == {"atendimento": 4, "atraso": 4, "embalagem": 5, "entregador": 2,
                             "item_faltando": 6, "qualidade_temperatura": 6}


def test_contagens_por_semana_batem_com_o_gabarito(contagens):
    assert [c.total for c in contagens] == [21, 21, 21, 22]
    assert sum(c.total for c in contagens) == 85
    assert [c.negativas for c in contagens] == [12, 14, 15, 14]
    assert [c.invalidas for c in contagens] == [0, 0, 0, 0]
    assert contagens[3].mencoes == {
        "atendimento": 2, "atraso": 4, "embalagem": 5, "entregador": 2, "item_faltando": 6,
        "qualidade_temperatura": 1,
    }


def test_semana_1_sem_anterior(contagens, cfg):
    r = ranking.calcular_ranking(contagens[0], None, cfg)
    # item_faltando 4×3 = 12; atendimento e atraso 4×2 = 8 (empate total: alfabética)
    assert _resumo(r) == (
        [("item_faltando", 4, 1, 12), ("atendimento", 4, 1, 8), ("atraso", 4, 1, 8)],
        [("qualidade_temperatura", 6), ("embalagem", 2)],
        [("entregador", 3)],
        False,
    )


def test_semana_2_contra_a_1(contagens, cfg):
    r = ranking.calcular_ranking(contagens[1], contagens[0], cfg)
    # qt subiu (4/21 > 2/21): 4×3×5/4 = 15; embalagem subiu (2 > 1): 2×2×5/4 = 5
    assert _resumo(r) == (
        [("qualidade_temperatura", 4, F(5, 4), 15), ("item_faltando", 3, 1, 9), ("atraso", 3, 1, 6)],
        [("embalagem", 5), ("atendimento", 2)],
        [("entregador", 3)],
        True,
    )


def test_semana_3_contra_a_2_tendencia_e_desempates(contagens, cfg):
    r = ranking.calcular_ranking(contagens[2], contagens[1], cfg)
    # tudo subiu (21 contra 21): qt e item_faltando 6×3×5/4 = 45/2, empate em gravidade e menções
    # (alfabética); embalagem 5×2×5/4 = 25/2; atendimento e atraso 4×2×5/4 = 10 (alfabética)
    assert _resumo(r) == (
        [("item_faltando", 6, F(5, 4), F(45, 2)), ("qualidade_temperatura", 6, F(5, 4), F(45, 2)),
         ("embalagem", 5, F(5, 4), F(25, 2))],
        [("atendimento", 10), ("atraso", 10)],
        [("entregador", 2)],
        True,
    )


def test_semana_4_contra_a_3_a_do_resumo(contagens, cfg):
    # o resumo de segunda 02/11 às 8h cobre a semana de 26/10
    assert semana.ultima_semana_completa(datetime(2026, 11, 2, 8, 0, tzinfo=SP)) == SEGUNDAS[3]
    r = ranking.calcular_ranking(contagens[3], contagens[2], cfg)
    # 22 contra 21 avaliações: mesmas menções com total maior é taxa MENOR (6/22 < 6/21), sem fator
    assert _resumo(r) == (
        [("item_faltando", 6, 1, 18), ("embalagem", 5, 1, 10), ("atraso", 4, 1, 8)],
        [("atendimento", 4), ("qualidade_temperatura", 3)],
        [("entregador", 2)],
        True,
    )
    assert r.cards[0].taxa_por_100 == F(600, 22)
