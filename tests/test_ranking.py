"""Testes do cálculo do ranking dos cards semanais (bloco 2.6). Lógica pura, sem banco.

Fonte da verdade: docs/ESPECIFICACAO.md ("Regras de negócio" e as duas decisões de 31/10/2026 sobre
o ranking). Um teste por regra.
"""

import copy
import random
from decimal import Decimal
from fractions import Fraction

import pytest

from app import config_ranking, ranking
from app.ranking import ContagemSemana

PADRAO = {
    "pesos": {
        "qualidade_temperatura": 3, "item_faltando": 3, "atraso": 2, "embalagem": 2, "atendimento": 2,
        "entregador": None,
    },
    "fator_tendencia": Decimal("1.25"),  # como o JSON chega (parse_float=Decimal)
    "amostra_minima": 20,
}


@pytest.fixture
def cfg():
    return config_ranking.validar_config(copy.deepcopy(PADRAO))


def C(total, invalidas=0, negativas=0, **mencoes):
    return ContagemSemana(total=total, mencoes=mencoes, invalidas=invalidas, negativas=negativas)


def _ordem(resultado):
    return [c.categoria for c in resultado.cards]


def _card(resultado, categoria):
    return next(c for c in resultado.cards + resultado.outros_temas if c.categoria == categoria)


# ---------- pontuação e ordem ----------

def test_ordem_por_pontuacao(cfg):
    r = ranking.calcular_ranking(C(30, atraso=5, item_faltando=2, embalagem=4), None, cfg)
    # atraso 5×2=10, embalagem 4×2=8, item_faltando 2×3=6
    assert _ordem(r) == ["atraso", "embalagem", "item_faltando"]
    assert [c.pontuacao for c in r.cards] == [10, 8, 6]


def test_campos_do_card_e_tipos_exatos(cfg):
    r = ranking.calcular_ranking(C(30, atraso=5), None, cfg)
    card = r.cards[0]
    assert (card.categoria, card.mencoes, card.gravidade) == ("atraso", 5, 2)
    assert card.taxa_por_100 == Fraction(50, 3)
    assert card.fator_tendencia == 1 and card.pontuacao == 10
    for valor in (card.taxa_por_100, card.fator_tendencia, card.pontuacao):
        assert isinstance(valor, Fraction)


def test_pontuacao_com_fator_e_fracao_exata(cfg):
    r = ranking.calcular_ranking(C(20, atraso=3), C(20, atraso=1), cfg)
    assert _card(r, "atraso").pontuacao == Fraction(15, 2)  # 3 × 2 × 5/4
    assert not isinstance(_card(r, "atraso").pontuacao, float)


# ---------- desempate ----------

def test_desempate_primeiro_por_maior_gravidade(cfg):
    # item_faltando 2×3 = 6 e atraso 3×2 = 6: empate, vence a gravidade 3
    r = ranking.calcular_ranking(C(30, atraso=3, item_faltando=2), None, cfg)
    assert _ordem(r) == ["item_faltando", "atraso"]


def test_desempate_depois_por_mais_mencoes():
    # mesma gravidade e mesma pontuação só com mesmas menções; para empatar com menções diferentes,
    # usa-se o fator: atraso 4×2×5/4 = 10 e embalagem 5×2×1 = 10
    cfg = config_ranking.validar_config(copy.deepcopy(PADRAO))
    r = ranking.calcular_ranking(C(40, atraso=4, embalagem=5), C(40, atraso=1, embalagem=5), cfg)
    assert _card(r, "atraso").pontuacao == _card(r, "embalagem").pontuacao == 10
    assert _ordem(r) == ["embalagem", "atraso"]


def test_desempate_por_ultimo_pelo_identificador_em_ordem_alfabetica(cfg):
    r = ranking.calcular_ranking(C(30, embalagem=2, atendimento=2, atraso=2), None, cfg)
    assert _ordem(r) == ["atendimento", "atraso", "embalagem"]
    r = ranking.calcular_ranking(C(30, qualidade_temperatura=1, item_faltando=1), None, cfg)
    assert _ordem(r) == ["item_faltando", "qualidade_temperatura"]


def test_peso_2_em_alta_perde_para_peso_3_sem_alta(cfg):
    # mesmas menções: atraso 2 × 1,25 = 2,5 por menção, item_faltando 3 × 1 = 3
    r = ranking.calcular_ranking(C(20, atraso=4, item_faltando=4), C(20, atraso=1, item_faltando=4), cfg)
    assert _card(r, "atraso").fator_tendencia == Fraction(5, 4)
    assert _card(r, "item_faltando").fator_tendencia == 1
    assert _ordem(r) == ["item_faltando", "atraso"]


# ---------- tendência ----------

def test_taxa_igual_nao_e_aumento(cfg):
    # 2/20 = 10% e 4/40 = 10%: mais menções absolutas, mesma taxa
    r = ranking.calcular_ranking(C(40, atraso=4), C(20, atraso=2), cfg)
    assert r.tendencia_calculada is True
    assert _card(r, "atraso").fator_tendencia == 1


def test_diferenca_minima_e_aumento(cfg):
    # 400/1000 = 40% contra 399/999 ≈ 39,94%: aumento mínimo, conta
    r = ranking.calcular_ranking(C(1000, atraso=400), C(999, atraso=399), cfg)
    assert _card(r, "atraso").fator_tendencia == Fraction(5, 4)
    # 401/1001 ≈ 40,06% contra 400/1000 = 40%
    r = ranking.calcular_ranking(C(1001, atraso=401), C(1000, atraso=400), cfg)
    assert _card(r, "atraso").fator_tendencia == Fraction(5, 4)


def test_taxa_menor_nao_e_aumento(cfg):
    r = ranking.calcular_ranking(C(20, atraso=1), C(20, atraso=2), cfg)
    assert _card(r, "atraso").fator_tendencia == 1


def test_mais_mencoes_com_taxa_menor_nao_e_aumento(cfg):
    r = ranking.calcular_ranking(C(100, atraso=5), C(20, atraso=2), cfg)  # 5% contra 10%
    assert _card(r, "atraso").fator_tendencia == 1


@pytest.mark.parametrize("total_atual,total_anterior,calculada", [
    (20, 20, True),
    (19, 20, False),
    (20, 19, False),
    (19, 19, False),
    (21, 25, True),
])
def test_amostra_minima_nas_duas_semanas(cfg, total_atual, total_anterior, calculada):
    r = ranking.calcular_ranking(C(total_atual, atraso=5), C(total_anterior, atraso=1), cfg)
    assert r.tendencia_calculada is calculada
    assert _card(r, "atraso").fator_tendencia == (Fraction(5, 4) if calculada else 1)
    # os cards continuam saindo por menções × gravidade
    assert _card(r, "atraso").pontuacao == 5 * 2 * (Fraction(5, 4) if calculada else 1)


def test_anterior_com_amostra_suficiente_e_taxa_0_com_aumento_vale_o_fator(cfg):
    r = ranking.calcular_ranking(C(20, atraso=1), C(20), cfg)
    assert r.tendencia_calculada is True
    assert _card(r, "atraso").fator_tendencia == Fraction(5, 4)


def test_anterior_inexistente_vale_1_e_tendencia_nao_calculada(cfg):
    r = ranking.calcular_ranking(C(50, atraso=5), None, cfg)
    assert r.tendencia_calculada is False
    assert _card(r, "atraso").fator_tendencia == 1


def test_categoria_ausente_na_anterior_conta_como_zero(cfg):
    r = ranking.calcular_ranking(C(20, embalagem=1), C(20, atraso=3), cfg)
    assert _card(r, "embalagem").fator_tendencia == Fraction(5, 4)


# ---------- entregador, corte em 3, zero menções ----------

def test_entregador_fora_dos_cards_e_em_temas_sem_acao(cfg):
    r = ranking.calcular_ranking(C(30, entregador=20, atraso=1), None, cfg)
    assert "entregador" not in _ordem(r)
    assert "entregador" not in [c.categoria for c in r.outros_temas]
    assert r.temas_sem_acao == [ranking.TemaSemAcao("entregador", 20)]
    assert _ordem(r) == ["atraso"]


def test_temas_sem_acao_aparece_mesmo_com_zero_mencoes(cfg):
    r = ranking.calcular_ranking(C(30, atraso=1), None, cfg)
    assert r.temas_sem_acao == [ranking.TemaSemAcao("entregador", 0)]


def test_corte_em_3_cards_e_o_resto_em_outros_temas(cfg):
    r = ranking.calcular_ranking(
        C(50, qualidade_temperatura=5, item_faltando=4, atraso=6, embalagem=1, atendimento=2), None, cfg
    )
    # qt 15, atraso 12, if 12 (if vence por gravidade), atendimento 4, embalagem 2
    assert _ordem(r) == ["qualidade_temperatura", "item_faltando", "atraso"]
    assert [c.categoria for c in r.outros_temas] == ["atendimento", "embalagem"]


def test_categoria_com_0_mencoes_nao_gera_card_nem_outro_tema(cfg):
    r = ranking.calcular_ranking(C(30, atraso=2, embalagem=0), None, cfg)
    assert _ordem(r) == ["atraso"]
    assert r.outros_temas == []


def test_semana_sem_mencoes_nao_tem_cards(cfg):
    r = ranking.calcular_ranking(C(25), C(25, atraso=4), cfg)
    assert r.cards == [] and r.outros_temas == []


def test_semana_vazia(cfg):
    r = ranking.calcular_ranking(C(0), None, cfg)
    assert r.cards == [] and r.tendencia_calculada is False and r.total == 0


# ---------- denominador e cobertura ----------

def test_denominador_inclui_positivas_sem_categoria_e_invalidas(cfg):
    # 40 avaliações: 4 com atraso, 30 positivas sem categoria e 6 inválidas: taxa = 4/40, não 4/4
    r = ranking.calcular_ranking(C(40, invalidas=6, negativas=4, atraso=4), None, cfg)
    assert _card(r, "atraso").taxa_por_100 == 10
    assert (r.total, r.invalidas, r.negativas) == (40, 6, 4)


def test_denominador_muda_a_tendencia(cfg):
    # 4 menções nas duas semanas; a atual tem mais positivas: taxa cai, sem fator
    r = ranking.calcular_ranking(C(40, atraso=4), C(20, atraso=4), cfg)
    assert _card(r, "atraso").fator_tendencia == 1
    assert _card(r, "atraso").taxa_por_100 == 10


def test_contagem_invalida_e_recusada(cfg):
    with pytest.raises(ValueError):
        ranking.calcular_ranking(C(5, atraso=6), None, cfg)  # mais menções que avaliações
    with pytest.raises(ValueError):
        ranking.calcular_ranking(C(5, desconhecida=1), None, cfg)
    with pytest.raises(ValueError):
        ranking.calcular_ranking(C(-1), None, cfg)
    with pytest.raises(ValueError):
        ranking.calcular_ranking(C(5, atraso=-1), None, cfg)
    with pytest.raises(ValueError):
        ranking.calcular_ranking(C(5, invalidas=6), None, cfg)


# ---------- determinismo e configuração ----------

def test_determinismo_independe_da_ordem_dos_pesos_e_das_mencoes():
    mencoes = {"qualidade_temperatura": 2, "item_faltando": 2, "atraso": 3, "embalagem": 3, "atendimento": 3}
    esperado = None
    rng = random.Random(7)
    for _ in range(30):
        pesos = list(PADRAO["pesos"].items())
        rng.shuffle(pesos)
        dados = copy.deepcopy(PADRAO)
        dados["pesos"] = dict(pesos)
        itens = list(mencoes.items())
        rng.shuffle(itens)
        r = ranking.calcular_ranking(ContagemSemana(total=30, mencoes=dict(itens)), None,
                                     config_ranking.validar_config(dados))
        ordem = (_ordem(r), [c.categoria for c in r.outros_temas])
        esperado = esperado or ordem
        assert ordem == esperado
    # todas valem 6: gravidade 3 primeiro (if e qt, 2 menções cada: alfabética), depois gravidade 2
    # com 3 menções cada (alfabética)
    assert esperado == (["item_faltando", "qualidade_temperatura", "atendimento"], ["atraso", "embalagem"])


def test_comparacao_de_taxa_e_exata_mesmo_onde_float_empata(cfg):
    # 10**17 + 1 sobre 10**18 + 10 contra 10**17 sobre 10**18: em float as duas taxas dão 0.1
    atual = C(10**18 + 10, atraso=10**17 + 2)
    anterior = C(10**18, atraso=10**17)
    assert (10**17 + 2) / (10**18 + 10) == 10**17 / 10**18  # o float empata
    assert _card(ranking.calcular_ranking(atual, anterior, cfg), "atraso").fator_tendencia == Fraction(5, 4)


def test_mudar_um_valor_da_config_muda_o_resultado():
    base = ranking.calcular_ranking(C(30, atraso=4, item_faltando=3), None,
                                    config_ranking.validar_config(copy.deepcopy(PADRAO)))
    assert _ordem(base) == ["item_faltando", "atraso"]  # 9 contra 8

    pesos = copy.deepcopy(PADRAO)
    pesos["pesos"]["atraso"] = 3
    assert _ordem(ranking.calcular_ranking(C(30, atraso=4, item_faltando=3), None,
                                           config_ranking.validar_config(pesos))) == ["atraso", "item_faltando"]

    fator = copy.deepcopy(PADRAO)
    fator["fator_tendencia"] = Decimal("1.4")
    r = ranking.calcular_ranking(C(20, atraso=4, item_faltando=3), C(20, item_faltando=3),
                                 config_ranking.validar_config(fator))
    assert _ordem(r) == ["atraso", "item_faltando"]  # 4×2×1,4 = 11,2 contra 9
    assert _card(r, "atraso").fator_tendencia == Fraction(7, 5)

    n = copy.deepcopy(PADRAO)
    n["amostra_minima"] = 10
    r = ranking.calcular_ranking(C(15, atraso=4), C(15, atraso=1), config_ranking.validar_config(n))
    assert r.tendencia_calculada is True  # com N = 20 não seria


def test_ranking_nao_tem_valores_fixos_no_codigo():
    import inspect

    fonte = inspect.getsource(ranking)
    for proibido in ("1.25", "Fraction(5, 4)", "= 20", "qualidade_temperatura", "entregador"):
        assert proibido not in fonte, proibido


# ---------- motivo_sem_tendencia ----------

def test_motivo_none_quando_a_tendencia_foi_calculada(cfg):
    r = ranking.calcular_ranking(C(20, atraso=2), C(20, atraso=1), cfg)
    assert r.tendencia_calculada is True and r.motivo_sem_tendencia is None


def test_motivo_sem_semana_anterior(cfg):
    r = ranking.calcular_ranking(C(50, atraso=2), None, cfg)
    assert r.tendencia_calculada is False and r.motivo_sem_tendencia == "sem_semana_anterior"


@pytest.mark.parametrize("atual,anterior", [(19, 20), (20, 19), (19, 19), (5, 100), (100, 5)])
def test_motivo_amostra_pequena(cfg, atual, anterior):
    r = ranking.calcular_ranking(C(atual, atraso=2), C(anterior, atraso=1), cfg)
    assert r.tendencia_calculada is False and r.motivo_sem_tendencia == "amostra_pequena"


def test_anterior_com_total_0_e_n_20_e_amostra_pequena(cfg):
    r = ranking.calcular_ranking(C(50, atraso=2), C(0), cfg)
    assert r.tendencia_calculada is False and r.motivo_sem_tendencia == "amostra_pequena"


def test_sem_semana_anterior_vence_amostra_pequena_da_atual(cfg):
    r = ranking.calcular_ranking(C(3, atraso=1), None, cfg)
    assert r.motivo_sem_tendencia == "sem_semana_anterior"


def test_tendencia_calculada_e_motivo_sempre_coerentes(cfg):
    for atual in (0, 19, 20, 40):
        for anterior in (None, C(0), C(19), C(20), C(40)):
            r = ranking.calcular_ranking(C(atual), anterior, cfg)
            assert r.tendencia_calculada == (r.motivo_sem_tendencia is None)
