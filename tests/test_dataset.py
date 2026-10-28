"""Valida a estrutura e os rótulos dos três conjuntos de avaliação em data/.

Dados simulados. Projeto independente, sem vínculo com o iFood.
"""
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data"

# conjunto -> (arquivo, quantidade esperada)
CONJUNTOS = {
    "desenvolvimento": ("desenvolvimento.json", 20),
    "geral": ("geral.json", 50),
    "critico": ("critico.json", 15),
}

# As seis categorias de docs/GUIA_ROTULACAO.md
CATEGORIAS = {
    "atraso",
    "item_faltando",
    "qualidade_temperatura",
    "embalagem",
    "atendimento",
    "entregador",
}

# As três marcações da seção "Marcação crítica" do mesmo guia
CRITICIDADES = {"saude", "alergia", "cobranca_indevida"}

CRITICOS_POR_MARCACAO = 5


def carregar(conjunto):
    arquivo, _ = CONJUNTOS[conjunto]
    with open(DATA / arquivo, encoding="utf-8") as f:
        return json.load(f)


def normalizar(texto):
    """Normalização básica: minúsculas, sem acentos nem pontuação, espaços colapsados."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto) if not unicodedata.combining(c)
    )
    return re.sub(r"[\W_]+", " ", sem_acento.lower()).strip()


@pytest.fixture(scope="module")
def dados():
    return {conjunto: carregar(conjunto) for conjunto in CONJUNTOS}


@pytest.fixture(scope="module")
def todas(dados):
    return [aval for avaliacoes in dados.values() for aval in avaliacoes]


@pytest.mark.parametrize("conjunto", CONJUNTOS)
def test_quantidade_do_conjunto(dados, conjunto):
    _, esperado = CONJUNTOS[conjunto]
    assert len(dados[conjunto]) == esperado


@pytest.mark.parametrize("conjunto", CONJUNTOS)
def test_campo_conjunto_corresponde_ao_arquivo(dados, conjunto):
    erradas = [a["id_externo"] for a in dados[conjunto] if a["conjunto"] != conjunto]
    assert erradas == []


def test_ids_unicos_nos_tres_arquivos(todas):
    repetidos = [i for i, n in Counter(a["id_externo"] for a in todas).items() if n > 1]
    assert repetidos == []


def test_categorias_somente_as_seis_do_guia(todas):
    invalidas = {
        a["id_externo"]: sorted(set(a["categorias"]) - CATEGORIAS)
        for a in todas
        if not set(a["categorias"]) <= CATEGORIAS
    }
    assert invalidas == {}


def test_criticidade_somente_as_tres_marcacoes(todas):
    invalidas = {
        a["id_externo"]: sorted(set(a["criticidade"]) - CRITICIDADES)
        for a in todas
        if not set(a["criticidade"]) <= CRITICIDADES
    }
    assert invalidas == {}


def test_fora_do_escopo_e_booleano(todas):
    # isinstance(1, int) e bool é subclasse de int; por isso a checagem é por tipo exato
    invalidas = [a["id_externo"] for a in todas if type(a["fora_do_escopo"]) is not bool]
    assert invalidas == []


def test_nota_inteira_de_1_a_5(todas):
    invalidas = [
        a["id_externo"]
        for a in todas
        if type(a["nota"]) is not int or not 1 <= a["nota"] <= 5
    ]
    assert invalidas == []


def test_geral_nao_tem_criticidade(dados):
    # O conjunto geral mede alerta falso: nenhum item dele pode ser crítico.
    criticos = [a["id_externo"] for a in dados["geral"] if a["criticidade"]]
    assert criticos == []


@pytest.mark.parametrize("marcacao", sorted(CRITICIDADES))
def test_critico_tem_5_por_marcacao(dados, marcacao):
    quantidade = sum(1 for a in dados["critico"] if marcacao in a["criticidade"])
    assert quantidade == CRITICOS_POR_MARCACAO


def test_todo_critico_tem_ao_menos_uma_marcacao_valida(dados):
    sem_marcacao = [
        a["id_externo"]
        for a in dados["critico"]
        if not (set(a["criticidade"]) & CRITICIDADES)
    ]
    assert sem_marcacao == []


@pytest.mark.parametrize("campo", ["texto", "justificativa"])
def test_texto_e_justificativa_nao_vazios(todas, campo):
    vazios = [
        a["id_externo"]
        for a in todas
        if not isinstance(a[campo], str) or not a[campo].strip()
    ]
    assert vazios == []


def test_sem_textos_identicos_entre_conjuntos(todas):
    contagem = Counter(normalizar(a["texto"]) for a in todas)
    repetidos = sorted(
        a["id_externo"] for a in todas if contagem[normalizar(a["texto"])] > 1
    )
    assert repetidos == []


@pytest.mark.parametrize("campo", ["categorias", "criticidade"])
def test_listas_sem_valores_repetidos(todas, campo):
    repetidas = [a["id_externo"] for a in todas if len(a[campo]) != len(set(a[campo]))]
    assert repetidas == []


def test_crit_007_somente_alergia(dados):
    # Protege uma decisão do gabarito (guia v1.1, regra de acúmulo). Não é uma regra
    # universal: alergia e saude podem coexistir quando há outro evento de saúde não alérgico.
    crit_007 = next(a for a in dados["critico"] if a["id_externo"] == "crit-007")
    assert crit_007["criticidade"] == ["alergia"]
