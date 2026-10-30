"""Testes do ajuste com o conjunto de desenvolvimento (ajuste/), sem rede.

Dados simulados. Projeto independente, sem vínculo com o iFood.
Só lê data/desenvolvimento.json; arquivos falsos são criados em tmp_path.
"""
import ast
import copy
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
import httpx2
import pytest

from ajuste.relatorio import gerar_relatorio
from ajuste.rodar_dev import (
    ARQUIVO_DEV,
    agora_sao_paulo,
    CRITICOS_DEV,
    FEWSHOT_IDS,
    MEDICAO_IDS,
    carregar_desenvolvimento,
    rodar,
    separar,
)
from app.classificador import PARAMETROS, ErroPermanenteDaAPI, ErroTecnicoDaAPI, ErroTecnicoEsgotado

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def esperas(monkeypatch):
    """Nenhum teste espera de verdade: a espera entre tentativas do ajuste só registra o tempo."""
    from ajuste import rodar_dev

    registradas = []
    monkeypatch.setattr(rodar_dev, "_dormir", registradas.append)
    return registradas


class Bloco:
    def __init__(self, type, text=None):
        self.type = type
        if text is not None:
            self.text = text


class Uso:
    input_tokens = 2500
    output_tokens = 40


class Resposta:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason
        self._request_id = "req_falso"
        self.usage = Uso()


class ClienteEspelho:
    """Responde, para cada texto conhecido, a saída programada para ele."""

    def __init__(self, saidas_por_texto):
        self.saidas = saidas_por_texto
        self.chamadas = []
        self.messages = self

    def create(self, **kwargs):
        self.chamadas.append(copy.deepcopy(kwargs))
        texto = kwargs["messages"][0]["content"]
        return Resposta([Bloco("text", json.dumps(self.saidas[texto]))])


def _item_falso(id_, conjunto="desenvolvimento"):
    return {"id_externo": id_, "conjunto": conjunto, "nota": 3, "texto": "texto falso",
            "categorias": [], "criticidade": [], "fora_do_escopo": False,
            "justificativa": "falsa"}


@pytest.fixture(scope="module")
def dev():
    return carregar_desenvolvimento()


# ---------- carregamento e separação ----------

def test_arquivo_dev_aponta_para_o_desenvolvimento():
    assert ARQUIVO_DEV == RAIZ / "data" / "desenvolvimento.json"


def test_carregar_rejeita_outro_nome_de_arquivo(tmp_path):
    outro = tmp_path / "outro.json"
    outro.write_text(json.dumps([_item_falso("dev-001")]), encoding="utf-8")
    with pytest.raises(ValueError):
        carregar_desenvolvimento(outro)


def test_carregar_rejeita_item_de_outro_conjunto(tmp_path):
    falso = tmp_path / "desenvolvimento.json"
    falso.write_text(json.dumps([_item_falso("dev-001"), _item_falso("x-1", "outro")]),
                     encoding="utf-8")
    with pytest.raises(ValueError):
        carregar_desenvolvimento(falso)


def test_carregar_aceita_desenvolvimento_falso_valido(tmp_path):
    falso = tmp_path / "desenvolvimento.json"
    falso.write_text(json.dumps([_item_falso("dev-001")]), encoding="utf-8")
    assert [a["id_externo"] for a in carregar_desenvolvimento(falso)] == ["dev-001"]


def test_carregar_o_desenvolvimento_real(dev):
    assert len(dev) == 20
    assert {a["conjunto"] for a in dev} == {"desenvolvimento"}


def test_separar_devolve_fewshot_e_medicao_na_ordem(dev):
    fewshot, medicao = separar(dev)
    assert [a["id_externo"] for a in fewshot] == FEWSHOT_IDS
    assert [a["id_externo"] for a in medicao] == MEDICAO_IDS
    assert set(CRITICOS_DEV) == {"dev-014", "dev-015", "dev-016"}


def test_separar_falha_com_id_faltando(dev):
    with pytest.raises(ValueError):
        separar([a for a in dev if a["id_externo"] != "dev-019"])


def test_separar_falha_com_id_sobrando(dev):
    with pytest.raises(ValueError):
        separar(dev + [_item_falso("dev-099")])


def test_separar_falha_com_id_repetido(dev):
    with pytest.raises(ValueError):
        separar(dev + [copy.deepcopy(dev[0])])


# ---------- rodar ----------

def test_rodar_envia_so_textos_e_registra_previsoes(dev):
    saidas = {a["texto"]: {"categorias": a["categorias"], "criticidade": a["criticidade"]}
              for a in dev}
    cliente = ClienteEspelho(saidas)
    rodadas = rodar(dev, cliente, "PROMPT", rodadas=3)

    assert len(rodadas) == 3
    assert len(cliente.chamadas) == 60
    textos_esperados = [a["texto"] for a in dev] * 3
    for kwargs, esperado in zip(cliente.chamadas, textos_esperados):
        assert kwargs == {**PARAMETROS, "system": "PROMPT",
                          "messages": [{"role": "user", "content": esperado}]}
    bruto = repr(cliente.chamadas)
    for a in dev:
        assert a["id_externo"] not in bruto
        assert a["justificativa"] not in bruto

    for n, rodada in enumerate(rodadas, start=1):
        assert rodada["rodada"] == n
        assert [i["id"] for i in rodada["itens"]] == [a["id_externo"] for a in dev]
        for item, a in zip(rodada["itens"], dev):
            assert item["texto"] == a["texto"]
            assert item["gabarito"] == {"categorias": a["categorias"],
                                        "criticidade": a["criticidade"]}
            assert item["previsao"] == item["gabarito"]
            assert item["tentativas"] == 1
            assert item["erros"] == []
            assert item["usos"] == [{"tentativa": 1, "input_tokens": 2500, "output_tokens": 40}]


class ClienteQueFalhaNaQuinta:
    """Responde bem 4 vezes e na 5ª levanta 401 (chave inválida)."""

    def __init__(self, saidas_por_texto):
        self.ok = ClienteEspelho(saidas_por_texto)
        self.messages = self
        self.chamadas = 0

    def create(self, **kwargs):
        self.chamadas += 1
        if self.chamadas == 5:
            resposta = httpx2.Response(401, request=httpx2.Request("POST", "https://x"))
            raise anthropic.APIStatusError("SEGREDO", response=resposta, body=None)
        return self.ok.create(**kwargs)


def _saidas_do_gabarito(dev):
    return {a["texto"]: {"categorias": a["categorias"], "criticidade": a["criticidade"]}
            for a in dev}


def test_rodar_aborta_inteira_em_erro_permanente(dev):
    cliente = ClienteQueFalhaNaQuinta(_saidas_do_gabarito(dev))
    with pytest.raises(ErroPermanenteDaAPI):
        rodar(dev, cliente, "PROMPT", rodadas=3)
    assert cliente.chamadas == 5  # nenhuma repetição e nenhuma chamada depois do erro


def test_main_aborta_sem_gravar_nada_em_erro_permanente(dev, tmp_path, monkeypatch):
    from ajuste import rodar_dev

    pasta = tmp_path / "relatorios"
    monkeypatch.setattr(rodar_dev, "RELATORIOS", pasta)
    monkeypatch.setattr(rodar_dev, "criar_cliente",
                        lambda: ClienteQueFalhaNaQuinta(_saidas_do_gabarito(dev)))
    with pytest.raises(SystemExit) as exc:
        rodar_dev.main(["--versao", "v0", "--rodadas", "1"])
    assert "401" in str(exc.value)
    assert "SEGREDO" not in str(exc.value)
    assert not pasta.exists() or list(pasta.iterdir()) == []


class ClienteQueEsgotaNaQuinta:
    """Responde bem 4 vezes e, da 5ª em diante, levanta 503 (erro técnico que nunca passa)."""

    def __init__(self, saidas_por_texto):
        self.ok = ClienteEspelho(saidas_por_texto)
        self.messages = self
        self.chamadas = 0

    def create(self, **kwargs):
        self.chamadas += 1
        if self.chamadas >= 5:
            resposta = httpx2.Response(503, request=httpx2.Request("POST", "https://x"),
                                       headers={"request-id": "req_503"})
            raise anthropic.APIStatusError("SEGREDO no corpo", response=resposta, body=None)
        return self.ok.create(**kwargs)


def test_rodar_aborta_inteira_em_erro_tecnico_esgotado(dev, esperas):
    cliente = ClienteQueEsgotaNaQuinta(_saidas_do_gabarito(dev))
    with pytest.raises(ErroTecnicoEsgotado):
        rodar(dev, cliente, "PROMPT", rodadas=3)
    assert cliente.chamadas == 7  # 4 boas + 3 tentativas do item que esgotou; nada depois
    assert esperas == [2, 5]  # esperas só registradas: nenhum teste espera de verdade


def test_rodar_usa_o_dormir_injetado_e_so_espera_quando_precisa(dev):
    class ClienteTimeoutUmaVez:
        def __init__(self, saidas):
            self.ok = ClienteEspelho(saidas)
            self.messages = self
            self.chamadas = 0

        def create(self, **kwargs):
            self.chamadas += 1
            if self.chamadas == 1:
                raise anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x"))
            return self.ok.create(**kwargs)

    registro = []
    cliente = ClienteTimeoutUmaVez(_saidas_do_gabarito(dev))
    rodadas = rodar(dev[:2], cliente, "PROMPT", rodadas=1, dormir=registro.append)
    assert registro == [2]  # só uma espera, antes da 2ª tentativa do 1º item
    assert [i["tentativas"] for i in rodadas[0]["itens"]] == [2, 1]
    assert rodadas[0]["itens"][0]["erros"][0]["tipo"] == "APITimeoutError"


@pytest.mark.parametrize("cliente_ruim, esperado_na_mensagem", [
    (lambda saidas: ClienteQueFalhaNaQuinta(saidas), "401"),
    (lambda saidas: ClienteQueEsgotaNaQuinta(saidas), "503"),
], ids=["permanente", "tecnico_esgotado"])
def test_main_trata_a_familia_de_erros_tecnicos_sem_gravar_nada(
        dev, tmp_path, monkeypatch, cliente_ruim, esperado_na_mensagem):
    from ajuste import rodar_dev

    pasta = tmp_path / "relatorios"
    monkeypatch.setattr(rodar_dev, "RELATORIOS", pasta)
    monkeypatch.setattr(rodar_dev, "criar_cliente",
                        lambda: cliente_ruim(_saidas_do_gabarito(dev)))
    with pytest.raises(SystemExit) as exc:
        rodar_dev.main(["--versao", "v0", "--rodadas", "1"])
    mensagem = str(exc.value)
    assert "Execução abortada, nada foi gravado" in mensagem
    assert esperado_na_mensagem in mensagem
    assert "SEGREDO" not in mensagem  # sem mensagem nem corpo da API
    assert not pasta.exists() or list(pasta.iterdir()) == []


def test_main_nao_captura_outras_excecoes_como_erro_da_api(dev, tmp_path, monkeypatch):
    from ajuste import rodar_dev

    class ClienteComBug:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            raise RuntimeError("bug local")

    monkeypatch.setattr(rodar_dev, "RELATORIOS", tmp_path / "relatorios")
    monkeypatch.setattr(rodar_dev, "criar_cliente", lambda: ClienteComBug())
    with pytest.raises(RuntimeError):
        rodar_dev.main(["--versao", "v0", "--rodadas", "1"])


def test_base_dos_erros_que_abortam_e_a_familia_certa():
    assert issubclass(ErroPermanenteDaAPI, ErroTecnicoDaAPI)
    assert issubclass(ErroTecnicoEsgotado, ErroTecnicoDaAPI)


def test_main_recusa_sobrescrever_e_valida_rodadas(tmp_path, monkeypatch):
    from ajuste import rodar_dev

    pasta = tmp_path / "relatorios"
    pasta.mkdir()
    (pasta / "v0.md").write_text("antigo", encoding="utf-8")
    monkeypatch.setattr(rodar_dev, "RELATORIOS", pasta)
    monkeypatch.setattr(rodar_dev, "criar_cliente",
                        lambda: pytest.fail("não deve criar cliente"))
    with pytest.raises(SystemExit):
        rodar_dev.main(["--versao", "v0", "--rodadas", "1"])
    with pytest.raises(SystemExit):
        rodar_dev.main(["--versao", "v0", "--rodadas", "0"])
    assert (pasta / "v0.md").read_text(encoding="utf-8") == "antigo"


def test_data_em_sao_paulo_tem_offset():
    momento = datetime.fromisoformat(agora_sao_paulo())
    assert momento.utcoffset() == timedelta(hours=-3)
    assert agora_sao_paulo().endswith("-03:00")


# ---------- separação dos conjuntos no código ----------

def _literais(caminho):
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    return [n.value for n in ast.walk(arvore)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


ARQUIVOS_VIGIADOS = sorted((RAIZ / "ajuste").glob("*.py")) + [RAIZ / "app" / "classificador.py"]


def test_arquivos_vigiados_existem():
    nomes = {p.name for p in ARQUIVOS_VIGIADOS}
    assert {"rodar_dev.py", "relatorio.py", "classificador.py"} <= nomes


@pytest.mark.parametrize("caminho", ARQUIVOS_VIGIADOS, ids=lambda p: p.name)
def test_literais_nao_citam_outros_conjuntos(caminho):
    for literal in _literais(caminho):
        baixo = literal.lower()
        assert "geral" not in baixo, literal
        assert "critico" not in baixo, literal
        assert "crit-" not in baixo, literal
        if ".json" in baixo:
            assert literal == "desenvolvimento.json", literal


# ---------- relatório ----------

def _rodadas_falsas(dev):
    """3 rodadas: tudo igual ao gabarito, com desvios programados."""
    rodadas = []
    for n in (1, 2, 3):
        itens = []
        for a in dev:
            gab = {"categorias": a["categorias"], "criticidade": a["criticidade"]}
            itens.append({"id": a["id_externo"], "texto": a["texto"], "gabarito": gab,
                          "previsao": copy.deepcopy(gab), "tentativas": 1, "erros": []})
        rodadas.append({"rodada": n, "itens": itens})
    por_id = [{i["id"]: i for i in r["itens"]} for r in rodadas]
    # Rodada 1: dev-001 inválida após 3 tentativas; dev-014 crítico não detectado.
    por_id[0]["dev-001"].update(previsao=None, tentativas=3,
                                erros=[{"tentativa": k, "tipo": "json_invalido"} for k in (1, 2, 3)])
    por_id[0]["dev-014"]["previsao"] = {"categorias": [], "criticidade": []}
    # Rodada 2: dev-003 com categoria a mais; dev-018 com falso alerta; dev-016 inválida.
    por_id[1]["dev-003"]["previsao"] = {"categorias": ["qualidade_temperatura", "embalagem"],
                                        "criticidade": []}
    por_id[1]["dev-018"]["previsao"] = {"categorias": ["qualidade_temperatura"],
                                        "criticidade": ["saude"]}
    por_id[1]["dev-016"].update(previsao=None, tentativas=3)
    return rodadas


METADADOS = {"data": "2026-10-29T12:00:00+00:00", "modelo": "claude-sonnet-5-5",
             "parametros": {"max_tokens": 400}}


@pytest.fixture(scope="module")
def relatorio(dev):
    return gerar_relatorio("v0", _rodadas_falsas(dev), METADADOS)


def _secao(md, titulo, nivel="## "):
    """Texto entre o cabeçalho exato e o próximo cabeçalho do mesmo nível."""
    partes = md.split("\n" + nivel)
    for p in partes:
        if p.startswith(titulo):
            return p
    raise AssertionError(f"seção ausente: {titulo}")


def test_relatorio_abre_com_aviso_de_ajuste(relatorio):
    linhas = [l for l in relatorio.splitlines() if l.strip()]
    assert linhas[0].startswith("# ")
    assert "Ajuste, não é métrica oficial" in linhas[1]
    assert "20 de desenvolvimento" in linhas[1]


def test_relatorio_tem_metadados(relatorio):
    meta = _secao(relatorio, "Metadados")
    assert "v0" in meta
    assert "2026-10-29" in meta
    assert "claude-sonnet-5-5" in meta
    assert "max_tokens" in meta
    assert "Rodadas: 3" in meta
    assert "sem temperature/top_p/top_k (rejeitados pelo modelo); variação medida por repetição" in meta
    assert "--ignore" not in relatorio


def test_relatorio_total_de_chamadas(relatorio):
    # 60 itens com 1 tentativa, mais 2 extras em dev-001 (r1) e 2 em dev-016 (r2).
    assert "Total de chamadas feitas: 64" in relatorio


def test_relatorio_sem_tokens_registrados(relatorio):
    # As rodadas falsas não têm "usos": é o caso da execução já feita da v0.
    secao = _secao(relatorio, "Tokens e custo estimado")
    assert "não registrado; custo no Console" in secao
    assert "US$" not in secao


def test_relatorio_com_tokens_registrados(dev):
    rodadas = _rodadas_falsas(dev)
    for r in rodadas:
        for i in r["itens"]:
            i["usos"] = [{"tentativa": k, "input_tokens": 1000, "output_tokens": 50}
                         for k in range(1, i["tentativas"] + 1)]
    md = gerar_relatorio("v1", rodadas, METADADOS)
    secao = _secao(md, "Tokens e custo estimado")
    # 64 chamadas: 64000 tokens de entrada a US$ 2/MTok + 3200 de saída a US$ 10/MTok.
    assert "Tokens de entrada: 64000" in secao
    assert "Tokens de saída: 3200" in secao
    assert "US$ 0.1600" in secao
    assert "US$ 2 / MTok" in secao and "US$ 10 / MTok" in secao
    assert "https://platform.claude.com/docs/en/about-claude/pricing" in secao
    assert "estimado" in secao
    assert "não registrado" not in secao


def test_relatorio_uso_com_valores_none_nao_quebra(dev):
    rodadas = _rodadas_falsas(dev)
    for r in rodadas:
        for i in r["itens"]:
            i["usos"] = [{"tentativa": 1, "input_tokens": None, "output_tokens": None}]
    secao = _secao(gerar_relatorio("v1", rodadas, METADADOS), "Tokens e custo estimado")
    assert "não registrado; custo no Console" in secao


def test_relatorio_separa_medicao_e_fewshot(relatorio):
    for n in (1, 2, 3):
        rodada = _secao(relatorio, f"Rodada {n}")
        medicao = _secao(rodada, "Medição (13)", "### ")
        fewshot = _secao(rodada, "Few-shot (7)", "### ")
        for parte in (medicao, fewshot):
            assert "F1 micro" in parte
            assert "F1 macro" in parte
            assert "categorias_fora_do_macro" in parte
            assert "saidas_invalidas" in parte
            for c in ("atraso", "entregador"):
                assert c in parte
        assert "Recall com 2 itens de medição NÃO é evidência de desempenho." in medicao
    r1 = _secao(relatorio, "Rodada 1")
    assert "detectados 1 de 2" in _secao(r1, "Medição (13)", "### ")
    assert "detectados 1 de 1" in _secao(r1, "Few-shot (7)", "### ")
    assert "saidas_invalidas: 1" in _secao(r1, "Medição (13)", "### ")
    r2 = _secao(relatorio, "Rodada 2")
    assert "Falsos alertas: 1 de 11" in _secao(r2, "Medição (13)", "### ")
    assert "Falsos alertas: 0 de 6" in _secao(r2, "Few-shot (7)", "### ")
    r3 = _secao(relatorio, "Rodada 3")
    assert "F1 micro: 1.000" in _secao(r3, "Medição (13)", "### ")


def test_relatorio_criticos_de_desenvolvimento_por_rodada(relatorio):
    secao = _secao(relatorio, "Críticos de desenvolvimento")
    for id_ in ("dev-014", "dev-015", "dev-016"):
        for n in (1, 2, 3):
            assert re.search(rf"\| {id_} \| {n} \|", secao), (id_, n)
    assert re.search(r"\| dev-014 \| 1 \| \[\"saude\"\] \| \[\] \| não \| 1 \|", secao)
    assert re.search(r"\| dev-016 \| 2 \| \[\"cobranca_indevida\"\] \| saída inválida \| não \| 3 \|",
                     secao)
    assert re.search(r"\| dev-015 \| 1 \| \[\"alergia\"\] \| \[\"alergia\"\] \| sim \| 1 \|", secao)


def test_relatorio_variacao_entre_rodadas(relatorio):
    secao = _secao(relatorio, "Variação entre rodadas")
    for id_ in ("dev-001", "dev-003", "dev-014", "dev-016", "dev-018"):
        assert id_ in secao
    for estavel in ("dev-002", "dev-015", "dev-020"):
        assert estavel not in secao


def test_relatorio_lista_erros_com_id_texto_gabarito_previsao(relatorio, dev):
    secao = _secao(relatorio, "Erros")
    textos = {a["id_externo"]: a["texto"] for a in dev}
    for id_ in ("dev-001", "dev-003", "dev-014", "dev-016", "dev-018"):
        assert id_ in secao
        assert textos[id_] in secao
    assert "gabarito" in secao
    assert "previsão" in secao
    assert "saída inválida" in secao
    assert "tentativas" in secao
    assert "dev-002" not in secao
    r1 = _secao(secao, "Rodada 1, Medição", "### ")
    assert "dev-001" in r1 and "dev-014" in r1 and "dev-003" not in r1
    r3m = _secao(secao, "Rodada 3, Medição", "### ")
    assert "Nenhum erro." in r3m
