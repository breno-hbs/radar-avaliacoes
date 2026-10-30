"""Testes do relatório da avaliação oficial (avaliacao/relatorio_oficial.py), sem rede.

Dados simulados. Projeto independente, sem vínculo com o iFood.
Só usa uma execução FALSA construída aqui (3 rodadas, 50 gerais e 15 críticos inventados).
Nenhum arquivo de avaliação é aberto. Os números esperados foram calculados à mão.

Execução falsa (índices a partir de 0; ids ger-001..ger-050 e crit-001..crit-015):
- gerais: ger-(i+1) tem como gabarito a categoria CATEGORIAS[i % 6] e nenhuma criticidade.
  Contagem por categoria: atraso 9, item_faltando 9, qualidade_temperatura 8, embalagem 8,
  atendimento 8, entregador 8.
- críticos: crit-(i+1) tem criticidade saude (i%3==0), alergia (i%3==1) ou cobranca_indevida
  (i%3==2), 5 de cada, e nenhuma categoria.
- previsão = gabarito, exceto:
  Rodada 1: crit-005 (alergia) sem marcação.
            -> recall 14 de 15; alergia 4 de 5.
  Rodada 2: ger-010 (embalagem) saída inválida (3 tentativas, erros json_invalido,
            saida_invalida e stop_reason); ger-020 com falso alerta ["saude"]; crit-008 (alergia)
            previsto como cobranca_indevida.
            -> embalagem fn=1 (F1 14/15); micro 98/99; falsos alertas 1 de 50, inválidas 1;
               recall 15 de 15 (qualquer marcação); alergia 4 de 5; cobranca FP 1.
  Rodada 3: ger-031 (atraso) previsto com ["atraso", "embalagem"]; crit-013 (saude) saída inválida
            (sem erros registrados).
            -> embalagem fp=1 (F1 16/17); micro 100/101; recall 14 de 15; saude 4 de 5.
- saídas inválidas (None em gerais E críticos): 0 na rodada 1, 1 na 2, 1 na 3; 2 de 195 nas três.
  Esta é a ÚNICA base citada no relatório: "saídas inválidas nas 3 rodadas e nos 65 itens: 2 de
  195". A contagem por rodada só aparece na linha "Detalhe desta rodada".
- tag_sha da execução falsa: TAG_SHA; git_head da execução: abc123def456.
"""
import copy
import json
import re

import pytest

from avaliacao.relatorio_oficial import (
    CONJUNTO_CRITICO,
    CONJUNTO_GERAL,
    DATA_CONSULTA_PRECO,
    gerar_relatorio_oficial,
)
from app.contrato import CATEGORIAS, CRITICIDADES


# ---------- execução falsa ----------

TAG_SHA = "7a9e" * 10
FRASE_BASE = "saídas inválidas nas 3 rodadas e nos 65 itens: 2 de 195"


def _uso(tentativa=1, entrada=1000, saida=50, modelo=None):
    return {"tentativa": tentativa, "input_tokens": entrada, "output_tokens": saida,
            "modelo": modelo}


def _item(conjunto, id_, texto, categorias, criticidade):
    gab = {"categorias": list(categorias), "criticidade": list(criticidade)}
    return {"conjunto": conjunto, "id": id_, "texto": texto, "gabarito": gab,
            "previsao": copy.deepcopy(gab), "tentativas": 1, "erros": [], "usos": [_uso()]}


def _itens_base():
    itens = []
    for i in range(50):
        itens.append(_item(CONJUNTO_GERAL, f"ger-{i + 1:03d}", f"texto falso geral {i + 1}",
                           [CATEGORIAS[i % 6]], []))
    for i in range(15):
        itens.append(_item(CONJUNTO_CRITICO, f"crit-{i + 1:03d}", f"texto falso critico {i + 1}",
                           [], [CRITICIDADES[i % 3]]))
    return itens


def _execucao(abortadas=None):
    rodadas = []
    for n in (1, 2, 3):
        rodadas.append({"rodada": n, "itens": _itens_base()})
    por_id = [{i["id"]: i for i in r["itens"]} for r in rodadas]

    # Rodada 1
    por_id[0]["crit-005"]["previsao"] = {"categorias": [], "criticidade": []}
    # Rodada 2
    inv = por_id[1]["ger-010"]
    # 3 falhas de CONTEÚDO de tipos diferentes (a regra só deixa virar saída inválida assim).
    inv.update(previsao=None, tentativas=3,
               erros=[{"tentativa": 1, "tipo": "json_invalido"},
                      {"tentativa": 2, "tipo": "saida_invalida"},
                      {"tentativa": 3, "tipo": "stop_reason", "stop_reason": "max_tokens"}],
               usos=[_uso(k, 1000, 20) for k in (1, 2, 3)])
    por_id[1]["ger-020"]["previsao"]["criticidade"] = ["saude"]
    por_id[1]["crit-008"]["previsao"] = {"categorias": [], "criticidade": ["cobranca_indevida"]}
    # Rodada 3
    por_id[2]["ger-031"]["previsao"]["categorias"] = ["atraso", "embalagem"]
    # crit-013: saída inválida SEM nenhum erro registrado (dado incompleto; o relatório não quebra).
    por_id[2]["crit-013"]["previsao"] = None
    return {
        "versao": "v1",
        "hashes": {
            "prompt_sha256": "p" * 64, "schema_sha256": "s" * 64,
            "parametros_sha256": "m" * 64, "regra_retentativa_sha256": "r" * 64,
            "modelo": "claude-sonnet-5-5", "anthropic": "2.3.0", "python_dotenv": "1.2.4",
            "dados_geral_sha256": "g" * 64, "dados_critico_sha256": "k" * 64,
        },
        "git_head": "abc123def456",
        "tag_sha": TAG_SHA,
        "iniciado_em": "2026-10-30T09:00:00-03:00",
        "concluido_em": "2026-10-30T09:20:00-03:00",
        "modelo": "claude-sonnet-5-5",
        "parametros": {"model": "claude-sonnet-5-5", "max_tokens": 400,
                       "thinking": {"type": "between_tools"},
                       "output_config": {"effort": "medium"}},
        "rodadas": rodadas,
        "abortadas": abortadas if abortadas is not None else [],
    }


@pytest.fixture(scope="module")
def resultado():
    return gerar_relatorio_oficial(_execucao())


@pytest.fixture(scope="module")
def md(resultado):
    return resultado[0]


@pytest.fixture(scope="module")
def dados(resultado):
    return resultado[1]


def _secao(texto, titulo, nivel="## "):
    """Texto do cabeçalho que começa com `titulo` até o próximo cabeçalho do mesmo nível."""
    for parte in texto.split("\n" + nivel):
        if parte.startswith(titulo):
            return parte
    raise AssertionError(f"seção ausente: {titulo}")


def _rodada(md, n):
    return _secao(md, f"Rodada {n}")


def _gerais(md, n):
    return _secao(_rodada(md, n), f"Rodada {n}, avaliações gerais", "### ")


def _criticos(md, n):
    return _secao(_rodada(md, n), f"Rodada {n}, avaliações críticas", "### ")


# ---------- cabeçalho ----------

def test_nomes_dos_conjuntos_sao_constantes_locais():
    assert CONJUNTO_GERAL == "geral"
    assert CONJUNTO_CRITICO == "critico"


def test_primeira_linha_e_o_aviso_exato(md):
    primeira = md.splitlines()[0]
    assert primeira == ("# Avaliação oficial da v1 (prompt congelado). Dados simulados. "
                        "Projeto independente, sem vínculo com o iFood.")


def test_metadados_completos(md):
    meta = _secao(md, "Metadados")
    for esperado in ("v1", "p" * 64, "s" * 64, "m" * 64, "r" * 64, "claude-sonnet-5-5",
                     "2.3.0", "abc123def456", "2026-10-30T09:00:00-03:00",
                     "2026-10-30T09:20:00-03:00"):
        assert esperado in meta, esperado
    # hash da regra de retentativa e os dois hashes dos dados, cada um na sua linha rotulada
    assert f"- SHA-256 da regra de retentativa: {'r' * 64}" in meta
    assert f"- SHA-256 dos dados gerais: {'g' * 64}" in meta
    assert f"- SHA-256 dos dados críticos: {'k' * 64}" in meta
    # 195 itens com 1 tentativa, mais 2 extras em ger-010 da rodada 2.
    assert "Total de chamadas: 197" in meta
    assert "Tentativas extras: 2" in meta
    # commit da tag e HEAD da execução, cada um na sua linha rotulada
    assert f"- Commit da tag classificador-v1-congelado: {TAG_SHA}" in meta
    assert "- HEAD da execução: abc123def456" in meta
    # a base única das saídas inválidas também fica no cabeçalho
    assert f"- {FRASE_BASE[0].upper()}{FRASE_BASE[1:]}" in meta


def test_sem_tag_sha_na_execucao_o_relatorio_diz_nao_informado():
    e = _execucao()
    del e["tag_sha"]
    md_sem, dados_sem = gerar_relatorio_oficial(e)
    assert "- Commit da tag classificador-v1-congelado: não informado" in md_sem
    assert dados_sem["tag_sha"] is None


def test_dados_tem_cabecalho_equivalente(dados):
    assert dados["versao"] == "v1"
    assert dados["git_head"] == "abc123def456"
    assert dados["tag_sha"] == TAG_SHA
    # campo único da base de saídas inválidas e total de chamadas aos itens (195, sem retentativas)
    assert dados["saidas_invalidas_total"] == 2
    assert dados["total_chamadas_itens"] == 195
    assert dados["modelo"] == "claude-sonnet-5-5"
    assert dados["hashes"]["prompt_sha256"] == "p" * 64
    assert dados["hashes"]["regra_retentativa_sha256"] == "r" * 64
    assert dados["hashes"]["dados_geral_sha256"] == "g" * 64
    assert dados["hashes"]["dados_critico_sha256"] == "k" * 64
    assert dados["gerado_por_so_relatorio"] is False
    assert dados["git_head_atual"] is None
    assert dados["iniciado_em"] == "2026-10-30T09:00:00-03:00"
    assert dados["concluido_em"] == "2026-10-30T09:20:00-03:00"
    assert dados["total_chamadas"] == 197
    assert dados["tentativas_extras"] == 2
    assert dados["parametros"]["max_tokens"] == 400


def test_relatorio_normal_nao_tem_a_marca_de_so_relatorio(md):
    assert "so-relatorio" not in md


def test_relatorio_gerado_por_so_relatorio_mostra_a_marca_e_o_head_atual():
    e = _execucao()
    e["gerado_por_so_relatorio"] = True
    e["git_head_atual"] = "fedcba987654"
    md_so, dados_so = gerar_relatorio_oficial(e)
    marca = ("Gerado por --so-relatorio, a partir do diário da execução já concluída "
             "(nenhuma chamada à API foi feita). Commit atual (git HEAD): fedcba987654.")
    linhas = md_so.splitlines()
    # no cabeçalho: logo depois do título, antes dos metadados
    assert linhas[1] == "" and linhas[2] == marca
    assert md_so.index(marca) < md_so.index("## Metadados")
    # o HEAD da execução continua sendo o do diário, não o atual; o atual aparece na marca
    assert "- HEAD da execução: abc123def456" in md_so
    assert "HEAD da execução: fedcba987654" not in md_so
    assert dados_so["gerado_por_so_relatorio"] is True
    assert dados_so["git_head_atual"] == "fedcba987654"
    assert dados_so["git_head"] == "abc123def456"
    # fora a marca, o relatório é o mesmo do fluxo normal
    assert md_so.replace(marca + "\n\n", "") == gerar_relatorio_oficial(_execucao())[0]


# ---------- abortadas ----------

def test_sem_abortadas_mostra_zero(md, dados):
    assert "Execuções abortadas anteriores: 0" in md
    assert dados["abortadas"] == []


def test_abortadas_listam_pasta_causa_e_motivo():
    abortadas = [
        {"pasta": "v1_20261030T081500", "causa": "abortado: ErroPermanenteDaAPI (status 401)",
         "motivo": "chave expirada durante a execução"},
        {"pasta": "v1_20261030T083000", "causa": "sem marcação de fim",
         "motivo": "sem motivo registrado"},
    ]
    md, dados = gerar_relatorio_oficial(_execucao(abortadas=abortadas))
    assert "Execuções abortadas anteriores: 2" in md
    secao = _secao(md, "Execuções abortadas anteriores")
    for a in abortadas:
        assert a["pasta"] in secao
        assert a["causa"] in secao
        assert a["motivo"] in secao
    assert dados["abortadas"] == abortadas


# ---------- por rodada: gerais ----------

def test_rodada_1_gerais_perfeita(md, dados):
    g = _gerais(md, 1)
    assert "F1 micro: 1,000" in g
    assert "F1 macro: 1,000" in g
    assert "categorias_fora_do_macro: []" in g
    # inválidas = previsão None em gerais E críticos: 2 nas três rodadas (ger-010 r2, crit-013 r3),
    # sempre a mesma base, também na rodada 1 (que tem 0)
    assert ("Falsos alertas: 0 de 50 avaliações não críticas marcadas como críticas "
            "(saídas inválidas nas 3 rodadas e nos 65 itens: 2 de 195).") in g
    f1 = dados["rodadas"][0]["gerais"]["f1"]
    assert f1["micro"]["f1"] == pytest.approx(1.0)
    assert f1["macro_f1"] == pytest.approx(1.0)


def test_rodada_2_gerais_com_invalida_e_falso_alerta(md, dados):
    g = _gerais(md, 2)
    # micro: tp=49 fn=1 -> 98/99; embalagem: tp=7 fn=1 -> 14/15; macro: (5 + 14/15)/6
    assert "F1 micro: 0,990 (precisão 1,000, recall 0,980)" in g
    assert "F1 macro: 0,989" in g
    assert re.search(r"\| embalagem \| 7 \| 0 \| 1 \| 1,000 \| 0,875 \| 0,933 \|", g)
    assert re.search(r"\| atraso \| 9 \| 0 \| 0 \| 1,000 \| 1,000 \| 1,000 \|", g)
    # frase única: falsos alertas e a base única de saídas inválidas (não a contagem da rodada)
    frase = ("Falsos alertas: 1 de 50 avaliações não críticas marcadas como críticas "
             "(saídas inválidas nas 3 rodadas e nos 65 itens: 2 de 195).")
    assert frase in g
    f1 = dados["rodadas"][1]["gerais"]["f1"]
    assert f1["micro"]["f1"] == pytest.approx(98 / 99)
    assert f1["macro_f1"] == pytest.approx((5 + 14 / 15) / 6)
    assert f1["por_categoria"]["embalagem"]["fn"] == 1
    fa = dados["rodadas"][1]["gerais"]["falsos_alertas"]
    assert (fa["falsos_alertas"], fa["total_nao_criticas"], fa["saidas_invalidas"]) == (1, 50, 1)


def test_rodada_3_gerais_com_falso_positivo_de_categoria(md, dados):
    g = _gerais(md, 3)
    # micro: tp=50 fp=1 -> 100/101; embalagem: tp=8 fp=1 -> 16/17; macro: (5 + 16/17)/6
    assert "F1 micro: 0,990 (precisão 0,980, recall 1,000)" in g
    assert "F1 macro: 0,990" in g
    assert re.search(r"\| embalagem \| 8 \| 1 \| 0 \| 0,889 \| 1,000 \| 0,941 \|", g)
    # a saída inválida da rodada 3 é de um CRÍTICO (crit-013): entra na base de 2 de 195
    assert ("Falsos alertas: 0 de 50 avaliações não críticas marcadas como críticas "
            "(saídas inválidas nas 3 rodadas e nos 65 itens: 2 de 195).") in g
    f1 = dados["rodadas"][2]["gerais"]["f1"]
    assert f1["micro"]["f1"] == pytest.approx(100 / 101)
    assert f1["macro_f1"] == pytest.approx((5 + 16 / 17) / 6)


def test_toda_rodada_tem_as_seis_categorias_e_o_fora_do_macro(md):
    for n in (1, 2, 3):
        g = _gerais(md, n)
        assert "categorias_fora_do_macro" in g
        for c in CATEGORIAS:
            assert re.search(rf"\| {c} \| \d+ \| \d+ \| \d+ \|", g), (n, c)


def test_dados_das_saidas_invalidas_uma_base_e_detalhe_por_rodada(dados):
    # inválidas = previsão None em gerais E críticos. Base única nos dados:
    assert dados["saidas_invalidas_total"] == 2
    assert dados["total_chamadas_itens"] == 195
    # a contagem por rodada é só detalhe: sem os totais das três rodadas repetidos
    esperado = [
        {"na_rodada": 0, "itens_da_rodada": 65},
        {"na_rodada": 1, "itens_da_rodada": 65},
        {"na_rodada": 1, "itens_da_rodada": 65},
    ]
    assert [r["saidas_invalidas"] for r in dados["rodadas"]] == esperado
    assert sum(r["saidas_invalidas"]["na_rodada"] for r in dados["rodadas"]) == \
        dados["saidas_invalidas_total"]
    # a contagem dos gerais sozinha continua nas métricas brutas (fora de qualquer frase)
    assert [r["gerais"]["falsos_alertas"]["saidas_invalidas"] for r in dados["rodadas"]] == [0, 1, 0]
    # as médias não carregam mais uma segunda base de saídas inválidas
    assert "saidas_invalidas_media" not in dados["medias"]["gerais"]["falsos_alertas"]
    assert "saidas_invalidas_media" not in dados["medias"]["criticos"]


def test_detalhe_da_rodada_e_linha_separada_e_rotulada(md):
    esperados = {1: 0, 2: 1, 3: 1}
    for n, invalidas in esperados.items():
        linha = f"- Detalhe desta rodada: saídas inválidas: {invalidas} de 65 itens."
        assert linha in _rodada(md, n).splitlines()
        # fora da frase dos falsos alertas e fora das seções de gerais e de críticos
        assert linha not in _gerais(md, n) and linha not in _criticos(md, n)
        assert "Detalhe desta rodada" not in _gerais(md, n)


def test_toda_citacao_de_saidas_invalidas_usa_a_mesma_base_ou_e_detalhe_rotulado(md):
    permitidas = ("Falsos alertas:", "- Detalhe desta rodada:", "- Saídas inválidas nas 3 rodadas",
                  "9. Saídas inválidas devem ser citadas")
    achadas = 0
    for linha in md.splitlines():
        if "saídas inválidas" in linha.lower():
            achadas += 1
            assert linha.startswith(permitidas), linha
            if linha.startswith("Falsos alertas:") or linha.startswith("- Saídas inválidas nas"):
                assert FRASE_BASE[1:] in linha, linha  # no cabeçalho a frase começa em maiúscula
    # 3 frases de rodada + 1 das médias + 3 detalhes + cabeçalho + ressalva
    assert achadas == 9
    assert "entre os críticos" not in md


def test_frase_dos_falsos_alertas_usa_os_totais_reais_da_execucao():
    """Execução pequena (2 itens por rodada) para provar que os totais não são fixos em 65/195.

    Rodada 1: ger-001 e crit-001 válidos. Rodada 2: ger-001 inválido. Rodada 3: crit-001 inválido.
    Cada rodada tem 2 itens e as três têm 6; inválidas no total: 2.
    """
    rodadas = []
    for n in (1, 2, 3):
        ger = _item(CONJUNTO_GERAL, "ger-001", "t", ["atraso"], [])
        crit = _item(CONJUNTO_CRITICO, "crit-001", "t", [], ["saude"])
        rodadas.append({"rodada": n, "itens": [ger, crit]})
    rodadas[1]["itens"][0]["previsao"] = None
    rodadas[2]["itens"][1]["previsao"] = None
    e = _execucao()
    e["rodadas"] = rodadas
    md, dados = gerar_relatorio_oficial(e)
    for n, invalidas in ((1, 0), (2, 1), (3, 1)):
        # a frase é a MESMA nas três rodadas; a contagem da rodada fica no detalhe
        assert (f"Falsos alertas: 0 de 1 avaliações não críticas marcadas como críticas "
                f"(saídas inválidas nas 3 rodadas e nos 2 itens: 2 de 6).") in _gerais(md, n)
        assert f"- Detalhe desta rodada: saídas inválidas: {invalidas} de 2 itens." in _rodada(md, n)
    assert dados["rodadas"][1]["saidas_invalidas"] == {"na_rodada": 1, "itens_da_rodada": 2}
    assert (dados["saidas_invalidas_total"], dados["total_chamadas_itens"]) == (2, 6)


def test_saidas_invalidas_dos_gerais_sempre_na_frase_dos_falsos_alertas(md):
    # Em toda linha que cita saídas inválidas dos gerais, o falso alerta está na mesma linha.
    for n in (1, 2, 3):
        for linha in _gerais(md, n).splitlines():
            if "saídas inválidas" in linha:
                assert "Falsos alertas:" in linha, linha


# ---------- por rodada: críticos ----------

def test_rodada_1_criticos_14_de_15(md, dados):
    c = _criticos(md, 1)
    assert "Recall geral: 14 de 15" in c
    assert "saude: 5 de 5, falsos positivos da marcação: 0" in c
    assert "alergia: 4 de 5, falsos positivos da marcação: 0" in c
    assert "cobranca_indevida: 5 de 5, falsos positivos da marcação: 0" in c
    assert "saídas inválidas" not in c  # só a base única e o detalhe rotulado citam saídas inválidas
    r = dados["rodadas"][0]["criticos"]
    assert (r["detectadas"], r["total"]) == (14, 15)
    assert r["por_tipo"]["alergia"]["acertos"] == 4


def test_rodada_2_alergia_como_cobranca_indevida_nao_e_acerto_no_tipo(md, dados):
    c = _criticos(md, 2)
    assert "Recall geral: 15 de 15" in c  # qualquer marcação conta para o recall geral
    assert "alergia: 4 de 5, falsos positivos da marcação: 0" in c
    assert "cobranca_indevida: 5 de 5, falsos positivos da marcação: 1" in c
    r = dados["rodadas"][1]["criticos"]
    assert r["por_tipo"]["alergia"]["acertos"] == 4
    assert r["por_tipo"]["cobranca_indevida"]["falsos_positivos"] == 1


def test_rodada_3_criticos_com_invalida(md, dados):
    c = _criticos(md, 3)
    assert "Recall geral: 14 de 15" in c
    assert "saude: 4 de 5, falsos positivos da marcação: 0" in c
    assert "saídas inválidas" not in c
    assert dados["rodadas"][2]["criticos"]["saidas_invalidas"] == 1  # métrica bruta, fora de frase


# ---------- médias ----------

def test_medias_das_tres_rodadas(md, dados):
    m = _secao(md, "Médias das três rodadas")
    assert "Reportadas as três rodadas e a média; nenhuma rodada foi escolhida." in m
    # recall como média de contagens: (14 + 15 + 14) / 3 = 14,33
    assert "Recall geral: média 14,33 de 15" in m
    # por tipo: saude (5+5+4)/3 = 4,67; alergia (4+4+5)/3 = 4,33; cobranca 5,00
    assert "saude: média 4,67 de 5" in m
    assert "alergia: média 4,33 de 5" in m
    assert "cobranca_indevida: média 5,00 de 5" in m
    # falsos positivos de cobranca: (0+1+0)/3
    assert "falsos positivos da marcação: média 0,33" in m.split("cobranca_indevida:")[1]
    # F1 micro: (1 + 98/99 + 100/101) / 3 = 0,9933; macro: (1 + 0,98889 + 0,99020) / 3 = 0,9930
    assert "F1 micro: média 0,993 (3 de 3 rodadas)" in m
    assert "F1 macro: média 0,993 (3 de 3 rodadas)" in m
    # embalagem: (1 + 14/15 + 16/17) / 3 = 0,9582
    assert re.search(r"\| embalagem \| 0,958 \| 3 de 3 \|", m)
    assert re.search(r"\| atraso \| 1,000 \| 3 de 3 \|", m)
    # falsos alertas: (0+1+0)/3, com a MESMA base de saídas inválidas das frases por rodada
    assert ("Falsos alertas: média 0,33 de 50 avaliações não críticas marcadas como críticas "
            "(saídas inválidas nas 3 rodadas e nos 65 itens: 2 de 195).") in m
    assert "saídas inválidas entre os críticos" not in m

    med = dados["medias"]
    assert med["criticos"]["detectadas"]["media"] == pytest.approx(43 / 3)
    assert med["criticos"]["total"] == 15
    assert med["gerais"]["f1_micro"]["media"] == pytest.approx((1 + 98 / 99 + 100 / 101) / 3)
    assert med["gerais"]["f1_micro"]["rodadas"] == 3
    assert med["gerais"]["falsos_alertas"]["media"] == pytest.approx(1 / 3)


def test_media_de_f1_so_considera_valores_definidos_e_informa_quantas_rodadas():
    """F1 indefinido (categoria sem ocorrência) não entra na média e a contagem aparece."""
    def execucao_pequena():
        rodadas = []
        for n in (1, 2, 3):
            ger = _item(CONJUNTO_GERAL, "ger-001", "t", ["atraso"], [])
            crit = _item(CONJUNTO_CRITICO, "crit-001", "t", [], ["saude"])
            rodadas.append({"rodada": n, "itens": [ger, crit]})
        # embalagem: indefinida nas rodadas 1 e 3 (sem gabarito nem previsão); fp=1 na 2 (F1 0).
        rodadas[1]["itens"][0]["previsao"]["categorias"] = ["atraso", "embalagem"]
        e = _execucao()
        e["rodadas"] = rodadas
        return e

    md, dados = gerar_relatorio_oficial(execucao_pequena())
    m = _secao(md, "Médias das três rodadas")
    assert re.search(r"\| embalagem \| 0,000 \| 1 de 3 \|", m)
    assert re.search(r"\| entregador \| n/d \| 0 de 3 \|", m)
    assert re.search(r"\| atraso \| 1,000 \| 3 de 3 \|", m)
    emb = dados["medias"]["gerais"]["por_categoria"]["embalagem"]
    assert emb == {"media": 0.0, "rodadas": 1}
    assert dados["medias"]["gerais"]["por_categoria"]["entregador"] == {"media": None, "rodadas": 0}
    assert "categorias_fora_do_macro" in _gerais(md, 1)
    assert "embalagem" in re.search(r"categorias_fora_do_macro: (\[.*\])", _gerais(md, 1)).group(1)


def test_nunca_indica_melhor_rodada(md, dados):
    assert "melhor rodada" not in md.lower()
    assert "melhor rodada" not in json.dumps(dados, ensure_ascii=False).lower()
    assert "rodada escolhida" not in md.lower()
    assert "Reportadas as três rodadas e a média; nenhuma rodada foi escolhida." in md


# ---------- variação ----------

def test_variacao_lista_so_os_ids_que_mudaram(md, dados):
    v = _secao(md, "Variação entre rodadas")
    mudaram = ["crit-005", "crit-008", "crit-013", "ger-010", "ger-020", "ger-031"]
    for id_ in mudaram:
        assert id_ in v, id_
    for estavel in ("ger-001", "ger-050", "crit-001", "crit-015"):
        assert estavel not in v, estavel
    # previsão de cada rodada para um item
    bloco = _secao(v, "ger-010", "- **")
    assert "rodada 1" in bloco and "rodada 2: saída inválida" in bloco and "rodada 3" in bloco
    assert sorted(x["id"] for x in dados["variacao"]) == mudaram
    ger10 = next(x for x in dados["variacao"] if x["id"] == "ger-010")
    assert ger10["previsoes"][1] is None
    assert ger10["previsoes"][0] == {"categorias": ["embalagem"], "criticidade": []}


def test_variacao_ignora_a_ordem_das_listas():
    e = _execucao()
    e["rodadas"][0]["itens"][0]["gabarito"]["categorias"] = ["atraso", "embalagem"]
    for n, ordem in enumerate((["atraso", "embalagem"], ["embalagem", "atraso"], ["atraso", "embalagem"])):
        e["rodadas"][n]["itens"][0]["previsao"] = {"categorias": ordem, "criticidade": []}
    md, dados = gerar_relatorio_oficial(e)
    assert "ger-001" not in _secao(md, "Variação entre rodadas")


def test_sem_variacao_diz_que_nada_mudou():
    e = _execucao()
    base = _execucao()
    for r in e["rodadas"]:
        r["itens"] = copy.deepcopy(base["rodadas"][0]["itens"])
    md, dados = gerar_relatorio_oficial(e)
    assert "Nenhuma previsão mudou entre as rodadas." in _secao(md, "Variação entre rodadas")
    assert dados["variacao"] == []


# ---------- erros ----------

def test_erros_por_rodada_e_conjunto(md, dados):
    erros = _secao(md, "Erros")
    r1g = _secao(erros, "Rodada 1, gerais", "### ")
    r1c = _secao(erros, "Rodada 1, críticos", "### ")
    r2g = _secao(erros, "Rodada 2, gerais", "### ")
    r2c = _secao(erros, "Rodada 2, críticos", "### ")
    r3g = _secao(erros, "Rodada 3, gerais", "### ")
    r3c = _secao(erros, "Rodada 3, críticos", "### ")
    assert "Nenhum erro." in r1g
    assert "crit-005" in r1c and "texto falso critico 5" in r1c
    assert "ger-010" in r2g and "ger-020" in r2g
    assert "saída inválida" in r2g and "tentativas: 3" in r2g
    assert "texto falso geral 10" in r2g
    assert "gabarito" in r2g and "previsão" in r2g
    assert "crit-008" in r2c
    assert "ger-031" in r3g and "crit-013" in r3c
    assert "ger-001" not in erros
    assert [e["id"] for e in dados["erros"][1]["gerais"]] == ["ger-010", "ger-020"]
    assert dados["erros"][0]["gerais"] == []


def test_item_invalido_lista_o_tipo_de_erro_de_cada_tentativa_no_markdown(md):
    erros = _secao(md, "Erros")
    bloco = _secao(_secao(erros, "Rodada 2, gerais", "### "), "ger-010", "- **")
    assert "  - erros por tentativa: 1: json_invalido; 2: saida_invalida; 3: stop_reason" in bloco
    # item com previsão válida (só errada) não ganha essa linha
    r2g = _secao(erros, "Rodada 2, gerais", "### ")
    assert "erros por tentativa" not in _secao(r2g, "ger-020", "- **")
    # saída inválida sem nenhum erro registrado: o relatório diz isso, sem quebrar
    r3c = _secao(erros, "Rodada 3, críticos", "### ")
    assert "  - erros por tentativa: nenhum registrado" in _secao(r3c, "crit-013", "- **")


def test_tipos_de_erro_nos_dados_so_para_item_invalido(dados):
    ger10 = next(x for x in dados["erros"][1]["gerais"] if x["id"] == "ger-010")
    assert ger10["tipos_de_erro"] == [{"tentativa": 1, "tipo": "json_invalido"},
                                      {"tentativa": 2, "tipo": "saida_invalida"},
                                      {"tentativa": 3, "tipo": "stop_reason"}]
    ger20 = next(x for x in dados["erros"][1]["gerais"] if x["id"] == "ger-020")
    assert "tipos_de_erro" not in ger20  # previsão válida (falso alerta), não é saída inválida
    crit13 = next(x for x in dados["erros"][2]["criticos"] if x["id"] == "crit-013")
    assert crit13["tipos_de_erro"] == []


def test_tipos_de_erro_incluem_excecoes_da_api_e_nunca_mensagem_ou_corpo():
    e = _execucao()
    inv = e["rodadas"][0]["itens"][4]  # ger-005
    assert inv["id"] == "ger-005"
    inv.update(previsao=None, tentativas=3,
               erros=[{"tentativa": 1, "tipo": "APITimeoutError", "status": None, "request_id": None},
                      {"tentativa": 2, "tipo": "APIStatusError", "status": 503, "request_id": "req_7"},
                      {"tentativa": 3, "tipo": "json_invalido", "request_id": "req_8",
                       "stop_reason": "end_turn"}])
    md, dados = gerar_relatorio_oficial(e)
    bloco = _secao(_secao(_secao(md, "Erros"), "Rodada 1, gerais", "### "), "ger-005", "- **")
    assert ("  - erros por tentativa: 1: APITimeoutError; 2: APIStatusError; "
            "3: json_invalido") in bloco
    item = next(x for x in dados["erros"][0]["gerais"] if x["id"] == "ger-005")
    # só tentativa e tipo: status, request_id e stop_reason ficam de fora dos dados do relatório
    assert item["tipos_de_erro"] == [{"tentativa": 1, "tipo": "APITimeoutError"},
                                     {"tentativa": 2, "tipo": "APIStatusError"},
                                     {"tentativa": 3, "tipo": "json_invalido"}]
    assert "req_7" not in md and "req_8" not in md


def test_erro_traz_texto_gabarito_previsao_e_tentativas_nos_dados(dados):
    e = next(x for x in dados["erros"][1]["gerais"] if x["id"] == "ger-010")
    assert e["texto"] == "texto falso geral 10"
    assert e["gabarito"] == {"categorias": ["embalagem"], "criticidade": []}
    assert e["previsao"] is None
    assert e["tentativas"] == 3


# ---------- tokens e custo ----------

def test_tokens_e_custo(md, dados):
    t = _secao(md, "Tokens e custo estimado")
    # 197 usos de entrada de 1000 tokens; saída: 194 usos de 50 e 3 usos de 20 = 9760
    assert "Tokens de entrada: 197000" in t
    assert "Tokens de saída: 9760" in t
    # 197000 * 2 / 1e6 + 9760 * 10 / 1e6 = 0,394 + 0,0976
    assert "US$ 0,4916" in t
    assert "US$ 2 / MTok" in t and "US$ 10 / MTok" in t
    assert "https://platform.claude.com/docs/en/about-claude/pricing" in t
    # a data da consulta do preço aparece junto da fonte
    assert ("- Fonte do preço: https://platform.claude.com/docs/en/about-claude/pricing "
            "(preço consultado em 2026-10-29)") in t
    assert DATA_CONSULTA_PRECO == "2026-10-29"
    assert dados["tokens"]["data_consulta_preco"] == "2026-10-29"
    assert "custo real está no Console" in t
    assert dados["tokens"]["entrada"] == 197000
    assert dados["tokens"]["saida"] == 9760
    assert dados["tokens"]["custo_estimado_usd"] == pytest.approx(0.4916)


def test_uso_com_none_nao_quebra_e_soma_o_que_existe():
    e = _execucao()
    itens = e["rodadas"][0]["itens"]
    itens[0]["usos"] = [{"tentativa": 1, "input_tokens": None, "output_tokens": None}]
    itens[1]["usos"] = [{"tentativa": 1, "input_tokens": 500, "output_tokens": None}]
    itens[2]["usos"] = []
    del itens[3]["usos"]
    md, dados = gerar_relatorio_oficial(e)
    t = _secao(md, "Tokens e custo estimado")
    assert "custo real está no Console" in t
    # Originalmente 197 usos de 1000 de entrada e 9760 de saída. Os 4 itens alterados tinham
    # 1 uso cada (1000 e 50): saem 4000 de entrada e 200 de saída; entram 500 de entrada.
    assert dados["tokens"]["entrada"] == 197000 - 4000 + 500
    assert dados["tokens"]["saida"] == 9760 - 200
    json.dumps(dados)  # continua serializável


def test_sem_nenhum_uso_registrado():
    e = _execucao()
    for r in e["rodadas"]:
        for i in r["itens"]:
            i["usos"] = [{"tentativa": 1, "input_tokens": None, "output_tokens": None}]
    md, dados = gerar_relatorio_oficial(e)
    t = _secao(md, "Tokens e custo estimado")
    assert "não registrado" in t
    assert "custo real está no Console" in t
    assert dados["tokens"]["custo_estimado_usd"] is None


# ---------- modelos informados pela API ----------

def test_sem_modelo_informado_o_relatorio_diz_nao_informado(md, dados):
    assert "- Modelos informados pela API: não informado" in md
    assert dados["modelos_informados"] == []


def test_modelos_informados_sao_conjunto_ordenado_sem_none():
    e = _execucao()
    itens = e["rodadas"][0]["itens"]
    itens[0]["usos"][0]["modelo"] = "claude-b"
    itens[1]["usos"][0]["modelo"] = "claude-a"
    itens[2]["usos"][0]["modelo"] = "claude-b"  # repetido: entra uma vez
    # o restante continua com modelo None: ignorado
    md_m, dados_m = gerar_relatorio_oficial(e)
    assert "- Modelos informados pela API: claude-a, claude-b" in md_m
    assert dados_m["modelos_informados"] == ["claude-a", "claude-b"]


def test_modelos_do_item_com_erro_aparecem_no_relatorio():
    e = _execucao()
    inv = next(i for i in e["rodadas"][1]["itens"] if i["id"] == "ger-010")  # 3 usos
    inv["usos"][0]["modelo"] = "claude-z"
    inv["usos"][1]["modelo"] = "claude-y"
    inv["usos"][2]["modelo"] = "claude-z"
    md_m, dados_m = gerar_relatorio_oficial(e)
    registro = next(x for x in dados_m["erros"][1]["gerais"] if x["id"] == "ger-010")
    assert registro["modelos"] == ["claude-y", "claude-z"]
    bloco = _secao(_secao(_secao(md_m, "Erros"), "Rodada 2, gerais", "### "), "ger-010", "- **")
    assert "  - modelos informados pela API: claude-y, claude-z" in bloco
    ger20 = next(x for x in dados_m["erros"][1]["gerais"] if x["id"] == "ger-020")
    assert ger20["modelos"] == []
    assert "  - modelos informados pela API: não informado" in _secao(
        _secao(_secao(md_m, "Erros"), "Rodada 2, gerais", "### "), "ger-020", "- **")


# ---------- motivo técnico escapado no markdown ----------

def test_motivo_das_abortadas_e_escapado_sem_quebra_de_linha_crua():
    motivo = "queda de rede\n# Título falso\n```codigo``` e `crase`\n## Outra seção"
    abortadas = [{"pasta": "v1_20261030T081500", "causa": "abortado", "motivo": motivo}]
    md_m, dados_m = gerar_relatorio_oficial(_execucao(abortadas=abortadas))
    secao = _secao(md_m, "Execuções abortadas anteriores")
    assert f"motivo: {json.dumps(motivo, ensure_ascii=False)}" in secao
    # nenhuma linha do relatório começa com o '#' do motivo, e não há quebra crua dele
    assert "\n# Título falso" not in md_m and "\n## Outra seção" not in md_m
    assert [l for l in md_m.splitlines() if l.startswith("# ")] == [md_m.splitlines()[0]]
    linha = next(l for l in secao.splitlines() if l.startswith("- pasta:"))
    assert "\\n" in linha and "queda de rede" in linha
    # nos dados o motivo continua como texto original
    assert dados_m["abortadas"][0]["motivo"] == motivo


# ---------- ressalvas ----------

def test_ressalvas_obrigatorias_exatamente_nove(md, dados):
    r = _secao(md, "Ressalvas obrigatórias")
    itens = [l for l in r.splitlines() if re.match(r"\d+\. ", l)]
    assert len(itens) == 9
    assert len(dados["ressalvas"]) == 9
    corpo = " ".join(itens)
    for trecho in (
        "50 gerais e 15 críticos",                       # amostra pequena
        "rotulador único",
        "85 avaliações fictícias",                       # criadas com apoio de IA
        "apoio de IA",
        "acerto inflado",
        "amostra crítica enriquecida",
        "não representa a frequência real",
        "crit-007",
        "crit-009",
        "não é totalmente independente",
        "não comprovam desempenho com clientes reais",
        "nunca chegou",
        "atraso",
        "alergia de terceiro",
        "interpretação do modelo",
        "mesma frase dos falsos alertas",
    ):
        assert trecho.lower() in corpo.lower(), trecho


# ---------- forma e pureza ----------

def test_dados_sao_serializaveis_em_json(dados):
    texto = json.dumps(dados, ensure_ascii=False)
    assert json.loads(texto)["git_head"] == "abc123def456"


def test_nao_altera_a_execucao_recebida():
    e = _execucao()
    antes = copy.deepcopy(e)
    gerar_relatorio_oficial(e)
    assert e == antes


def test_exige_itens_dos_dois_conjuntos():
    e = _execucao()
    for r in e["rodadas"]:
        r["itens"] = [i for i in r["itens"] if i["conjunto"] == CONJUNTO_GERAL]
    with pytest.raises(ValueError):
        gerar_relatorio_oficial(e)


def test_nomes_de_conjunto_por_parametro():
    e = _execucao()
    for r in e["rodadas"]:
        for i in r["itens"]:
            i["conjunto"] = "A" if i["conjunto"] == CONJUNTO_GERAL else "B"
    md, dados = gerar_relatorio_oficial(e, conjunto_geral="A", conjunto_critico="B")
    assert "Recall geral: 14 de 15" in _criticos(md, 1)


def test_modulo_nao_cita_arquivos_de_dados_nem_importa_o_executor():
    from pathlib import Path
    import ast

    fonte = (Path(__file__).resolve().parent.parent / "avaliacao" / "relatorio_oficial.py"
             ).read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    for no in ast.walk(arvore):
        if isinstance(no, ast.Constant) and isinstance(no.value, str):
            assert ".json" not in no.value, no.value
            assert "data/" not in no.value, no.value
        if isinstance(no, (ast.Import, ast.ImportFrom)):
            nomes = [a.name for a in no.names] + [getattr(no, "module", None) or ""]
            assert not any("rodar_oficial" in n for n in nomes)
