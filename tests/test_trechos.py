"""Testes da função única de trecho (bloco 4.1).

Fonte da verdade: docs/ESPECIFICACAO.md (decisão de 31/10/2026 sobre as rotas de leitura do painel e a das
evidências do POST /acoes): até 140 caracteres, cortados em limite de palavra, com reticências quando houve
corte, espaços e quebras de linha normalizados.
"""

import pytest

from app import trechos


def test_texto_curto_sai_igual_e_sem_reticencias():
    assert trechos.trecho("A comida chegou fria.") == "A comida chegou fria."


def test_exatamente_140_caracteres_sai_inteiro():
    texto = ("palavra " * 18)[:140]  # 140 caracteres, termina sem espaço
    assert len(texto) == 140
    assert trechos.trecho(texto) == texto


def test_141_caracteres_corta_em_limite_de_palavra_com_reticencias():
    texto = "a" * 135 + " bbbbb"  # 141 caracteres; a última palavra passaria do limite
    assert trechos.trecho(texto) == "a" * 135 + "…"


def test_corte_nunca_divide_uma_palavra():
    texto = " ".join(f"palavra{i}" for i in range(40))
    resultado = trechos.trecho(texto)
    assert resultado.endswith("…")
    corpo = resultado[:-1]
    assert len(corpo) <= 140
    assert texto.startswith(corpo)
    assert texto[len(corpo)] == " "  # o corte caiu num espaço


def test_espaco_logo_depois_do_140_aproveita_os_140():
    texto = "x" * 140 + " depois"
    assert trechos.trecho(texto) == "x" * 140 + "…"


def test_palavra_gigante_sem_espaco_e_cortada_em_140():
    texto = "y" * 500
    assert trechos.trecho(texto) == "y" * 140 + "…"


def test_espacos_e_quebras_de_linha_normalizados():
    assert trechos.trecho("  a comida\n\nveio\tfria   e  atrasada \r\n") == "a comida veio fria e atrasada"


def test_normalizacao_vale_antes_da_contagem():
    texto = "a  " * 70  # 210 caracteres crus, 139 normalizados
    assert trechos.trecho(texto) == ("a " * 70).strip()


@pytest.mark.parametrize("vazio", ["", "   ", "\n\t "])
def test_texto_vazio(vazio):
    assert trechos.trecho(vazio) == ""


def test_limite_e_140():
    assert trechos.LIMITE == 140


def test_nunca_passa_de_140_caracteres_mais_a_reticencia():
    for n in range(0, 400, 7):
        texto = " ".join(["abc"] * n) + " z" * (n % 5)
        r = trechos.trecho(texto)
        if r.endswith("…"):
            assert len(r) - 1 <= 140  # até 140 de texto, mais a reticência
        else:
            assert len(r) <= 140
