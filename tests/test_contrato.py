"""Valida o contrato de saída do classificador (app/contrato.py).

Dados simulados. Projeto independente, sem vínculo com o iFood.
"""
import pytest

from app.contrato import (
    CATEGORIAS,
    CRITICIDADES,
    SaidaInvalida,
    saida_texto_vazio,
    texto_vazio,
    validar_saida,
)


def test_conjuntos_do_guia():
    assert set(CATEGORIAS) == {
        "atraso", "item_faltando", "qualidade_temperatura",
        "embalagem", "atendimento", "entregador",
    }
    assert set(CRITICIDADES) == {"saude", "alergia", "cobranca_indevida"}


def test_saida_valida_e_devolvida():
    obj = {"categorias": ["atraso", "embalagem"], "criticidade": ["alergia"]}
    assert validar_saida(obj) == obj


def test_listas_vazias_sao_validas():
    obj = {"categorias": [], "criticidade": []}
    assert validar_saida(obj) == obj


@pytest.mark.parametrize("obj", [None, "", "texto", [], ["atraso"], 3])
def test_rejeita_nao_dict(obj):
    with pytest.raises(SaidaInvalida):
        validar_saida(obj)


@pytest.mark.parametrize("obj", [
    {},
    {"categorias": []},
    {"criticidade": []},
    {"categorias": [], "criticidade": [], "fora_do_escopo": False},
    {"categorias": [], "criticidade": [], "justificativa": "x"},
    {"categorias": [], "criticidade": [], "extra": []},
    {"categorias": [], "critica": []},
])
def test_rejeita_chaves_diferentes(obj):
    with pytest.raises(SaidaInvalida):
        validar_saida(obj)


@pytest.mark.parametrize("obj", [
    {"categorias": "atraso", "criticidade": []},
    {"categorias": None, "criticidade": []},
    {"categorias": ("atraso",), "criticidade": []},
    {"categorias": [], "criticidade": "alergia"},
    {"categorias": [], "criticidade": {"alergia"}},
])
def test_rejeita_valor_que_nao_e_lista(obj):
    with pytest.raises(SaidaInvalida):
        validar_saida(obj)


@pytest.mark.parametrize("item", [True, 1, None, 1.5, ["atraso"], {"a": 1}])
def test_rejeita_item_que_nao_e_str(item):
    with pytest.raises(SaidaInvalida):
        validar_saida({"categorias": [item], "criticidade": []})
    with pytest.raises(SaidaInvalida):
        validar_saida({"categorias": [], "criticidade": [item]})


def test_rejeita_item_fora_do_conjunto():
    with pytest.raises(SaidaInvalida):
        validar_saida({"categorias": ["preco"], "criticidade": []})
    with pytest.raises(SaidaInvalida):
        validar_saida({"categorias": [], "criticidade": ["fora_do_escopo"]})


def test_rejeita_troca_entre_conjuntos():
    # Marcação crítica não é categoria e vice-versa.
    with pytest.raises(SaidaInvalida):
        validar_saida({"categorias": ["alergia"], "criticidade": []})
    with pytest.raises(SaidaInvalida):
        validar_saida({"categorias": [], "criticidade": ["atraso"]})


def test_rejeita_item_repetido():
    with pytest.raises(SaidaInvalida):
        validar_saida({"categorias": ["atraso", "atraso"], "criticidade": []})
    with pytest.raises(SaidaInvalida):
        validar_saida({"categorias": [], "criticidade": ["saude", "saude"]})


def test_saida_invalida_e_value_error():
    assert issubclass(SaidaInvalida, ValueError)


@pytest.mark.parametrize("texto", [None, "", "   ", "\n\t "])
def test_texto_vazio_verdadeiro(texto):
    assert texto_vazio(texto) is True


@pytest.mark.parametrize("texto", ["Tudo perfeito!", " a "])
def test_texto_vazio_falso(texto):
    assert texto_vazio(texto) is False


def test_texto_vazio_resulta_em_saida_vazia_valida():
    saida = saida_texto_vazio()
    assert saida == {"categorias": [], "criticidade": []}
    assert validar_saida(saida) == saida


def test_saida_texto_vazio_nao_compartilha_estado():
    a = saida_texto_vazio()
    a["categorias"].append("atraso")
    assert saida_texto_vazio() == {"categorias": [], "criticidade": []}
