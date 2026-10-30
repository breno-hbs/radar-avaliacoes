"""Testes do prompt v0 do classificador e do seu registro.

Dados simulados. Projeto independente, sem vínculo com o iFood.
Lê só prompts/classificador/ e data/desenvolvimento.json.
"""
import json
import re
from pathlib import Path

import pytest

from ajuste.rodar_dev import FEWSHOT_IDS, MEDICAO_IDS
from app.classificador import carregar_prompt
from app.contrato import CATEGORIAS, CRITICIDADES

RAIZ = Path(__file__).resolve().parent.parent
PASTA = RAIZ / "prompts" / "classificador"
PROMPT = PASTA / "v0.md"
REGISTRO = PASTA / "v0.registro.md"
DEV = RAIZ / "data" / "desenvolvimento.json"

PADRAO_EXEMPLO = re.compile(
    r'<exemplo id="([^"]+)">\nTexto: (.*)\nSaída: (.*)\n</exemplo>'
)


@pytest.fixture(scope="module")
def prompt():
    return PROMPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def exemplos(prompt):
    return PADRAO_EXEMPLO.findall(prompt)


@pytest.fixture(scope="module")
def dev():
    return {a["id_externo"]: a for a in json.loads(DEV.read_text(encoding="utf-8"))}


def test_arquivos_existem():
    assert PROMPT.is_file()
    assert REGISTRO.is_file()


def test_carregar_prompt_devolve_o_arquivo_exato(prompt):
    assert carregar_prompt("v0") == prompt


@pytest.mark.parametrize("versao", ["../v0", "v0.md", "", "v0/../v0", "x1"])
def test_carregar_prompt_rejeita_versao_invalida(versao):
    with pytest.raises(ValueError):
        carregar_prompt(versao)


def test_prompt_cita_categorias_e_marcacoes(prompt):
    for rotulo in CATEGORIAS + CRITICIDADES:
        assert rotulo in prompt


def test_todo_bloco_de_exemplo_esta_no_formato(prompt, exemplos):
    assert prompt.count("<exemplo") == len(exemplos)
    assert prompt.count("</exemplo>") == len(exemplos)


def test_ids_dos_exemplos_na_ordem(exemplos):
    assert [e[0] for e in exemplos] == FEWSHOT_IDS
    assert len(exemplos) <= 8


def test_texto_e_saida_identicos_ao_desenvolvimento(exemplos, dev):
    for id_, texto, saida in exemplos:
        item = dev[id_]
        assert texto == item["texto"]
        assert json.loads(saida) == {
            "categorias": item["categorias"],
            "criticidade": item["criticidade"],
        }
        assert list(json.loads(saida)) == ["categorias", "criticidade"]


def test_fewshot_cobre_as_seis_categorias(dev):
    cobertas = {c for id_ in FEWSHOT_IDS for c in dev[id_]["categorias"]}
    assert cobertas == set(CATEGORIAS)


def test_fewshot_tem_critico_negacao_e_negativo_dificil(dev):
    assert any(dev[id_]["criticidade"] for id_ in FEWSHOT_IDS)
    assert "dev-017" in FEWSHOT_IDS  # negação de reação alérgica
    assert "dev-013" in FEWSHOT_IDS  # positivo que cita entregador e comida quentinha


def test_blocos_sem_campos_de_auditoria(prompt):
    blocos = re.findall(r"<exemplo .*?</exemplo>", prompt, flags=re.S)
    assert blocos
    for bloco in blocos:
        for proibido in ("nota", "justificativa", "fora_do_escopo"):
            assert proibido not in bloco


def test_prompt_sem_marca_oficial_nem_referencia_a_outros_conjuntos(prompt):
    baixo = prompt.lower()
    assert "ifood" not in baixo
    assert ".json" not in baixo
    assert "crit-" not in baixo
    assert "justificativa" not in baixo
    assert "fora_do_escopo" not in baixo


def test_fewshot_e_medicao_particionam_o_desenvolvimento(dev):
    assert set(FEWSHOT_IDS).isdisjoint(MEDICAO_IDS)
    assert len(FEWSHOT_IDS) == len(set(FEWSHOT_IDS))
    assert len(MEDICAO_IDS) == len(set(MEDICAO_IDS))
    assert set(FEWSHOT_IDS) | set(MEDICAO_IDS) == set(dev)
    assert len(dev) == 20


def test_registro_declara_candidato_e_metadados():
    registro = REGISTRO.read_text(encoding="utf-8")
    assert "candidato (não congelado)" in registro
    assert "v0" in registro
    assert "2026-10-29" in registro
    assert "claude-sonnet-5-5" in registro
    # Resultado da execução nos 20 (ajuste, não é métrica oficial), sem ajuste de prompt.
    assert "20 de 20 corretos nas 3 rodadas" in registro
    assert "0 saídas inválidas" in registro
    assert "0 tentativas extras" in registro
    assert "SEM ajuste de prompt" in registro
    assert "Métricas dos 20" in registro
    for id_ in FEWSHOT_IDS + MEDICAO_IDS:
        assert id_ in registro
