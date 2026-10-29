"""Valida as métricas de avaliação do classificador (app/metricas.py).

Dados simulados. Projeto independente, sem vínculo com o iFood.
Todos os exemplos são listas pequenas inventadas aqui; nenhum arquivo é lido.
"""
import ast
import inspect

import pytest

from app import metricas
from app.metricas import f1_categorias, falsos_alertas, recall_criticos

# Cálculo à mão do exemplo de 4 avaliações:
#   (1) gab [atraso, embalagem]  prev [atraso]
#   (2) gab [atraso]             prev [atraso, atendimento]
#   (3) gab []                   prev []
#   (4) gab [embalagem]          prev [embalagem]
# atraso:      tp=2 (1 e 2), fp=0, fn=0 -> P=1, R=1, F1=1.0
# embalagem:   tp=1 (4), fp=0, fn=1 (1) -> P=1, R=1/2, F1=2*1*0.5/1.5=2/3
# atendimento: tp=0, fp=1 (2), fn=0     -> P=0, R=None, F1=0.0
# demais:      tp=fp=fn=0               -> F1 None (fora do macro)
# micro: TP=3, FP=1, FN=1 -> P=3/4, R=3/4, F1=0.75
# macro: (1 + 2/3 + 0) / 3 = 0.5556
GAB_4 = [["atraso", "embalagem"], ["atraso"], [], ["embalagem"]]
PREV_4 = [["atraso"], ["atraso", "atendimento"], [], ["embalagem"]]


def test_f1_calculo_a_mao():
    r = f1_categorias(GAB_4, PREV_4)
    pc = r["por_categoria"]
    assert set(pc) == {
        "atraso", "item_faltando", "qualidade_temperatura",
        "embalagem", "atendimento", "entregador",
    }

    assert (pc["atraso"]["tp"], pc["atraso"]["fp"], pc["atraso"]["fn"]) == (2, 0, 0)
    assert pc["atraso"]["f1"] == pytest.approx(1.0)

    assert (pc["embalagem"]["tp"], pc["embalagem"]["fp"], pc["embalagem"]["fn"]) == (1, 0, 1)
    assert pc["embalagem"]["precisao"] == pytest.approx(1.0)
    assert pc["embalagem"]["recall"] == pytest.approx(0.5)
    assert pc["embalagem"]["f1"] == pytest.approx(2 / 3)

    assert (pc["atendimento"]["tp"], pc["atendimento"]["fp"], pc["atendimento"]["fn"]) == (0, 1, 0)
    assert pc["atendimento"]["f1"] == 0.0
    assert pc["atendimento"]["precisao"] == 0.0
    assert pc["atendimento"]["recall"] is None

    for cat in ("item_faltando", "qualidade_temperatura", "entregador"):
        assert pc[cat]["f1"] is None
        assert pc[cat]["precisao"] is None
        assert pc[cat]["recall"] is None
    assert set(r["categorias_fora_do_macro"]) == {
        "item_faltando", "qualidade_temperatura", "entregador",
    }

    assert r["micro"]["precisao"] == pytest.approx(0.75)
    assert r["micro"]["recall"] == pytest.approx(0.75)
    assert r["micro"]["f1"] == pytest.approx(0.75)
    assert r["macro_f1"] == pytest.approx((1 + 2 / 3 + 0) / 3)
    assert r["saidas_invalidas"] == 0


def test_f1_categoria_sem_ocorrencia_fica_fora_do_macro():
    r = f1_categorias([["atraso"]], [["atraso"]])
    assert r["por_categoria"]["entregador"]["f1"] is None
    assert "entregador" in r["categorias_fora_do_macro"]
    assert r["macro_f1"] == pytest.approx(1.0)


def test_f1_categoria_so_com_previsao_e_zero():
    r = f1_categorias([[]], [["entregador"]])
    assert r["por_categoria"]["entregador"]["f1"] == 0.0
    assert "entregador" not in r["categorias_fora_do_macro"]


def test_f1_categoria_so_com_gabarito_e_zero():
    r = f1_categorias([["embalagem"]], [[]])
    assert r["por_categoria"]["embalagem"]["f1"] == 0.0


def test_f1_tudo_vazio_sem_divisao_por_zero():
    r = f1_categorias([[], []], [[], []])
    assert r["micro"] == {"precisao": None, "recall": None, "f1": None}
    assert r["macro_f1"] is None
    assert len(r["categorias_fora_do_macro"]) == 6


def test_f1_none_conta_como_previsao_vazia_e_e_contado():
    r = f1_categorias([["atraso"], ["embalagem"]], [None, ["embalagem"]])
    assert r["saidas_invalidas"] == 1
    assert r["por_categoria"]["atraso"]["fn"] == 1
    assert r["por_categoria"]["atraso"]["tp"] == 0
    assert r["por_categoria"]["embalagem"]["tp"] == 1


def test_f1_tamanhos_diferentes():
    with pytest.raises(ValueError):
        f1_categorias([["atraso"], []], [["atraso"]])


def test_recall_contagem_com_qualquer_marcacao():
    gab = [["saude"], ["alergia"], ["cobranca_indevida"]]
    prev = [["saude"], [], ["saude"]]
    r = recall_criticos(gab, prev)
    assert (r["detectadas"], r["total"]) == (2, 3)
    assert r["por_tipo"]["saude"] == {
        "acertos": 1, "total": 1, "recall": 1.0, "falsos_positivos": 1,
    }
    assert r["por_tipo"]["alergia"]["acertos"] == 0
    assert r["por_tipo"]["alergia"]["recall"] == 0.0
    assert r["saidas_invalidas"] == 0


def test_recall_alergia_como_cobranca_nao_e_acerto_de_tipo():
    r = recall_criticos([["alergia"]], [["cobranca_indevida"]])
    assert r["detectadas"] == 1 and r["total"] == 1
    assert r["por_tipo"]["alergia"]["acertos"] == 0
    assert r["por_tipo"]["alergia"]["recall"] == 0.0
    assert r["por_tipo"]["cobranca_indevida"]["falsos_positivos"] == 1
    assert r["por_tipo"]["cobranca_indevida"]["total"] == 0


def test_recall_tipo_sem_ocorrencia_e_none():
    r = recall_criticos([["saude"]], [["saude"]])
    assert r["por_tipo"]["alergia"]["total"] == 0
    assert r["por_tipo"]["alergia"]["recall"] is None
    assert r["por_tipo"]["cobranca_indevida"]["recall"] is None


def test_recall_none_nao_detectada_e_conta_invalida():
    r = recall_criticos([["saude"], ["alergia"]], [None, ["alergia"]])
    assert (r["detectadas"], r["total"]) == (1, 2)
    assert r["saidas_invalidas"] == 1
    assert r["por_tipo"]["saude"]["acertos"] == 0
    assert r["por_tipo"]["saude"]["recall"] == 0.0


def test_recall_tamanhos_diferentes():
    with pytest.raises(ValueError):
        recall_criticos([["saude"], ["alergia"]], [["saude"]])


def test_exemplo_da_especificacao_alergia_com_saude_extra():
    # Gabarito ["alergia"], previsão ["alergia", "saude"]:
    # acerto de alergia, falso positivo de saude, 1 detectada de 1,
    # e nenhum falso alerta (o item é crítico no gabarito).
    gab = [["alergia"]]
    prev = [["alergia", "saude"]]
    r = recall_criticos(gab, prev)
    assert (r["detectadas"], r["total"]) == (1, 1)
    assert r["por_tipo"]["alergia"]["acertos"] == 1
    assert r["por_tipo"]["alergia"]["falsos_positivos"] == 0
    assert r["por_tipo"]["saude"]["falsos_positivos"] == 1
    fa = falsos_alertas(gab, prev)
    assert fa["falsos_alertas"] == 0
    assert fa["total_nao_criticas"] == 0


def test_falsos_alertas_basico():
    gab = [[], [], [], []]
    prev = [[], ["saude"], ["saude", "alergia"], []]
    r = falsos_alertas(gab, prev)
    # Duas marcações na mesma avaliação contam 1.
    assert r == {"falsos_alertas": 2, "total_nao_criticas": 4, "saidas_invalidas": 0}


def test_falsos_alertas_item_critico_nunca_e_falso_alerta():
    r = falsos_alertas([["saude"], []], [["saude"], ["saude"]])
    assert r["falsos_alertas"] == 1
    assert r["total_nao_criticas"] == 1


def test_falsos_alertas_none_sem_alerta_e_invalida():
    r = falsos_alertas([[], []], [None, []])
    assert r == {"falsos_alertas": 0, "total_nao_criticas": 2, "saidas_invalidas": 1}


def test_falsos_alertas_tamanhos_diferentes():
    with pytest.raises(ValueError):
        falsos_alertas([[], []], [[]])


# Validação de entrada: rótulo desconhecido ou tipo errado levanta erro, nunca é ignorado.
FUNCOES = [f1_categorias, recall_criticos, falsos_alertas]
ROTULO_BOM = {
    f1_categorias: "atraso",
    recall_criticos: "saude",
    falsos_alertas: "saude",
}
ROTULO_FORA = {
    # marcação crítica não é categoria e vice-versa
    f1_categorias: "alergia",
    recall_criticos: "atraso",
    falsos_alertas: "atraso",
}


@pytest.mark.parametrize("funcao", FUNCOES)
def test_rotulo_fora_do_conjunto_no_gabarito_levanta_erro(funcao):
    bom, fora = ROTULO_BOM[funcao], ROTULO_FORA[funcao]
    for invalido in (fora, "inexistente"):
        with pytest.raises(ValueError):
            funcao([[bom], [invalido]], [[bom], [bom]])


@pytest.mark.parametrize("funcao", FUNCOES)
def test_rotulo_fora_do_conjunto_na_previsao_levanta_erro(funcao):
    bom, fora = ROTULO_BOM[funcao], ROTULO_FORA[funcao]
    for invalido in (fora, "inexistente"):
        with pytest.raises(ValueError):
            funcao([[bom], [bom]], [[bom], [invalido]])


@pytest.mark.parametrize("funcao", FUNCOES)
@pytest.mark.parametrize("invalido", [None, "", 1, True, ["x"]])
def test_rotulo_que_nao_e_str_levanta_erro(funcao, invalido):
    bom = ROTULO_BOM[funcao]
    with pytest.raises(ValueError):
        funcao([[bom]], [[invalido]])
    with pytest.raises(ValueError):
        funcao([[bom, invalido]], [[bom]])


@pytest.mark.parametrize("funcao", FUNCOES)
@pytest.mark.parametrize("nao_lista", ["atraso", ("saude",), {"saude"}, 3, {"a": 1}])
def test_gabarito_ou_previsao_que_nao_e_lista_levanta_erro(funcao, nao_lista):
    bom = ROTULO_BOM[funcao]
    with pytest.raises(ValueError):
        funcao([nao_lista], [[bom]])
    with pytest.raises(ValueError):
        funcao([[bom]], [nao_lista])


@pytest.mark.parametrize("funcao", FUNCOES)
def test_colecao_de_gabaritos_ou_previsoes_que_nao_e_lista_levanta_erro(funcao):
    bom = ROTULO_BOM[funcao]
    with pytest.raises(ValueError):
        funcao(([bom],), [[bom]])
    with pytest.raises(ValueError):
        funcao([[bom]], ([bom],))


@pytest.mark.parametrize("funcao", FUNCOES)
def test_gabarito_none_levanta_erro(funcao):
    # Só a previsão pode ser None (saída inválida do modelo); gabarito nunca.
    bom = ROTULO_BOM[funcao]
    with pytest.raises(ValueError):
        funcao([None], [[bom]])


def test_recall_gabarito_vazio_nao_e_caso_critico():
    with pytest.raises(ValueError):
        recall_criticos([["saude"], []], [["saude"], []])


def test_recall_entrada_vazia_levanta_erro():
    with pytest.raises(ValueError):
        recall_criticos([], [])


def test_f1_e_falsos_alertas_aceitam_entrada_vazia():
    assert f1_categorias([], [])["macro_f1"] is None
    assert falsos_alertas([], [])["total_nao_criticas"] == 0


@pytest.mark.parametrize("funcao", FUNCOES)
def test_rotulo_repetido_no_gabarito_levanta_erro(funcao):
    bom = ROTULO_BOM[funcao]
    with pytest.raises(ValueError):
        funcao([[bom, bom]], [[bom]])


@pytest.mark.parametrize("funcao", FUNCOES)
def test_rotulo_repetido_na_previsao_levanta_erro(funcao):
    bom = ROTULO_BOM[funcao]
    with pytest.raises(ValueError):
        funcao([[bom]], [[bom, bom]])


def test_falsos_alertas_aceita_gabarito_vazio():
    # No conjunto geral o gabarito de criticidade é vazio; não pode virar erro.
    assert falsos_alertas([[]], [[]])["total_nao_criticas"] == 1


# Isolamento: as métricas não leem arquivo nem tocam em data/.
def _fonte():
    return inspect.getsource(metricas)


def test_metricas_sem_imports_de_arquivo():
    proibidos = {"os", "pathlib", "json", "io", "open"}
    nomes = set()
    for no in ast.walk(ast.parse(_fonte())):
        if isinstance(no, ast.Import):
            nomes |= {a.name.split(".")[0] for a in no.names}
            nomes |= {a.asname for a in no.names if a.asname}
        elif isinstance(no, ast.ImportFrom):
            nomes.add((no.module or "").split(".")[0])
            nomes |= {a.name for a in no.names}
            nomes |= {a.asname for a in no.names if a.asname}
        elif isinstance(no, ast.Name) and no.id == "open":
            nomes.add("open")
    assert not (proibidos & nomes)


def test_metricas_sem_referencia_a_data():
    fonte = _fonte()
    assert "data/" not in fonte
    assert "data\\" not in fonte
