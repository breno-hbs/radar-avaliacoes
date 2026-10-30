"""Testes do executor da avaliação oficial (avaliacao/rodar_oficial.py), sem rede.

Dados simulados. Projeto independente, sem vínculo com o iFood.

NENHUM teste toca os arquivos reais de avaliação, a API ou o .env:
- um fixture autouse redireciona todos os caminhos do módulo para tmp_path, aponta os arquivos
  de dados para caminhos que NÃO existem (abrir qualquer um deles falha) e troca git_head,
  git_alteracoes e criar_cliente por versões falsas;
- os conjuntos de 50 e 15 itens são inventados aqui;
- o cliente é um objeto falso que só devolve respostas programadas.
"""
import ast
import copy
import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
import httpx2
import pytest

from app import classificador
from app.classificador import PARAMETROS, carregar_prompt, sha256_do_texto
from app.contrato import CATEGORIAS, CRITICIDADES
from avaliacao import rodar_oficial as ro
from avaliacao.congelamento import hash_canonico, hashes_atuais

RAIZ = Path(__file__).resolve().parent.parent
NOMES_DAS_CONSTANTES = ("VERSAO", "ARQUIVO_GERAL", "ARQUIVO_CRITICO", "ARQUIVO_CONGELAMENTO",
                        "PASTA_RESULTADOS", "PASTA_ABORTADAS", "ARQUIVO_LOCK", "RODADAS",
                        "N_GERAL", "N_CRITICO", "CUSTO_ESTIMADO_POR_CHAMADA",
                        "TAG_CONGELAMENTO", "ARQUIVOS_CONGELADOS")
# Valores padrão, capturados antes de o fixture autouse trocá-los.
PADROES = {nome: getattr(ro, nome) for nome in NOMES_DAS_CONSTANTES}
# Funções reais de git, capturadas antes de o fixture autouse trocá-las por versões falsas.
GIT_HEAD_REAL = ro.git_head
GIT_ALTERACOES_REAL = ro.git_alteracoes
CONFERIR_TAG_REAL = ro.conferir_tag

HEAD_FALSO = "cafe0123456789abcdef0123456789abcdef0123"
TAG_SHA_FALSO = "7a9e1b2c3d4e5f60718293a4b5c6d7e8f9012345"
MODELO_FALSO = "claude-falso-1"  # o que a API "informa" em response.model nos testes
MSG_ORFAO = "relatório presente (possivelmente incompleto): confira manualmente; não sobrescrevo"
MSG_ABORTO = "Execução abortada; nenhum resultado parcial foi exibido. Diário preservado.\n"


# ---------- objetos falsos ----------

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
        self.model = MODELO_FALSO


class ClienteEspelho:
    """Responde a cada texto conhecido com a saída programada (por padrão, o gabarito).

    `antes(n)` roda no início da chamada n (para inspecionar o diário no meio da execução).
    `falhas` mapeia o número da chamada a uma exceção a levantar.
    `textos_ruins` mapeia o número da chamada a um texto de resposta (por exemplo, JSON inválido).
    """

    def __init__(self, saidas_por_texto, falhas=None, antes=None, textos_ruins=None):
        self.saidas = saidas_por_texto
        self.falhas = falhas or {}
        self.antes = antes
        self.textos_ruins = textos_ruins or {}
        self.chamadas = 0
        self.kwargs = []
        self.messages = self

    def create(self, **kwargs):
        self.chamadas += 1
        self.kwargs.append(kwargs)
        if self.antes is not None:
            self.antes(self.chamadas)
        if self.chamadas in self.falhas:
            raise self.falhas[self.chamadas]
        if self.chamadas in self.textos_ruins:
            return Resposta([Bloco("text", self.textos_ruins[self.chamadas])])
        texto = kwargs["messages"][0]["content"]
        return Resposta([Bloco("text", json.dumps(self.saidas[texto]))])


def _erro_401(mensagem="SEGREDO"):
    resposta = httpx2.Response(401, request=httpx2.Request("POST", "https://x"),
                               headers={"request-id": "req_401"})
    return anthropic.APIStatusError(mensagem, response=resposta, body=None)


def _erro_503(mensagem="SEGREDO"):
    resposta = httpx2.Response(503, request=httpx2.Request("POST", "https://x"),
                               headers={"request-id": "req_503"})
    return anthropic.APIStatusError(mensagem, response=resposta, body=None)


def _timeout():
    return anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x"))


# ---------- dados falsos (50 gerais e 15 críticos inventados) ----------

def _avaliacao(conjunto, i, categorias, criticidade):
    return {"id_externo": f"{conjunto[:3]}-{i + 1:03d}", "conjunto": conjunto, "nota": 3,
            "texto": f"texto falso {conjunto} {i + 1}", "categorias": categorias,
            "criticidade": criticidade, "fora_do_escopo": False, "justificativa": "falsa"}


def gerais_falsos():
    return [_avaliacao("geral", i, [CATEGORIAS[i % 6]], []) for i in range(50)]


def criticos_falsos():
    return [_avaliacao("critico", i, [], [CRITICIDADES[i % 3]]) for i in range(15)]


def saidas_do_gabarito(*conjuntos):
    return {a["texto"]: {"categorias": a["categorias"], "criticidade": a["criticidade"]}
            for conjunto in conjuntos for a in conjunto}


def cliente_bom(**kwargs):
    return ClienteEspelho(saidas_do_gabarito(gerais_falsos(), criticos_falsos()), **kwargs)


def _escrever_json(caminho, obj):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


# ---------- fixtures ----------

@pytest.fixture(autouse=True)
def isolar(monkeypatch, tmp_path):
    """Nenhum teste usa caminhos reais; cliente e git são falsos por padrão."""
    base = tmp_path / "sandbox"
    monkeypatch.setattr(ro, "ARQUIVO_GERAL", base / "dados" / "g_inexistente.json")
    monkeypatch.setattr(ro, "ARQUIVO_CRITICO", base / "dados" / "c_inexistente.json")
    monkeypatch.setattr(ro, "ARQUIVO_CONGELAMENTO", base / "registro_falso.md")
    monkeypatch.setattr(ro, "PASTA_RESULTADOS", base / "resultados" / "v1")
    monkeypatch.setattr(ro, "PASTA_ABORTADAS", base / "abortadas")
    monkeypatch.setattr(ro, "ARQUIVO_LOCK", base / "resultados" / "v1.lock")
    monkeypatch.setattr(ro, "git_head", lambda raiz=None: HEAD_FALSO)
    monkeypatch.setattr(ro, "git_alteracoes", lambda raiz=None: [])
    # tag "confere" por padrão e devolve o SHA do commit da tag
    monkeypatch.setattr(ro, "conferir_tag", lambda raiz=None: TAG_SHA_FALSO)
    criados = []
    monkeypatch.setattr(ro, "criar_cliente", lambda: criados.append(1) or pytest.fail("cliente real"))
    # Garantia: nada aponta para dentro do repositório real.
    for nome in ("ARQUIVO_GERAL", "ARQUIVO_CRITICO", "ARQUIVO_CONGELAMENTO",
                 "PASTA_RESULTADOS", "PASTA_ABORTADAS", "ARQUIVO_LOCK"):
        assert not getattr(ro, nome).is_relative_to(RAIZ), nome
    return criados


@pytest.fixture(autouse=True)
def esperas(monkeypatch):
    """Nenhum teste espera de verdade: a espera entre tentativas só registra os segundos."""
    registradas = []
    monkeypatch.setattr(ro, "_dormir", registradas.append)
    return registradas


@pytest.fixture
def clientes_criados(isolar):
    return isolar


def usar_cliente(monkeypatch, cliente, criados):
    def criar():
        criados.append(1)
        return cliente

    monkeypatch.setattr(ro, "criar_cliente", criar)


def gravar_dados(gerais=None, criticos=None):
    _escrever_json(ro.ARQUIVO_GERAL, gerais_falsos() if gerais is None else gerais)
    _escrever_json(ro.ARQUIVO_CRITICO, criticos_falsos() if criticos is None else criticos)


def hashes_dos_dados_falsos():
    """Hash dos conjuntos falsos tal como gravar_dados() os escreve (mesmo JSON canônico)."""
    return {"dados_geral_sha256": hash_canonico(gerais_falsos()),
            "dados_critico_sha256": hash_canonico(criticos_falsos())}


def gravar_registro(alterar=None, status="CONGELADO", versao="v1", remover=()):
    valores = {**hashes_atuais(ro.VERSAO), **hashes_dos_dados_falsos(), **(alterar or {})}
    linhas = [f"status: {status}", f"versao: {versao}"]
    linhas += [f"{k}: `{v}`" for k, v in valores.items() if k not in remover]
    texto = ("# Registro falso\n\n<!-- congelamento:inicio -->\n" + "\n".join(linhas) +
             "\n<!-- congelamento:fim -->\n")
    ro.ARQUIVO_CONGELAMENTO.parent.mkdir(parents=True, exist_ok=True)
    ro.ARQUIVO_CONGELAMENTO.write_text(texto, encoding="utf-8")


def preparar(monkeypatch, criados, cliente=None):
    """Registro congelado correto, dados falsos válidos e cliente falso instalado."""
    gravar_registro()
    gravar_dados()
    cliente = cliente or cliente_bom()
    usar_cliente(monkeypatch, cliente, criados)
    return cliente


def linhas_do_diario(pasta=None):
    caminho = (pasta or ro.PASTA_RESULTADOS) / "diario.jsonl"
    return [json.loads(l) for l in caminho.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------- constantes e isolamento ----------

def test_constantes_padrao_do_modulo():
    assert PADROES["VERSAO"] == "v1"
    assert PADROES["ARQUIVO_GERAL"] == RAIZ / "data" / "geral.json"
    assert PADROES["ARQUIVO_CRITICO"] == RAIZ / "data" / "critico.json"
    assert PADROES["ARQUIVO_CONGELAMENTO"] == RAIZ / "prompts" / "classificador" / "v1.congelamento.md"
    assert PADROES["PASTA_RESULTADOS"] == RAIZ / "avaliacao" / "resultados" / "v1"
    assert PADROES["PASTA_ABORTADAS"] == RAIZ / "avaliacao" / "abortadas"
    assert PADROES["ARQUIVO_LOCK"] == RAIZ / "avaliacao" / "resultados" / "v1.lock"
    assert PADROES["ARQUIVO_LOCK"] == PADROES["PASTA_RESULTADOS"].parent / "v1.lock"
    assert (PADROES["RODADAS"], PADROES["N_GERAL"], PADROES["N_CRITICO"]) == (3, 50, 15)
    assert PADROES["CUSTO_ESTIMADO_POR_CHAMADA"] == pytest.approx(0.4486 / 60)
    assert PADROES["TAG_CONGELAMENTO"] == "classificador-v1-congelado"
    from avaliacao import relatorio_oficial
    assert relatorio_oficial.TAG_CONGELAMENTO == PADROES["TAG_CONGELAMENTO"]  # nome citado no relatório
    assert PADROES["ARQUIVOS_CONGELADOS"] == ["app/classificador.py", "app/contrato.py",
                                              "app/metricas.py"]


def test_nenhum_teste_usa_caminhos_reais(tmp_path):
    for nome in ("ARQUIVO_GERAL", "ARQUIVO_CRITICO", "ARQUIVO_CONGELAMENTO",
                 "PASTA_RESULTADOS", "PASTA_ABORTADAS", "ARQUIVO_LOCK"):
        assert getattr(ro, nome).is_relative_to(tmp_path), nome
        assert getattr(ro, nome) != PADROES[nome]
    assert not ro.ARQUIVO_GERAL.exists() and not ro.ARQUIVO_CRITICO.exists()


# ---------- trava 1: hashes e registro de congelamento ----------

@pytest.mark.parametrize("chave", ["prompt_sha256", "schema_sha256", "parametros_sha256",
                                   "regra_retentativa_sha256", "modelo", "anthropic",
                                   "python_dotenv"])
@pytest.mark.parametrize("executar", [[], ["--executar"]])
def test_hash_divergente_sai_2_sem_criar_cliente(chave, executar, clientes_criados, capsys):
    gravar_registro(alterar={chave: "valor-diferente"})
    assert ro.main(executar) == 2
    assert clientes_criados == []
    assert chave in capsys.readouterr().err
    assert not ro.PASTA_RESULTADOS.exists()


def test_registro_com_versao_diferente_sai_2(clientes_criados):
    gravar_registro(versao="v2")
    assert ro.main(["--executar"]) == 2
    assert clientes_criados == []


@pytest.mark.parametrize("problema", ["status", "chave", "chave_de_dados", "dados_nao_hex",
                                      "ausente"])
def test_registro_invalido_ou_ausente_sai_2(problema, clientes_criados, capsys):
    if problema == "status":
        gravar_registro(status="candidato")
    elif problema == "chave":
        gravar_registro(remover=("schema_sha256",))
    elif problema == "chave_de_dados":
        gravar_registro(remover=("dados_critico_sha256",))
    elif problema == "dados_nao_hex":
        gravar_registro(alterar={"dados_geral_sha256": "isto-nao-e-hash"})
    # "ausente": nenhum arquivo é escrito
    assert ro.main(["--executar"]) == 2
    assert clientes_criados == []
    assert capsys.readouterr().err != ""


# ---------- trava 2: git ----------

@pytest.mark.parametrize("executar", [[], ["--executar"]])
def test_git_sujo_sai_3_sem_cliente_e_sem_abrir_dados(executar, monkeypatch, clientes_criados, capsys):
    gravar_registro()
    monkeypatch.setattr(ro, "git_alteracoes",
                        lambda raiz=None: [" M app/classificador.py", "?? avaliacao/novo.py"])
    assert ro.main(executar) == 3
    assert clientes_criados == []
    # Os arquivos de dados não existem: se algum fosse aberto, main falharia de outro modo.
    assert not ro.ARQUIVO_GERAL.exists() and not ro.ARQUIVO_CRITICO.exists()
    err = capsys.readouterr().err
    assert "app/classificador.py" in err and "avaliacao/novo.py" in err
    assert not ro.PASTA_RESULTADOS.exists()


def test_hash_divergente_vem_antes_do_git_sujo(monkeypatch, clientes_criados):
    gravar_registro(alterar={"modelo": "outro"})
    monkeypatch.setattr(ro, "git_alteracoes", lambda raiz=None: [" M app/x.py"])
    assert ro.main(["--executar"]) == 2


def test_falha_do_git_sai_3(monkeypatch, clientes_criados, capsys):
    gravar_registro()

    def falha(raiz=None):
        raise RuntimeError("git indisponível")

    monkeypatch.setattr(ro, "git_alteracoes", falha)
    assert ro.main(["--executar"]) == 3
    assert clientes_criados == []

    monkeypatch.setattr(ro, "git_alteracoes", lambda raiz=None: [])
    monkeypatch.setattr(ro, "git_head", falha)
    assert ro.main(["--executar"]) == 3
    assert clientes_criados == []


# ---------- dry-run ----------

def test_dry_run_sai_0_sem_abrir_dados_nem_criar_cliente(clientes_criados, capsys):
    gravar_registro()
    assert ro.main([]) == 0
    assert clientes_criados == []
    out = capsys.readouterr().out
    assert "v1" in out
    assert HEAD_FALSO in out
    assert hashes_atuais("v1")["prompt_sha256"] in out
    assert "195" in out and "(50+15)*3" in out
    assert "US$ 1,46" in out  # 195 x 0,4486 / 60
    assert "extrapolado, não garantido" in out
    assert "nada foi aberto nem chamado" in out.lower()
    # avisa o que o dry-run NÃO confere
    assert "tag" in out.lower() and "hash dos dados" in out.lower() and "--executar" in out
    assert not ro.PASTA_RESULTADOS.exists() and not ro.PASTA_ABORTADAS.exists()
    assert not ro.ARQUIVO_GERAL.exists() and not ro.ARQUIVO_CRITICO.exists()
    assert not ro.ARQUIVO_LOCK.exists()


def test_dry_run_nao_confere_tag_nem_hash_dos_dados_nem_abre_dados(monkeypatch, clientes_criados):
    # Registro com hashes de dados que NUNCA bateriam, tag que falharia e dados inexistentes:
    # o dry-run passa mesmo assim, porque só --executar confere essas duas coisas.
    gravar_registro(alterar={"dados_geral_sha256": "0" * 64, "dados_critico_sha256": "1" * 64})
    chamadas = []

    def tag_falha(raiz=None):
        chamadas.append(1)
        raise RuntimeError("tag inexistente")

    monkeypatch.setattr(ro, "conferir_tag", tag_falha)
    monkeypatch.setattr(ro, "hash_canonico", lambda obj: chamadas.append(2) or pytest.fail("hash"))
    monkeypatch.setattr(ro, "carregar_conjunto",
                        lambda *a, **k: chamadas.append(3) or pytest.fail("abriu dados"))
    assert ro.main([]) == 0
    assert chamadas == [] and clientes_criados == []
    assert not ro.ARQUIVO_GERAL.exists() and not ro.ARQUIVO_CRITICO.exists()


def test_dry_run_continua_valendo_hashes_e_git(monkeypatch, clientes_criados):
    gravar_registro(alterar={"modelo": "outro"})
    assert ro.main([]) == 2
    gravar_registro()
    monkeypatch.setattr(ro, "git_alteracoes", lambda raiz=None: [" M app/x.py"])
    assert ro.main([]) == 3


def test_dry_run_ignora_motivo_tecnico_e_nao_mexe_no_diario(clientes_criados, capsys):
    gravar_registro()
    ro.PASTA_RESULTADOS.mkdir(parents=True)
    (ro.PASTA_RESULTADOS / "diario.jsonl").write_text("{}\n", encoding="utf-8")
    assert ro.main(["--motivo-tecnico", "x"]) == 0  # sem --executar nada é arquivado
    assert (ro.PASTA_RESULTADOS / "diario.jsonl").exists()
    assert clientes_criados == []


# ---------- trava 4: o que já existe ----------

def test_recusa_sobrescrever_relatorio_existente(monkeypatch, clientes_criados, capsys):
    preparar(monkeypatch, clientes_criados)
    ro.PASTA_RESULTADOS.mkdir(parents=True)
    (ro.PASTA_RESULTADOS / "relatorio.json").write_text('{"antigo": true}', encoding="utf-8")
    clientes_criados.clear()
    assert ro.main(["--executar"]) == 4
    assert ro.main(["--executar", "--motivo-tecnico", "tentando forçar"]) == 4
    assert clientes_criados == []
    assert (ro.PASTA_RESULTADOS / "relatorio.json").read_text(encoding="utf-8") == '{"antigo": true}'
    assert not ro.PASTA_ABORTADAS.exists()


@pytest.mark.parametrize("fluxo", [["--executar"], ["--so-relatorio"]])
@pytest.mark.parametrize("existente", ["relatorio.json", "relatorio.md"])
def test_relatorio_orfao_sai_4_com_mensagem_de_conferir_manualmente(
        existente, fluxo, monkeypatch, clientes_criados, capsys):
    # Só UM dos dois arquivos existe: a mensagem não pode dizer "avaliação concluída".
    preparar(monkeypatch, clientes_criados)
    ro.PASTA_RESULTADOS.mkdir(parents=True)
    (ro.PASTA_RESULTADOS / existente).write_text("parcial", encoding="utf-8")
    clientes_criados.clear()
    capsys.readouterr()
    assert ro.main(fluxo) == 4
    err = capsys.readouterr().err
    assert MSG_ORFAO in err
    assert "avaliação concluída" not in err
    assert (ro.PASTA_RESULTADOS / existente).read_text(encoding="utf-8") == "parcial"
    assert clientes_criados == [] and not ro.ARQUIVO_LOCK.exists()


@pytest.mark.parametrize("fluxo", [["--executar"], ["--so-relatorio"]])
def test_relatorio_completo_continua_dizendo_avaliacao_concluida(
        fluxo, monkeypatch, clientes_criados, capsys):
    preparar(monkeypatch, clientes_criados)
    ro.PASTA_RESULTADOS.mkdir(parents=True)
    for nome in ("relatorio.json", "relatorio.md"):
        (ro.PASTA_RESULTADOS / nome).write_text("antigo", encoding="utf-8")
    capsys.readouterr()
    assert ro.main(fluxo) == 4
    err = capsys.readouterr().err
    assert "avaliação concluída" in err and MSG_ORFAO not in err


def test_diario_sem_relatorio_exige_motivo_tecnico(monkeypatch, clientes_criados, capsys):
    preparar(monkeypatch, clientes_criados)
    ro.PASTA_RESULTADOS.mkdir(parents=True)
    (ro.PASTA_RESULTADOS / "diario.jsonl").write_text('{"tipo": "inicio"}\n', encoding="utf-8")
    clientes_criados.clear()
    assert ro.main(["--executar"]) == 5
    assert ro.main(["--executar", "--motivo-tecnico", "   "]) == 5  # motivo em branco não vale
    assert clientes_criados == []
    assert "--motivo-tecnico" in capsys.readouterr().err
    assert (ro.PASTA_RESULTADOS / "diario.jsonl").read_text(encoding="utf-8") == '{"tipo": "inicio"}\n'
    assert not ro.PASTA_ABORTADAS.exists()


def test_motivo_tecnico_sem_nada_a_arquivar_sai_5(monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados)
    clientes_criados.clear()
    assert ro.main(["--executar", "--motivo-tecnico", "não há nada"]) == 5
    assert clientes_criados == []
    assert not ro.PASTA_RESULTADOS.exists()


# ---------- trava da tag de congelamento (saída 7), só com --executar ----------

def _tag_falha(motivo="a tag classificador-v1-congelado não existe"):
    def falha(raiz=None):
        raise RuntimeError(motivo)

    return falha


def test_tag_que_falha_sai_7_antes_de_abrir_dados_e_sem_cliente(monkeypatch, clientes_criados, capsys):
    # Registro correto, MAS dados inexistentes (os caminhos do fixture não existem): se algum
    # conjunto fosse aberto, a saída seria 6, não 7.
    gravar_registro()
    monkeypatch.setattr(ro, "conferir_tag", _tag_falha())
    assert not ro.ARQUIVO_GERAL.exists() and not ro.ARQUIVO_CRITICO.exists()
    assert ro.main(["--executar"]) == 7
    assert clientes_criados == []
    err = capsys.readouterr().err
    assert "classificador-v1-congelado" in err
    assert not ro.PASTA_RESULTADOS.exists() and not ro.ARQUIVO_LOCK.exists()


def test_tag_que_falha_nao_toca_no_diario_nem_arquiva(monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados)
    ro.PASTA_RESULTADOS.mkdir(parents=True)
    (ro.PASTA_RESULTADOS / "diario.jsonl").write_text('{"tipo": "inicio"}\n', encoding="utf-8")
    clientes_criados.clear()
    monkeypatch.setattr(ro, "conferir_tag", _tag_falha("HEAD não descende da tag"))
    assert ro.main(["--executar", "--motivo-tecnico", "x"]) == 7
    assert clientes_criados == []
    assert (ro.PASTA_RESULTADOS / "diario.jsonl").read_text(encoding="utf-8") == '{"tipo": "inicio"}\n'
    assert not ro.PASTA_ABORTADAS.exists()


def test_tag_vem_antes_de_recusar_sobrescrever_relatorio(monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados)
    ro.PASTA_RESULTADOS.mkdir(parents=True)
    (ro.PASTA_RESULTADOS / "relatorio.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ro, "conferir_tag", _tag_falha())
    assert ro.main(["--executar"]) == 7


def test_hashes_e_git_vem_antes_da_tag(monkeypatch, clientes_criados):
    chamadas = []
    monkeypatch.setattr(ro, "conferir_tag", lambda raiz=None: chamadas.append(1) or TAG_SHA_FALSO)
    gravar_registro(alterar={"modelo": "outro"})
    assert ro.main(["--executar"]) == 2
    gravar_registro()
    monkeypatch.setattr(ro, "git_alteracoes", lambda raiz=None: [" M app/x.py"])
    assert ro.main(["--executar"]) == 3
    assert chamadas == []  # a tag nem foi consultada


def test_com_executar_a_tag_e_conferida_uma_vez_sobre_o_repositorio(monkeypatch, clientes_criados):
    chamadas = []
    monkeypatch.setattr(ro, "conferir_tag", lambda raiz=None: chamadas.append(raiz) or TAG_SHA_FALSO)
    preparar(monkeypatch, clientes_criados)
    assert ro.main(["--executar"]) == 0
    assert len(chamadas) == 1


# ---------- trava do hash dos dados (saída 8), só com --executar ----------

@pytest.mark.parametrize("divergente, outro", [("geral", "critico"), ("critico", "geral")])
def test_hash_dos_dados_diferente_do_registro_sai_8_sem_cliente_e_sem_tocar_no_diario(
        divergente, outro, monkeypatch, clientes_criados, capsys):
    preparar(monkeypatch, clientes_criados)
    gravar_registro(alterar={f"dados_{divergente}_sha256": "0" * 64})
    ro.PASTA_RESULTADOS.mkdir(parents=True)
    (ro.PASTA_RESULTADOS / "diario.jsonl").write_text('{"tipo": "inicio"}\n', encoding="utf-8")
    clientes_criados.clear()
    assert ro.main(["--executar", "--motivo-tecnico", "x"]) == 8
    assert clientes_criados == []  # o cliente nunca é criado
    assert (ro.PASTA_RESULTADOS / "diario.jsonl").read_text(encoding="utf-8") == '{"tipo": "inicio"}\n'
    assert not ro.PASTA_ABORTADAS.exists() and not ro.ARQUIVO_LOCK.exists()
    err = capsys.readouterr().err
    # diz só QUAL conjunto diverge, nunca conteúdo nem os hashes
    assert f"'{divergente}'" in err and f"'{outro}'" not in err
    assert "texto falso" not in err and "0" * 64 not in err


def test_hash_dos_dois_conjuntos_divergentes_cita_os_dois(monkeypatch, clientes_criados, capsys):
    preparar(monkeypatch, clientes_criados)
    gravar_registro(alterar={"dados_geral_sha256": "0" * 64, "dados_critico_sha256": "1" * 64})
    assert ro.main(["--executar"]) == 8
    err = capsys.readouterr().err
    assert "'geral'" in err and "'critico'" in err
    assert clientes_criados == []


def test_conjunto_modificado_depois_do_congelamento_sai_8(monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados)  # registro com o hash dos dados falsos originais
    gerais = gerais_falsos()
    gerais[0]["texto"] = "um texto que mudou depois do congelamento"
    gravar_dados(gerais=gerais)  # ainda válido, mas diferente do registrado
    assert ro.main(["--executar"]) == 8
    assert clientes_criados == []


def test_hash_dos_dados_e_o_hash_canonico_do_conteudo_do_arquivo(tmp_path):
    from avaliacao.congelamento import hash_dados

    gravar_dados()
    assert hash_dados(ro.ARQUIVO_GERAL) == hash_canonico(ro.carregar_conjunto(
        ro.ARQUIVO_GERAL, "geral", 50)) == hashes_dos_dados_falsos()["dados_geral_sha256"]
    assert hash_dados(ro.ARQUIVO_CRITICO) == hashes_dos_dados_falsos()["dados_critico_sha256"]


def test_conjunto_invalido_vem_antes_do_hash(monkeypatch, clientes_criados):
    # Dados inválidos (49 gerais) E registro divergente: a saída é 6 (inválido), não 8.
    gravar_registro(alterar={"dados_geral_sha256": "0" * 64})
    gravar_dados(gerais=gerais_falsos()[:49])
    assert ro.main(["--executar"]) == 6
    assert clientes_criados == []


# ---------- validar e criar o cliente ANTES de arquivar ----------

def _diario_incompleto_no_lugar():
    ro.PASTA_RESULTADOS.mkdir(parents=True)
    (ro.PASTA_RESULTADOS / "diario.jsonl").write_text(
        '{"tipo": "inicio"}\n{"tipo": "item"}\n', encoding="utf-8")
    return (ro.PASTA_RESULTADOS / "diario.jsonl").read_bytes()


def test_diario_existente_e_conjunto_invalido_sai_6_e_o_diario_fica_no_lugar(
        monkeypatch, clientes_criados, capsys):
    gravar_registro()
    gravar_dados(gerais=gerais_falsos()[:49])
    original = _diario_incompleto_no_lugar()
    assert ro.main(["--executar", "--motivo-tecnico", "queda"]) == 6
    assert clientes_criados == []
    assert (ro.PASTA_RESULTADOS / "diario.jsonl").read_bytes() == original  # NÃO foi movido
    assert not ro.PASTA_ABORTADAS.exists()
    assert not ro.ARQUIVO_LOCK.exists()

    # conserta e repete o MESMO comando: agora arquiva e roda
    gravar_dados()
    cliente = cliente_bom()
    usar_cliente(monkeypatch, cliente, clientes_criados)
    capsys.readouterr()
    assert ro.main(["--executar", "--motivo-tecnico", "queda"]) == 0
    arquivadas = [p for p in ro.PASTA_ABORTADAS.iterdir() if p.is_dir()]
    assert len(arquivadas) == 1
    assert (arquivadas[0] / "diario.jsonl").read_bytes() == original
    assert cliente.chamadas == 195


def test_diario_existente_e_cliente_sem_chave_sai_10_e_o_diario_fica_no_lugar(
        monkeypatch, clientes_criados, capsys):
    gravar_registro()
    gravar_dados()
    original = _diario_incompleto_no_lugar()

    def sem_chave():
        raise SystemExit("ANTHROPIC_API_KEY ausente SEGREDO-NA-MENSAGEM")

    monkeypatch.setattr(ro, "criar_cliente", sem_chave)
    assert ro.main(["--executar", "--motivo-tecnico", "queda"]) == 10
    captura = capsys.readouterr()
    assert captura.err == "cliente não pôde ser criado (chave ausente?)\n"
    assert captura.out == ""
    assert "SEGREDO" not in captura.err and "ANTHROPIC" not in captura.err
    assert (ro.PASTA_RESULTADOS / "diario.jsonl").read_bytes() == original
    assert not ro.PASTA_ABORTADAS.exists()
    assert not ro.ARQUIVO_LOCK.exists()

    # a chave aparece; o MESMO comando arquiva e roda
    cliente = cliente_bom()
    usar_cliente(monkeypatch, cliente, clientes_criados)
    assert ro.main(["--executar", "--motivo-tecnico", "queda"]) == 0
    assert len([p for p in ro.PASTA_ABORTADAS.iterdir() if p.is_dir()]) == 1
    assert cliente.chamadas == 195


def test_cliente_sem_chave_sem_diario_sai_10_sem_criar_pastas(monkeypatch, clientes_criados, capsys):
    gravar_registro()
    gravar_dados()
    monkeypatch.setattr(ro, "criar_cliente", lambda: (_ for _ in ()).throw(SystemExit("x")))
    assert ro.main(["--executar"]) == 10
    assert not ro.PASTA_RESULTADOS.exists() and not ro.PASTA_ABORTADAS.exists()
    assert not ro.ARQUIVO_LOCK.exists()


def test_checagem_do_motivo_continua_antes_de_abrir_dados(monkeypatch, clientes_criados):
    # Sem --motivo-tecnico e com diário incompleto: 5, mesmo com os dados inexistentes.
    gravar_registro()
    _diario_incompleto_no_lugar()
    assert ro.main(["--executar"]) == 5
    assert not ro.ARQUIVO_GERAL.exists()
    assert clientes_criados == []


def test_falha_ao_arquivar_sai_5_remove_o_lock_e_deixa_o_diario(monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados)
    original = _diario_incompleto_no_lugar()

    def falha(motivo):
        raise OSError("disco")

    monkeypatch.setattr(ro, "_arquivar", falha)
    assert ro.main(["--executar", "--motivo-tecnico", "x"]) == 5
    assert (ro.PASTA_RESULTADOS / "diario.jsonl").read_bytes() == original
    assert not ro.ARQUIVO_LOCK.exists()


# ---------- trava de execução: v1.lock (saída 9) ----------

def test_lock_presente_sai_9_sem_tocar_em_nada(monkeypatch, clientes_criados, capsys):
    cliente = preparar(monkeypatch, clientes_criados)
    ro.ARQUIVO_LOCK.parent.mkdir(parents=True, exist_ok=True)
    ro.ARQUIVO_LOCK.write_text("pid 1\nhora de outra execução\n", encoding="utf-8")
    assert ro.main(["--executar"]) == 9
    assert cliente.chamadas == 0
    assert ro.ARQUIVO_LOCK.read_text(encoding="utf-8") == "pid 1\nhora de outra execução\n"
    assert not ro.PASTA_RESULTADOS.exists() and not ro.PASTA_ABORTADAS.exists()
    err = capsys.readouterr().err
    assert "outra execução em andamento ou lock órfão" in err
    assert "remova manualmente" in err


def test_lock_presente_com_diario_anterior_nao_arquiva_nem_move(monkeypatch, clientes_criados):
    # O lock é checado ANTES de arquivar: uma execução em andamento nunca perde o diário.
    cliente = preparar(monkeypatch, clientes_criados)
    original = _diario_incompleto_no_lugar()
    ro.ARQUIVO_LOCK.write_text("pid 1\n", encoding="utf-8")
    assert ro.main(["--executar", "--motivo-tecnico", "tentando"]) == 9
    assert cliente.chamadas == 0
    assert (ro.PASTA_RESULTADOS / "diario.jsonl").read_bytes() == original
    assert not ro.PASTA_ABORTADAS.exists()
    assert ro.ARQUIVO_LOCK.exists()  # não é nosso: não removemos


def test_segunda_execucao_durante_a_primeira_sai_9(monkeypatch, clientes_criados):
    resultados = []

    def antes(n):
        if n == 10:  # no meio da primeira execução, tenta uma segunda
            resultados.append(ro.main(["--executar"]))

    cliente = cliente_bom(antes=antes)
    preparar(monkeypatch, clientes_criados, cliente=cliente)
    assert ro.main(["--executar"]) == 0
    assert resultados == [9]
    assert cliente.chamadas == 195  # a segunda não chamou nada
    assert not ro.ARQUIVO_LOCK.exists()  # a segunda não removeu o lock da primeira (nem sobrou)


def test_lock_guarda_pid_e_hora_e_existe_durante_a_execucao(monkeypatch, clientes_criados):
    vistos = []

    def antes(n):
        if n == 1:
            vistos.append(ro.ARQUIVO_LOCK.read_text(encoding="utf-8"))

    preparar(monkeypatch, clientes_criados, cliente=cliente_bom(antes=antes))
    assert ro.main(["--executar"]) == 0
    assert len(vistos) == 1
    linhas = vistos[0].splitlines()
    assert len(linhas) == 2
    assert linhas[0] == f"pid {os.getpid()}"
    assert datetime.fromisoformat(linhas[1]).utcoffset() == timedelta(hours=-3)


def test_lock_e_removido_depois_do_sucesso(monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados)
    assert ro.main(["--executar"]) == 0
    assert not ro.ARQUIVO_LOCK.exists()


@pytest.mark.parametrize("falha, saida", [
    (_erro_401(), 1),
    (ValueError("x"), 1),
    (KeyboardInterrupt(), 130),
], ids=["permanente", "generico", "ctrl_c"])
def test_lock_e_removido_depois_de_aborto(falha, saida, monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados, cliente=cliente_bom(falhas={5: falha}))
    assert ro.main(["--executar"]) == saida
    assert not ro.ARQUIVO_LOCK.exists()


def test_lock_e_removido_se_o_relatorio_falhar(monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados)
    monkeypatch.setattr(ro, "gerar_relatorio_oficial", lambda e: (_ for _ in ()).throw(ValueError("x")))
    assert ro.main(["--executar"]) == 1
    assert not ro.ARQUIVO_LOCK.exists()


@pytest.mark.parametrize("quebra", ["tag", "hash", "invalido", "cliente", "motivo"])
def test_lock_nao_e_criado_quando_uma_trava_anterior_falha(quebra, monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados)
    argv = ["--executar"]
    if quebra == "tag":
        monkeypatch.setattr(ro, "conferir_tag", _tag_falha())
    elif quebra == "hash":
        gravar_registro(alterar={"dados_geral_sha256": "0" * 64})
    elif quebra == "invalido":
        gravar_dados(gerais=gerais_falsos()[:49])
    elif quebra == "cliente":
        monkeypatch.setattr(ro, "criar_cliente", lambda: (_ for _ in ()).throw(SystemExit("x")))
    else:
        argv += ["--motivo-tecnico", "sem diário"]
    assert ro.main(argv) != 0
    assert not ro.ARQUIVO_LOCK.exists()


def test_lock_e_criado_com_open_no_modo_x(monkeypatch, clientes_criados):
    abertos = []
    real = open

    def espiao(*args, **kwargs):
        if args and Path(str(args[0])) == ro.ARQUIVO_LOCK:
            abertos.append(args[1] if len(args) > 1 else kwargs.get("mode", "r"))
        return real(*args, **kwargs)

    preparar(monkeypatch, clientes_criados)
    monkeypatch.setattr("builtins.open", espiao)
    assert ro.main(["--executar"]) == 0
    assert abertos == ["x"]


# ---------- argparse: sem abreviações ----------

@pytest.mark.parametrize("abreviado", [["--exec"], ["--executa"], ["--motivo", "x"],
                                       ["--so-rel"], ["--executar", "--motivo-tec", "x"]])
def test_abreviacao_de_opcao_e_erro_de_argparse_e_nada_roda(abreviado, monkeypatch, clientes_criados, capsys):
    # Se algo além do argparse rodasse, ler_congelamento (registro ausente) devolveria 2
    # em vez de levantar SystemExit; e o cliente falso derrubaria o teste.
    chamadas = []
    for nome in ("ler_congelamento", "hashes_atuais", "git_alteracoes", "git_head",
                 "conferir_tag", "carregar_conjunto", "carregar_prompt"):
        monkeypatch.setattr(ro, nome, lambda *a, _n=nome, **k: chamadas.append(_n))
    with pytest.raises(SystemExit) as exc:
        ro.main(abreviado)
    assert exc.value.code == 2
    assert chamadas == [] and clientes_criados == []
    assert "unrecognized arguments" in capsys.readouterr().err
    assert not ro.PASTA_RESULTADOS.exists() and not ro.ARQUIVO_LOCK.exists()


def test_opcoes_completas_continuam_valendo(monkeypatch, clientes_criados):
    gravar_registro()
    assert ro.main(["--executar", "--motivo-tecnico", "x"]) == 5  # chegou às travas, sem diário


# ---------- o prompt é lido UMA vez ----------

def test_prompt_e_lido_uma_vez_e_o_hash_conferido_e_o_da_string_enviada(monkeypatch, clientes_criados):
    leituras = []
    textos = ["PROMPT LIDO NA PRIMEIRA VEZ\n", "PROMPT DIFERENTE NA SEGUNDA LEITURA\n"]

    def carregar(versao):
        leituras.append(versao)
        return textos[min(len(leituras), 2) - 1]

    # o registro congela o hash do PRIMEIRO texto; se o código lesse de novo (segunda leitura),
    # ou hashes e envio viessem de strings diferentes, o teste falharia
    gravar_registro(alterar={"prompt_sha256": sha256_do_texto(textos[0])})
    gravar_dados()
    cliente = cliente_bom()
    usar_cliente(monkeypatch, cliente, clientes_criados)
    # as duas referências: a do executor e a que sha256_do_prompt usaria (depois de gravar o
    # registro, que também lê o prompt)
    monkeypatch.setattr(ro, "carregar_prompt", carregar)
    monkeypatch.setattr(classificador, "carregar_prompt", carregar)

    assert ro.main(["--executar"]) == 0
    assert leituras == ["v1"]  # exatamente uma leitura
    assert cliente.chamadas == 195
    assert all(k["system"] == textos[0] for k in cliente.kwargs)  # mesma string em todas as chamadas
    inicio = linhas_do_diario()[0]
    assert inicio["prompt_sha256"] == sha256_do_texto(textos[0])
    dados = json.loads((ro.PASTA_RESULTADOS / "relatorio.json").read_text(encoding="utf-8"))
    assert dados["hashes"]["prompt_sha256"] == sha256_do_texto(textos[0])


def test_prompt_e_lido_uma_vez_tambem_no_dry_run(monkeypatch, clientes_criados):
    leituras = []
    texto = "PROMPT DO DRY-RUN\n"
    gravar_registro(alterar={"prompt_sha256": sha256_do_texto(texto)})
    monkeypatch.setattr(ro, "carregar_prompt", lambda v: leituras.append(v) or texto)
    monkeypatch.setattr(classificador, "carregar_prompt", lambda v: leituras.append(v) or texto)
    assert ro.main([]) == 0
    assert leituras == ["v1"]


def test_prompt_alterado_entre_o_registro_e_a_execucao_sai_2(monkeypatch, clientes_criados):
    gravar_registro(alterar={"prompt_sha256": sha256_do_texto("outro texto")})
    assert ro.main(["--executar"]) == 2
    assert clientes_criados == []


# ---------- validação dos conjuntos ----------

def _arquivo_com(tmp_path, itens, nome="c.json"):
    caminho = tmp_path / nome
    _escrever_json(caminho, itens)
    return caminho


def test_carregar_conjunto_aceita_os_conjuntos_validos(tmp_path):
    g = ro.carregar_conjunto(_arquivo_com(tmp_path, gerais_falsos(), "g.json"), "geral", 50)
    c = ro.carregar_conjunto(_arquivo_com(tmp_path, criticos_falsos(), "c.json"), "critico", 15)
    assert len(g) == 50 and len(c) == 15
    assert g[0]["id_externo"] == "ger-001" and c[-1]["id_externo"] == "cri-015"


def test_carregar_conjunto_rejeita_nao_lista(tmp_path):
    with pytest.raises(ValueError):
        ro.carregar_conjunto(_arquivo_com(tmp_path, {"a": 1}), "geral", 50)


def test_carregar_conjunto_rejeita_conjunto_errado(tmp_path):
    itens = gerais_falsos()
    itens[7]["conjunto"] = "critico"
    with pytest.raises(ValueError, match="conjunto"):
        ro.carregar_conjunto(_arquivo_com(tmp_path, itens), "geral", 50)
    # e o nome pedido também conta: itens gerais não valem como críticos
    with pytest.raises(ValueError, match="conjunto"):
        ro.carregar_conjunto(_arquivo_com(tmp_path, gerais_falsos()), "critico", 50)


@pytest.mark.parametrize("quantidade", [0, 49, 51])
def test_carregar_conjunto_rejeita_contagem_diferente_de_50(tmp_path, quantidade):
    itens = [_avaliacao("geral", i, [], []) for i in range(quantidade)]
    with pytest.raises(ValueError, match="50"):
        ro.carregar_conjunto(_arquivo_com(tmp_path, itens), "geral", 50)


def test_carregar_conjunto_rejeita_contagem_diferente_de_15(tmp_path):
    itens = [_avaliacao("critico", i, [], ["saude"]) for i in range(14)]
    with pytest.raises(ValueError, match="15"):
        ro.carregar_conjunto(_arquivo_com(tmp_path, itens), "critico", 15)


def test_carregar_conjunto_rejeita_id_repetido(tmp_path):
    itens = gerais_falsos()
    itens[3]["id_externo"] = itens[2]["id_externo"]
    with pytest.raises(ValueError, match="repetido"):
        ro.carregar_conjunto(_arquivo_com(tmp_path, itens), "geral", 50)


def test_carregar_conjunto_rejeita_geral_com_criticidade(tmp_path):
    itens = gerais_falsos()
    itens[0]["criticidade"] = ["saude"]
    with pytest.raises(ValueError, match="criticidade"):
        ro.carregar_conjunto(_arquivo_com(tmp_path, itens), "geral", 50)


def test_carregar_conjunto_rejeita_critico_sem_criticidade(tmp_path):
    itens = criticos_falsos()
    itens[0]["criticidade"] = []
    with pytest.raises(ValueError, match="criticidade"):
        ro.carregar_conjunto(_arquivo_com(tmp_path, itens), "critico", 15)


def test_carregar_conjunto_rejeita_rotulo_fora_do_contrato(tmp_path):
    itens = gerais_falsos()
    itens[0]["categorias"] = ["preco"]
    with pytest.raises(ValueError):
        ro.carregar_conjunto(_arquivo_com(tmp_path, itens), "geral", 50)


@pytest.mark.parametrize("quebra", ["sem_texto", "item_nao_objeto", "sem_id"])
def test_carregar_conjunto_rejeita_item_malformado(tmp_path, quebra):
    itens = gerais_falsos()
    if quebra == "sem_texto":
        del itens[0]["texto"]
    elif quebra == "item_nao_objeto":
        itens[0] = "isto não é um objeto"
    else:
        del itens[0]["id_externo"]
    with pytest.raises(ValueError):
        ro.carregar_conjunto(_arquivo_com(tmp_path, itens), "geral", 50)


def test_carregar_conjunto_nome_desconhecido(tmp_path):
    with pytest.raises(ValueError):
        ro.carregar_conjunto(_arquivo_com(tmp_path, gerais_falsos()), "desenvolvimento", 50)


def test_main_com_conjunto_invalido_sai_6_sem_cliente_nem_diario(monkeypatch, clientes_criados, capsys):
    gravar_registro()
    gravar_dados(gerais=gerais_falsos()[:49])
    assert ro.main(["--executar"]) == 6
    assert clientes_criados == []
    assert not (ro.PASTA_RESULTADOS / "diario.jsonl").exists()
    assert "texto falso" not in capsys.readouterr().err


# ---------- execução completa ----------

def test_execucao_completa_feliz(monkeypatch, clientes_criados, capsys, esperas):
    cliente = preparar(monkeypatch, clientes_criados)
    assert ro.main(["--executar"]) == 0
    assert clientes_criados == [1]
    assert cliente.chamadas == 195

    # requisição: só o texto, com o prompt e os parâmetros congelados
    prompt = carregar_prompt("v1")
    primeira = cliente.kwargs[0]
    assert primeira == {**PARAMETROS, "system": prompt,
                        "messages": [{"role": "user", "content": "texto falso geral 1"}]}
    assert "ger-001" not in repr(cliente.kwargs)

    # diário: 1 início + 195 itens + 1 fim concluído
    linhas = linhas_do_diario()
    assert len(linhas) == 197
    assert [l["tipo"] for l in linhas] == ["inicio"] + ["item"] * 195 + ["fim"]
    inicio, fim = linhas[0], linhas[-1]
    assert inicio["versao"] == "v1" and inicio["git_head"] == HEAD_FALSO
    assert inicio["tag_sha"] == TAG_SHA_FALSO  # commit apontado pela tag, além do git_head
    assert inicio["iniciado_em"].endswith("-03:00")
    for chave, valor in {**hashes_atuais("v1"), **hashes_dos_dados_falsos()}.items():
        assert inicio[chave] == valor  # inclui regra de retentativa e os dois hashes dos dados
    assert fim["status"] == "concluido" and fim["concluido_em"].endswith("-03:00")
    item = linhas[1]
    assert set(item) == {"tipo", "rodada", "conjunto", "id", "texto", "gabarito", "previsao",
                         "tentativas", "erros", "usos", "modelos"}
    assert item["usos"] == [{"tentativa": 1, "input_tokens": 2500, "output_tokens": 40,
                             "modelo": MODELO_FALSO}]
    assert item["modelos"] == [MODELO_FALSO]  # conjunto ordenado dos modelos informados pela API

    # ordem: rodada -> geral -> crítico
    esperada = []
    for r in (1, 2, 3):
        esperada += [(r, "geral")] * 50 + [(r, "critico")] * 15
    assert [(l["rodada"], l["conjunto"]) for l in linhas[1:-1]] == esperada
    assert [l["id"] for l in linhas[1:51]] == [a["id_externo"] for a in gerais_falsos()]

    # relatórios
    md = (ro.PASTA_RESULTADOS / "relatorio.md").read_text(encoding="utf-8")
    dados = json.loads((ro.PASTA_RESULTADOS / "relatorio.json").read_text(encoding="utf-8"))
    assert "Recall geral: 15 de 15" in md
    assert dados["rodadas"][0]["gerais"]["f1"]["micro"]["f1"] == 1.0
    assert dados["chamadas_realizadas"] == 195
    for chave, valor in {**hashes_atuais("v1"), **hashes_dos_dados_falsos()}.items():
        assert dados["hashes"][chave] == valor
    assert hashes_dos_dados_falsos()["dados_geral_sha256"] in md
    assert dados["gerado_por_so_relatorio"] is False and "so-relatorio" not in md
    # commit da tag, HEAD da execução e modelos informados pela API no relatório
    assert dados["tag_sha"] == TAG_SHA_FALSO and dados["git_head"] == HEAD_FALSO
    assert f"- Commit da tag classificador-v1-congelado: {TAG_SHA_FALSO}" in md
    assert f"- HEAD da execução: {HEAD_FALSO}" in md
    assert dados["modelos_informados"] == [MODELO_FALSO]
    assert f"- Modelos informados pela API: {MODELO_FALSO}" in md
    assert dados["saidas_invalidas_total"] == 0 and dados["chamadas_planejadas"] == 195
    assert not ro.ARQUIVO_LOCK.exists()
    assert esperas == []  # nenhuma retentativa, nenhuma espera

    # saída: só a linha com os caminhos
    capturado = capsys.readouterr()
    assert capturado.err == ""
    assert capturado.out.count("\n") == 1
    assert str(ro.PASTA_RESULTADOS / "relatorio.md") in capturado.out
    assert str(ro.PASTA_RESULTADOS / "relatorio.json") in capturado.out
    for proibido in ("F1", "recall", "Recall", "texto falso", "ger-0"):
        assert proibido not in capturado.out
    assert not list(ro.PASTA_RESULTADOS.glob("*.tmp"))


def test_relatorio_gravado_cita_o_git_head_recebido(monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados)
    monkeypatch.setattr(ro, "git_head", lambda raiz=None: "1234abcd" * 5)
    assert ro.main(["--executar"]) == 0
    dados = json.loads((ro.PASTA_RESULTADOS / "relatorio.json").read_text(encoding="utf-8"))
    md = (ro.PASTA_RESULTADOS / "relatorio.md").read_text(encoding="utf-8")
    assert dados["git_head"] == "1234abcd" * 5
    assert "1234abcd" * 5 in md
    assert dados["hashes"]["prompt_sha256"] == hashes_atuais("v1")["prompt_sha256"]
    assert dados["modelo"] == PARAMETROS["model"]
    assert dados["iniciado_em"].endswith("-03:00") and dados["concluido_em"].endswith("-03:00")
    assert linhas_do_diario()[0]["git_head"] == "1234abcd" * 5


def test_diario_e_gravado_item_a_item_com_flush_e_fsync(monkeypatch, clientes_criados):
    estado = []

    def antes(n):
        itens = [l for l in linhas_do_diario() if l["tipo"] == "item"]
        tipos = [l["tipo"] for l in linhas_do_diario()]
        estado.append((n, len(itens), tipos[0]))

    preparar(monkeypatch, clientes_criados, cliente=cliente_bom(antes=antes))
    sincronizados = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: sincronizados.append(fd) or real(fd))
    assert ro.main(["--executar"]) == 0
    assert len(estado) == 195
    # na chamada N o diário já tem o início e exatamente N-1 itens
    assert all(itens == n - 1 and primeiro == "inicio" for n, itens, primeiro in estado)
    assert len(sincronizados) >= 197  # início + 195 itens + fim


def test_diario_usa_modo_append_e_quebra_de_linha_lf(monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados)
    assert ro.main(["--executar"]) == 0
    bruto = (ro.PASTA_RESULTADOS / "diario.jsonl").read_bytes()
    assert b"\r" not in bruto
    assert bruto.endswith(b"\n")


# ---------- abortos ----------

def _depois_de_abortar(capsys, antes_do_aborto_itens, esperado_causa):
    linhas = linhas_do_diario()
    assert [l["tipo"] for l in linhas] == ["inicio"] + ["item"] * antes_do_aborto_itens + ["fim"]
    fim = linhas[-1]
    assert fim["status"] == "abortado"
    assert fim["causa"] == esperado_causa
    assert not (ro.PASTA_RESULTADOS / "relatorio.md").exists()
    assert not (ro.PASTA_RESULTADOS / "relatorio.json").exists()
    bruto = (ro.PASTA_RESULTADOS / "diario.jsonl").read_text(encoding="utf-8")
    assert "SEGREDO" not in bruto
    capturado = capsys.readouterr()
    assert capturado.out == ""
    assert capturado.err == MSG_ABORTO
    assert "SEGREDO" not in capturado.out + capturado.err


def test_erro_4xx_na_chamada_80_aborta_sem_relatorio(monkeypatch, clientes_criados, capsys):
    cliente = cliente_bom(falhas={80: _erro_401("SEGREDO na mensagem")})
    preparar(monkeypatch, clientes_criados, cliente=cliente)
    assert ro.main(["--executar"]) == 1
    assert cliente.chamadas == 80  # nenhuma repetição, nenhuma chamada depois
    _depois_de_abortar(capsys, 79, {"tipo": "ErroPermanenteDaAPI", "status": 401,
                                    "request_id": "req_401"})
    assert [l["id"] for l in linhas_do_diario()[1:66]][:3] == ["ger-001", "ger-002", "ger-003"]


@pytest.mark.parametrize("falha, status, request_id", [
    (_timeout, None, None),
    (_erro_503, 503, "req_503"),
], ids=["timeout", "status_503"])
def test_erro_tecnico_esgotado_na_chamada_80_aborta_como_o_permanente(
        falha, status, request_id, monkeypatch, clientes_criados, capsys, esperas):
    # A chamada 80 e as duas retentativas (81 e 82) falham com erro técnico transitório.
    cliente = cliente_bom(falhas={80: falha(), 81: falha(), 82: falha()})
    preparar(monkeypatch, clientes_criados, cliente=cliente)
    assert ro.main(["--executar"]) == 1
    assert cliente.chamadas == 82  # exatamente 3 chamadas no item 80; nada depois
    assert esperas == [2, 5]
    # o diário guarda SÓ tipo, status e request_id (mesma forma do erro permanente)
    _depois_de_abortar(capsys, 79, {"tipo": "ErroTecnicoEsgotado", "status": status,
                                    "request_id": request_id})
    assert not ro.ARQUIVO_LOCK.exists()
    # o item 80 NÃO virou saída inválida: não há linha de item para ele. O último item gravado é o
    # 79º (rodada 2, 14º item, ger-014); o 80º seria ger-015.
    ultimo = [l for l in linhas_do_diario() if l["tipo"] == "item"][-1]
    assert (ultimo["rodada"], ultimo["id"]) == (2, "ger-014")


def test_erro_tecnico_esgotado_nao_vaza_mensagem_nem_corpo(monkeypatch, clientes_criados, capsys):
    falha = _erro_503("SEGREDO na mensagem")
    cliente = cliente_bom(falhas={10: falha, 11: _erro_503("SEGREDO"), 12: _erro_503("SEGREDO")})
    preparar(monkeypatch, clientes_criados, cliente=cliente)
    assert ro.main(["--executar"]) == 1
    capturado = capsys.readouterr()
    bruto = (ro.PASTA_RESULTADOS / "diario.jsonl").read_text(encoding="utf-8")
    assert "SEGREDO" not in capturado.out + capturado.err + bruto
    assert capturado.err == MSG_ABORTO and capturado.out == ""
    fim = linhas_do_diario()[-1]
    assert set(fim) == {"tipo", "status", "causa"}
    assert set(fim["causa"]) == {"tipo", "status", "request_id"}


def test_erro_permanente_grava_so_tipo_status_e_request_id(monkeypatch, clientes_criados, capsys):
    preparar(monkeypatch, clientes_criados, cliente=cliente_bom(falhas={3: _erro_401()}))
    assert ro.main(["--executar"]) == 1
    assert set(linhas_do_diario()[-1]["causa"]) == {"tipo", "status", "request_id"}


def test_erro_tecnico_esgotado_depois_aborta_e_refazer_exige_motivo(monkeypatch, clientes_criados, capsys):
    cliente = cliente_bom(falhas={5: _timeout(), 6: _timeout(), 7: _timeout()})
    preparar(monkeypatch, clientes_criados, cliente=cliente)
    assert ro.main(["--executar"]) == 1
    capsys.readouterr()
    usar_cliente(monkeypatch, cliente_bom(), clientes_criados)
    assert ro.main(["--executar"]) == 5
    assert ro.main(["--executar", "--motivo-tecnico", "queda de rede simulada"]) == 0
    dados = json.loads((ro.PASTA_RESULTADOS / "relatorio.json").read_text(encoding="utf-8"))
    assert "ErroTecnicoEsgotado" in dados["abortadas"][0]["causa"]


def test_falha_de_conteudo_esgotada_vira_saida_invalida_sem_abortar(monkeypatch, clientes_criados, esperas):
    # Chamadas 10, 11 e 12: JSON inválido nas três tentativas do 10º item -> previsão None.
    cliente = cliente_bom(textos_ruins={10: "{ruim", 11: "{ruim", 12: "{ruim"})
    preparar(monkeypatch, clientes_criados, cliente=cliente)
    assert ro.main(["--executar"]) == 0
    assert cliente.chamadas == 197  # 195 + 2 retentativas
    assert esperas == [2, 5]
    item10 = [l for l in linhas_do_diario() if l["tipo"] == "item"][9]
    assert item10["id"] == "ger-010" and item10["previsao"] is None and item10["tentativas"] == 3
    assert [e["tipo"] for e in item10["erros"]] == ["json_invalido"] * 3
    dados = json.loads((ro.PASTA_RESULTADOS / "relatorio.json").read_text(encoding="utf-8"))
    assert dados["rodadas"][0]["auxiliar_invalidas_por_rodada"]["na_rodada"] == 1
    assert dados["erros"][0]["gerais"][0]["tipos_de_erro"] == [
        {"tentativa": 1, "tipo": "json_invalido"}, {"tentativa": 2, "tipo": "json_invalido"},
        {"tentativa": 3, "tipo": "json_invalido"}]


def test_keyboard_interrupt_na_chamada_50_marca_o_diario_e_sai_130(monkeypatch, clientes_criados, capsys):
    cliente = cliente_bom(falhas={50: KeyboardInterrupt()})
    preparar(monkeypatch, clientes_criados, cliente=cliente)
    assert ro.main(["--executar"]) == 130
    _depois_de_abortar(capsys, 49, {"tipo": "KeyboardInterrupt"})


def test_erro_generico_aborta_sem_relatorio(monkeypatch, clientes_criados, capsys):
    cliente = cliente_bom(falhas={10: ValueError("SEGREDO no erro genérico")})
    preparar(monkeypatch, clientes_criados, cliente=cliente)
    assert ro.main(["--executar"]) == 1
    _depois_de_abortar(capsys, 9, {"tipo": "ValueError"})


def test_aborto_nao_imprime_metricas_nem_previsoes(monkeypatch, clientes_criados, capsys):
    cliente = cliente_bom(falhas={100: _erro_401()})
    preparar(monkeypatch, clientes_criados, cliente=cliente)
    assert ro.main(["--executar"]) == 1
    capturado = capsys.readouterr()
    texto = capturado.out + capturado.err
    for proibido in ("F1", "recall", "Recall", "categorias", "texto falso", "ger-", "99", "100"):
        assert proibido not in texto


def test_falha_ao_marcar_o_diario_nao_esconde_o_aborto(monkeypatch, clientes_criados, capsys):
    """Marcar o fim é melhor esforço: se a gravação falhar, o aborto continua sendo o erro."""
    cliente = cliente_bom(falhas={5: ValueError("falha")})
    preparar(monkeypatch, clientes_criados, cliente=cliente)

    def fsync_quebrado(fd):
        # O início e os 4 itens (5 linhas) gravam normalmente; a marcação de fim (6ª) falha.
        if len(linhas_do_diario()) >= 6:
            raise OSError("disco cheio")

    monkeypatch.setattr(os, "fsync", fsync_quebrado)
    assert ro.main(["--executar"]) == 1
    assert capsys.readouterr().err == MSG_ABORTO


# ---------- execuções abortadas anteriores ----------

def _arquivar_falso(nome, fim=None, motivo=None):
    pasta = ro.PASTA_ABORTADAS / nome
    pasta.mkdir(parents=True)
    linhas = [{"tipo": "inicio"}, {"tipo": "item", "id": "x"}]
    if fim is not None:
        linhas.append(fim)
    (pasta / "diario.jsonl").write_text("\n".join(json.dumps(l) for l in linhas) + "\n",
                                        encoding="utf-8")
    if motivo is not None:
        (pasta / "motivo.json").write_text(json.dumps(motivo), encoding="utf-8")
    return pasta


def test_relatorio_sem_abortadas_diz_zero(monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados)
    assert ro.main(["--executar"]) == 0
    md = (ro.PASTA_RESULTADOS / "relatorio.md").read_text(encoding="utf-8")
    dados = json.loads((ro.PASTA_RESULTADOS / "relatorio.json").read_text(encoding="utf-8"))
    assert "Execuções abortadas anteriores: 0" in md
    assert dados["abortadas"] == []


def test_relatorio_lista_abortadas_anteriores_com_causa_e_motivo(monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados)
    _arquivar_falso(
        "v1_20261030T081500",
        fim={"tipo": "fim", "status": "abortado",
             "causa": {"tipo": "ErroPermanenteDaAPI", "status": 401, "request_id": "req_1"}},
        motivo={"motivo": "chave expirada", "arquivada_em": "2026-10-30T08:20:00-03:00"})
    _arquivar_falso("v1_20261030T083000")  # sem marcação de fim e sem motivo
    (ro.PASTA_ABORTADAS / "arquivo_solto.txt").write_text("não é pasta", encoding="utf-8")

    assert ro.main(["--executar"]) == 0
    dados = json.loads((ro.PASTA_RESULTADOS / "relatorio.json").read_text(encoding="utf-8"))
    md = (ro.PASTA_RESULTADOS / "relatorio.md").read_text(encoding="utf-8")
    assert [a["pasta"] for a in dados["abortadas"]] == ["v1_20261030T081500", "v1_20261030T083000"]
    primeira, segunda = dados["abortadas"]
    assert "abortado" in primeira["causa"] and "ErroPermanenteDaAPI" in primeira["causa"]
    assert "401" in primeira["causa"]
    assert primeira["motivo"] == "chave expirada"
    assert segunda["causa"] == "sem marcação de fim"
    assert segunda["motivo"] == "sem motivo registrado"
    assert "Execuções abortadas anteriores: 2" in md
    assert "chave expirada" in md and "sem marcação de fim" in md


def test_aborto_e_nova_avaliacao_com_motivo_tecnico(monkeypatch, clientes_criados, capsys):
    # 1) primeira tentativa aborta na chamada 5 (4 itens gravados)
    cliente_ruim = cliente_bom(falhas={5: _erro_401()})
    preparar(monkeypatch, clientes_criados, cliente=cliente_ruim)
    assert ro.main(["--executar"]) == 1
    assert len(linhas_do_diario()) == 1 + 4 + 1
    capsys.readouterr()

    # 2) refazer sem motivo é recusado, e nada muda
    antes = (ro.PASTA_RESULTADOS / "diario.jsonl").read_bytes()
    bom = cliente_bom()
    usar_cliente(monkeypatch, bom, clientes_criados)
    assert ro.main(["--executar"]) == 5
    assert bom.chamadas == 0
    assert (ro.PASTA_RESULTADOS / "diario.jsonl").read_bytes() == antes
    capsys.readouterr()

    # 3) com --motivo-tecnico: o diário antigo é MOVIDO (não apagado) e a avaliação roda do zero
    assert ro.main(["--executar", "--motivo-tecnico", "queda de conexão simulada"]) == 0
    assert bom.chamadas == 195

    arquivadas = [p for p in ro.PASTA_ABORTADAS.iterdir() if p.is_dir()]
    assert len(arquivadas) == 1
    pasta = arquivadas[0]
    assert re.fullmatch(r"v1_\d{8}T\d{6}", pasta.name)
    assert (pasta / "diario.jsonl").read_bytes() == antes  # nada foi apagado nem alterado
    motivo = json.loads((pasta / "motivo.json").read_text(encoding="utf-8"))
    assert set(motivo) == {"motivo", "arquivada_em"}
    assert motivo["motivo"] == "queda de conexão simulada"
    assert datetime.fromisoformat(motivo["arquivada_em"]).utcoffset() == timedelta(hours=-3)

    novo = linhas_do_diario()
    assert len(novo) == 197 and novo[0]["tipo"] == "inicio" and novo[-1]["status"] == "concluido"

    dados = json.loads((ro.PASTA_RESULTADOS / "relatorio.json").read_text(encoding="utf-8"))
    assert len(dados["abortadas"]) == 1
    assert dados["abortadas"][0]["pasta"] == pasta.name
    assert dados["abortadas"][0]["motivo"] == "queda de conexão simulada"
    assert "ErroPermanenteDaAPI" in dados["abortadas"][0]["causa"]
    md = (ro.PASTA_RESULTADOS / "relatorio.md").read_text(encoding="utf-8")
    assert "Execuções abortadas anteriores: 1" in md and "queda de conexão simulada" in md

    # 4) agora existe avaliação concluída: não sobrescreve
    assert ro.main(["--executar", "--motivo-tecnico", "de novo"]) == 4


def test_diario_concluido_sem_relatorio_tambem_exige_motivo(monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados)
    ro.PASTA_RESULTADOS.mkdir(parents=True)
    linhas = [{"tipo": "inicio"}, {"tipo": "fim", "status": "concluido"}]
    (ro.PASTA_RESULTADOS / "diario.jsonl").write_text(
        "\n".join(json.dumps(l) for l in linhas) + "\n", encoding="utf-8")
    clientes_criados.clear()
    assert ro.main(["--executar"]) == 5
    assert clientes_criados == []


# ---------- gravação dos relatórios: JSON antes do MD, com limpeza ----------

def _sem_sobras(pasta):
    return not list(pasta.glob("*.tmp"))


def test_relatorio_json_e_gravado_antes_do_md(monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados)
    ordem = []
    real = ro._gravar_atomico
    monkeypatch.setattr(ro, "_gravar_atomico",
                        lambda caminho, texto: ordem.append(caminho.name) or real(caminho, texto))
    assert ro.main(["--executar"]) == 0
    assert ordem == ["relatorio.json", "relatorio.md"]


def test_se_o_md_falhar_o_json_e_removido_e_o_diario_fica(monkeypatch, clientes_criados, capsys):
    preparar(monkeypatch, clientes_criados)
    ordem = []
    real = ro._gravar_atomico

    def espiao(caminho, texto):
        ordem.append(caminho.name)
        if caminho.name == "relatorio.md":
            raise OSError("disco cheio SEGREDO")
        real(caminho, texto)

    monkeypatch.setattr(ro, "_gravar_atomico", espiao)
    assert ro.main(["--executar"]) == 1
    assert ordem == ["relatorio.json", "relatorio.md"]
    assert not (ro.PASTA_RESULTADOS / "relatorio.json").exists()
    assert not (ro.PASTA_RESULTADOS / "relatorio.md").exists()
    assert _sem_sobras(ro.PASTA_RESULTADOS)
    assert linhas_do_diario()[-1]["status"] == "concluido"  # o diário continua inteiro
    err = capsys.readouterr().err
    assert "SEGREDO" not in err and "OSError" in err
    assert not ro.ARQUIVO_LOCK.exists()


def test_se_o_json_falhar_nada_e_criado(monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados)
    ordem = []

    def falha(caminho, texto):
        ordem.append(caminho.name)
        raise OSError("sem permissão")

    monkeypatch.setattr(ro, "_gravar_atomico", falha)
    assert ro.main(["--executar"]) == 1
    assert ordem == ["relatorio.json"]  # o MD nem é tentado
    assert not (ro.PASTA_RESULTADOS / "relatorio.json").exists()
    assert not (ro.PASTA_RESULTADOS / "relatorio.md").exists()


def test_falha_no_rename_do_md_nao_deixa_json_nem_tmp(monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados)
    real = os.replace

    def replace_falha(origem, destino):
        if Path(destino).name == "relatorio.md":
            raise OSError("rename falhou")
        return real(origem, destino)

    monkeypatch.setattr(os, "replace", replace_falha)
    assert ro.main(["--executar"]) == 1
    assert not (ro.PASTA_RESULTADOS / "relatorio.json").exists()
    assert not (ro.PASTA_RESULTADOS / "relatorio.md").exists()
    assert _sem_sobras(ro.PASTA_RESULTADOS)


# ---------- --so-relatorio: relatório a partir de um diário completo ----------

def cliente_variado():
    """Cliente com desvios para os números não serem triviais: um crítico não detectado, um
    falso alerta, uma saída inválida (3 respostas ruins) e uma retentativa que dá certo."""
    saidas = saidas_do_gabarito(gerais_falsos(), criticos_falsos())
    saidas["texto falso critico 5"] = {"categorias": [], "criticidade": []}
    saidas["texto falso geral 20"] = {"categorias": [CATEGORIAS[19 % 6]], "criticidade": ["saude"]}
    return ClienteEspelho(saidas, textos_ruins={10: "{ruim", 11: "{ruim", 12: "{ruim", 20: "{ruim"})


def diario_valido(monkeypatch, criados):
    """Roda a avaliação falsa inteira e devolve (linhas do diário, dados, markdown).

    Depois APAGA o relatório gerado, deixando o diário completo e concluído SEM relatório.
    """
    preparar(monkeypatch, criados, cliente=cliente_variado())
    assert ro.main(["--executar"]) == 0
    dados = json.loads((ro.PASTA_RESULTADOS / "relatorio.json").read_text(encoding="utf-8"))
    md = (ro.PASTA_RESULTADOS / "relatorio.md").read_text(encoding="utf-8")
    (ro.PASTA_RESULTADOS / "relatorio.json").unlink()
    (ro.PASTA_RESULTADOS / "relatorio.md").unlink()
    return linhas_do_diario(), dados, md


def escrever_diario(linhas):
    """Regrava o diário. Entradas str são gravadas como estão (para linhas corrompidas)."""
    with open(ro.PASTA_RESULTADOS / "diario.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for linha in linhas:
            f.write((linha if isinstance(linha, str) else json.dumps(linha, ensure_ascii=False)) + "\n")


def bloquear_api_e_dados(monkeypatch):
    """--so-relatorio nunca cria cliente, nunca chama o classificador e nunca abre os dados."""
    def proibido(*a, **k):
        pytest.fail("--so-relatorio não pode criar cliente, chamar a API nem abrir os dados")

    monkeypatch.setattr(ro, "criar_cliente", proibido)
    monkeypatch.setattr(ro, "classificar", proibido)
    monkeypatch.setattr(ro, "carregar_conjunto", proibido)
    for arquivo in (ro.ARQUIVO_GERAL, ro.ARQUIVO_CRITICO):
        arquivo.unlink(missing_ok=True)  # os arquivos de dados deixam de existir


MARCA_SO_RELATORIO = ("Gerado por --so-relatorio, a partir do diário da execução já concluída "
                      "(nenhuma chamada à API foi feita). Commit atual (git HEAD): {head}.")


def test_so_relatorio_gera_o_mesmo_relatorio_do_fluxo_normal(monkeypatch, clientes_criados, capsys):
    linhas, dados_normal, md_normal = diario_valido(monkeypatch, clientes_criados)
    assert "saída inválida" in md_normal  # o cenário tem saída inválida e retentativa
    capsys.readouterr()
    bloquear_api_e_dados(monkeypatch)
    head_atual = "9999aaaa" * 5
    monkeypatch.setattr(ro, "git_head", lambda raiz=None: head_atual)

    assert ro.main(["--so-relatorio"]) == 0

    captura = capsys.readouterr()
    assert captura.err == ""
    assert captura.out.count("\n") == 1  # só os caminhos
    assert str(ro.PASTA_RESULTADOS / "relatorio.md") in captura.out
    assert str(ro.PASTA_RESULTADOS / "relatorio.json") in captura.out
    for proibido in ("F1", "ecall", "texto falso", "ger-0"):
        assert proibido not in captura.out

    dados = json.loads((ro.PASTA_RESULTADOS / "relatorio.json").read_text(encoding="utf-8"))
    md = (ro.PASTA_RESULTADOS / "relatorio.md").read_text(encoding="utf-8")
    # mesmos números, mesmas datas e mesmo commit AVALIADO (o do diário, não o atual)
    marca_de_so_relatorio = {"gerado_por_so_relatorio", "git_head_atual"}
    assert {k: v for k, v in dados.items() if k not in marca_de_so_relatorio} == \
        {k: v for k, v in dados_normal.items() if k not in marca_de_so_relatorio}
    assert dados["git_head"] == HEAD_FALSO
    assert dados["iniciado_em"] == linhas[0]["iniciado_em"]
    assert dados["concluido_em"] == linhas[-1]["concluido_em"]
    # marcação e HEAD atual
    assert dados["gerado_por_so_relatorio"] is True and dados["git_head_atual"] == head_atual
    marca = MARCA_SO_RELATORIO.format(head=head_atual)
    assert marca in md
    assert md.replace(marca + "\n\n", "") == md_normal
    # o tag_sha e o HEAD da execução vêm do diário; o HEAD atual só aparece na marca
    assert dados["tag_sha"] == dados_normal["tag_sha"] == TAG_SHA_FALSO
    assert f"- HEAD da execução: {HEAD_FALSO}" in md and head_atual not in md_normal
    # sem lixo, sem lock, sem pasta de abortadas
    assert _sem_sobras(ro.PASTA_RESULTADOS)
    assert not ro.ARQUIVO_LOCK.exists() and not ro.PASTA_ABORTADAS.exists()


def test_so_relatorio_reaproveita_o_diario_sem_altera_lo(monkeypatch, clientes_criados):
    linhas, _, _ = diario_valido(monkeypatch, clientes_criados)
    antes = (ro.PASTA_RESULTADOS / "diario.jsonl").read_bytes()
    bloquear_api_e_dados(monkeypatch)
    assert ro.main(["--so-relatorio"]) == 0
    assert (ro.PASTA_RESULTADOS / "diario.jsonl").read_bytes() == antes


@pytest.mark.parametrize("existente", ["relatorio.json", "relatorio.md"])
def test_so_relatorio_recusa_se_ja_existe_relatorio(existente, monkeypatch, clientes_criados):
    diario_valido(monkeypatch, clientes_criados)
    bloquear_api_e_dados(monkeypatch)
    (ro.PASTA_RESULTADOS / existente).write_text("antigo", encoding="utf-8")
    assert ro.main(["--so-relatorio"]) == 4
    assert (ro.PASTA_RESULTADOS / existente).read_text(encoding="utf-8") == "antigo"
    outro = "relatorio.md" if existente == "relatorio.json" else "relatorio.json"
    assert not (ro.PASTA_RESULTADOS / outro).exists()


def _trocar(linhas, indice, **campos):
    novas = copy.deepcopy(linhas)
    novas[indice].update(campos)
    return novas


def _sem_a_chave(linhas, indice, chave):
    novas = copy.deepcopy(linhas)
    del novas[indice][chave]
    return novas


# Cada variante parte de um diário válido (197 linhas: início, 195 itens e fim) e o estraga.
VARIANTES_INVALIDAS = {
    "194_itens": lambda L: L[:5] + L[6:],
    "196_itens": lambda L: L[:-1] + [copy.deepcopy(L[-2])] + L[-1:],
    "sem_fim": lambda L: L[:-1],
    "fim_abortado": lambda L: L[:-1] + [{"tipo": "fim", "status": "abortado",
                                          "causa": {"tipo": "ErroTecnicoEsgotado", "status": 503,
                                                    "request_id": "req_1"}}],
    "linha_de_aborto_e_fim_concluido": lambda L: L[:-1] + [
        {"tipo": "fim", "status": "abortado", "causa": {"tipo": "ValueError"}}, L[-1]],
    "dois_fins_concluidos": lambda L: L + [copy.deepcopy(L[-1])],
    "fim_com_status_estranho": lambda L: _trocar(L, -1, status="parcial"),
    "sem_inicio": lambda L: L[1:],
    "inicio_fora_do_lugar": lambda L: [L[1], L[0]] + L[2:],
    "dois_inicios": lambda L: [L[0]] + L,
    "conjunto_fora_de_ordem": lambda L: L[:1] + [L[60]] + L[2:60] + [L[1]] + L[61:],
    "rodada_errada": lambda L: _trocar(L, 3, rodada=2),
    "conjunto_errado": lambda L: _trocar(L, 1, conjunto="critico"),
    "ids_diferentes_entre_rodadas": lambda L: _trocar(L, 70, id="ger-999"),
    "linha_corrompida": lambda L: L[:10] + ["{isto nao e json"] + L[11:],
    "linha_que_nao_e_objeto": lambda L: L[:10] + ["[1, 2, 3]"] + L[11:],
    "tipo_desconhecido": lambda L: L[:-1] + [{"tipo": "outro"}] + L[-1:],
    "item_sem_previsao": lambda L: _sem_a_chave(L, 4, "previsao"),
    "item_sem_gabarito": lambda L: _sem_a_chave(L, 4, "gabarito"),
    "previsao_fora_do_contrato": lambda L: _trocar(
        L, 4, previsao={"categorias": ["inventada"], "criticidade": []}),
    "versao_diferente": lambda L: _trocar(L, 0, versao="v2"),
    "inicio_sem_git_head": lambda L: _sem_a_chave(L, 0, "git_head"),
    "inicio_sem_tag_sha": lambda L: _sem_a_chave(L, 0, "tag_sha"),
    "tag_sha_do_diario_diferente_do_atual": lambda L: _trocar(L, 0, tag_sha="f" * 40),
    "inicio_sem_data": lambda L: _sem_a_chave(L, 0, "iniciado_em"),
    "fim_sem_data": lambda L: _sem_a_chave(L, -1, "concluido_em"),
}
for _chave in ("prompt_sha256", "schema_sha256", "parametros_sha256", "regra_retentativa_sha256",
               "modelo", "anthropic", "python_dotenv", "dados_geral_sha256", "dados_critico_sha256"):
    VARIANTES_INVALIDAS[f"hash_divergente_{_chave}"] = \
        lambda L, c=_chave: _trocar(L, 0, **{c: "valor-que-nao-bate"})
    VARIANTES_INVALIDAS[f"hash_ausente_{_chave}"] = lambda L, c=_chave: _sem_a_chave(L, 0, c)


@pytest.mark.parametrize("variante", sorted(VARIANTES_INVALIDAS))
def test_so_relatorio_com_diario_invalido_sai_11_sem_gerar_relatorio(
        variante, monkeypatch, clientes_criados, capsys):
    linhas, _, _ = diario_valido(monkeypatch, clientes_criados)
    assert len(linhas) == 197
    escrever_diario(VARIANTES_INVALIDAS[variante](linhas))
    bruto_antes = (ro.PASTA_RESULTADOS / "diario.jsonl").read_bytes()
    bloquear_api_e_dados(monkeypatch)
    capsys.readouterr()

    assert ro.main(["--so-relatorio"]) == 11

    captura = capsys.readouterr()
    assert captura.out == ""
    assert captura.err.strip() != ""
    # a mensagem não traz conteúdo do diário (textos, ids, hashes, previsões)
    for conteudo in ("texto falso", "ger-0", "cri-0", "valor-que-nao-bate", "inventada"):
        assert conteudo not in captura.err
    assert not (ro.PASTA_RESULTADOS / "relatorio.json").exists()
    assert not (ro.PASTA_RESULTADOS / "relatorio.md").exists()
    assert (ro.PASTA_RESULTADOS / "diario.jsonl").read_bytes() == bruto_antes
    assert _sem_sobras(ro.PASTA_RESULTADOS)


def test_so_relatorio_sem_diario_sai_11(monkeypatch, clientes_criados):
    gravar_registro()
    assert ro.main(["--so-relatorio"]) == 11
    assert not ro.PASTA_RESULTADOS.exists() or not list(ro.PASTA_RESULTADOS.iterdir())
    assert clientes_criados == []


def test_so_relatorio_com_diario_vazio_sai_11(monkeypatch, clientes_criados):
    gravar_registro()
    ro.PASTA_RESULTADOS.mkdir(parents=True)
    (ro.PASTA_RESULTADOS / "diario.jsonl").write_text("", encoding="utf-8")
    assert ro.main(["--so-relatorio"]) == 11


def test_so_relatorio_nunca_cria_cliente_nem_chama_api_nem_abre_dados(monkeypatch, clientes_criados):
    diario_valido(monkeypatch, clientes_criados)
    clientes_criados.clear()
    bloquear_api_e_dados(monkeypatch)  # qualquer toque derruba o teste
    assert ro.main(["--so-relatorio"]) == 0
    assert clientes_criados == []
    assert not ro.ARQUIVO_GERAL.exists() and not ro.ARQUIVO_CRITICO.exists()


def test_so_relatorio_aplica_as_mesmas_travas_hashes_git_e_depois_tag(monkeypatch, clientes_criados):
    diario_valido(monkeypatch, clientes_criados)
    bloquear_api_e_dados(monkeypatch)
    consultas = []
    monkeypatch.setattr(ro, "conferir_tag",
                        lambda raiz=None: consultas.append(1) or TAG_SHA_FALSO)
    # travas a) e b) vêm ANTES da tag: a tag nem é consultada
    monkeypatch.setattr(ro, "git_alteracoes", lambda raiz=None: [" M app/x.py"])
    assert ro.main(["--so-relatorio"]) == 3
    monkeypatch.setattr(ro, "git_alteracoes", lambda raiz=None: [])
    gravar_registro(alterar={"modelo": "outro"})
    assert ro.main(["--so-relatorio"]) == 2
    assert consultas == []
    # com tudo certo, a tag é conferida (uma vez) e o relatório sai
    gravar_registro()
    assert ro.main(["--so-relatorio"]) == 0
    assert consultas == [1]


def test_so_relatorio_com_tag_que_falha_sai_7_com_a_mesma_mensagem_do_executar(
        monkeypatch, clientes_criados, capsys):
    diario_valido(monkeypatch, clientes_criados)
    bloquear_api_e_dados(monkeypatch)
    antes = (ro.PASTA_RESULTADOS / "diario.jsonl").read_bytes()
    monkeypatch.setattr(ro, "conferir_tag", _tag_falha("app/classificador.py diferem da tag"))
    capsys.readouterr()
    assert ro.main(["--so-relatorio"]) == 7
    err_so = capsys.readouterr().err
    assert "A trava da tag classificador-v1-congelado falhou" in err_so
    assert "Nada foi aberto nem chamado." in err_so
    assert not (ro.PASTA_RESULTADOS / "relatorio.json").exists()
    assert not (ro.PASTA_RESULTADOS / "relatorio.md").exists()
    assert (ro.PASTA_RESULTADOS / "diario.jsonl").read_bytes() == antes
    # --executar emite exatamente a mesma mensagem
    assert ro.main(["--executar"]) == 7
    assert capsys.readouterr().err == err_so


def test_so_relatorio_tag_vem_antes_de_recusar_relatorio_existente(monkeypatch, clientes_criados):
    diario_valido(monkeypatch, clientes_criados)
    bloquear_api_e_dados(monkeypatch)
    (ro.PASTA_RESULTADOS / "relatorio.md").write_text("antigo", encoding="utf-8")
    monkeypatch.setattr(ro, "conferir_tag", _tag_falha())
    assert ro.main(["--so-relatorio"]) == 7


def test_dry_run_continua_sem_conferir_tag_mesmo_com_so_relatorio_conferindo(
        monkeypatch, clientes_criados):
    gravar_registro()
    monkeypatch.setattr(ro, "conferir_tag", lambda raiz=None: pytest.fail("dry-run não confere tag"))
    assert ro.main([]) == 0


def test_so_relatorio_com_tag_sha_igual_ao_do_inicio_mostra_tag_head_da_execucao_e_head_atual(
        monkeypatch, clientes_criados):
    diario_valido(monkeypatch, clientes_criados)
    bloquear_api_e_dados(monkeypatch)
    head_atual = "9999aaaa" * 5
    monkeypatch.setattr(ro, "git_head", lambda raiz=None: head_atual)
    assert ro.main(["--so-relatorio"]) == 0
    md = (ro.PASTA_RESULTADOS / "relatorio.md").read_text(encoding="utf-8")
    dados = json.loads((ro.PASTA_RESULTADOS / "relatorio.json").read_text(encoding="utf-8"))
    assert f"- Commit da tag classificador-v1-congelado: {TAG_SHA_FALSO}" in md
    assert f"- HEAD da execução: {HEAD_FALSO}" in md  # o do diário
    assert f"Commit atual (git HEAD): {head_atual}." in md  # o atual
    assert dados["tag_sha"] == TAG_SHA_FALSO
    assert dados["git_head"] == HEAD_FALSO and dados["git_head_atual"] == head_atual


MSG_TAG_SHA_DIVERGE = ("o commit da tag classificador-v1-congelado mudou desde a execução "
                       "(tag_sha do início do diário diferente do atual)")


def test_so_relatorio_com_tag_movida_desde_a_execucao_sai_11_com_mensagem_fixa(
        monkeypatch, clientes_criados, capsys):
    diario_valido(monkeypatch, clientes_criados)  # o início grava TAG_SHA_FALSO
    bloquear_api_e_dados(monkeypatch)
    outro = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.setattr(ro, "conferir_tag", lambda raiz=None: outro)
    capsys.readouterr()
    assert ro.main(["--so-relatorio"]) == 11
    err = capsys.readouterr().err
    assert MSG_TAG_SHA_DIVERGE in err
    assert outro not in err and TAG_SHA_FALSO not in err  # mensagem fixa, sem os SHAs
    assert not (ro.PASTA_RESULTADOS / "relatorio.json").exists()
    assert not (ro.PASTA_RESULTADOS / "relatorio.md").exists()


def test_so_relatorio_com_diario_sem_tag_sha_sai_11(monkeypatch, clientes_criados, capsys):
    linhas, _, _ = diario_valido(monkeypatch, clientes_criados)
    escrever_diario(_sem_a_chave(linhas, 0, "tag_sha"))
    bloquear_api_e_dados(monkeypatch)
    capsys.readouterr()
    assert ro.main(["--so-relatorio"]) == 11
    assert MSG_TAG_SHA_DIVERGE in capsys.readouterr().err


def test_so_relatorio_compara_os_hashes_dos_dados_com_o_registro(monkeypatch, clientes_criados):
    # O diário traz os hashes dos dados da execução; o registro precisa concordar.
    diario_valido(monkeypatch, clientes_criados)
    bloquear_api_e_dados(monkeypatch)
    gravar_registro(alterar={"dados_geral_sha256": "0" * 64})
    assert ro.main(["--so-relatorio"]) == 11


@pytest.mark.parametrize("extra", [["--executar"], ["--motivo-tecnico", "x"],
                                   ["--executar", "--motivo-tecnico", "x"]])
def test_so_relatorio_e_exclusivo_com_executar_e_motivo_tecnico(extra, monkeypatch, clientes_criados, capsys):
    chamadas = []
    for nome in ("ler_congelamento", "hashes_atuais", "git_alteracoes", "git_head",
                 "conferir_tag", "carregar_conjunto", "carregar_prompt"):
        monkeypatch.setattr(ro, nome, lambda *a, _n=nome, **k: chamadas.append(_n))
    with pytest.raises(SystemExit) as exc:
        ro.main(["--so-relatorio", *extra])
    assert exc.value.code == 2
    assert chamadas == [] and clientes_criados == []
    err = capsys.readouterr().err
    assert "--so-relatorio" in err


def test_so_relatorio_depois_de_falha_na_gravacao_do_md(monkeypatch, clientes_criados):
    """Cenário real: o MD falhou no fluxo normal; o diário concluído sobrou; --so-relatorio resolve."""
    preparar(monkeypatch, clientes_criados)
    real = ro._gravar_atomico

    def md_falha(caminho, texto):
        if caminho.name == "relatorio.md":
            raise OSError("disco cheio")
        real(caminho, texto)

    monkeypatch.setattr(ro, "_gravar_atomico", md_falha)
    assert ro.main(["--executar"]) == 1
    monkeypatch.setattr(ro, "_gravar_atomico", real)
    bloquear_api_e_dados(monkeypatch)
    assert ro.main(["--so-relatorio"]) == 0
    assert (ro.PASTA_RESULTADOS / "relatorio.md").exists()
    assert (ro.PASTA_RESULTADOS / "relatorio.json").exists()


def test_so_relatorio_json_antes_do_md_e_limpeza_se_o_md_falhar(monkeypatch, clientes_criados):
    diario_valido(monkeypatch, clientes_criados)
    bloquear_api_e_dados(monkeypatch)
    ordem = []
    real = ro._gravar_atomico

    def espiao(caminho, texto):
        ordem.append(caminho.name)
        if caminho.name == "relatorio.md":
            raise OSError("disco cheio")
        real(caminho, texto)

    monkeypatch.setattr(ro, "_gravar_atomico", espiao)
    assert ro.main(["--so-relatorio"]) == 1
    assert ordem == ["relatorio.json", "relatorio.md"]
    assert not (ro.PASTA_RESULTADOS / "relatorio.json").exists()
    assert not (ro.PASTA_RESULTADOS / "relatorio.md").exists()
    assert _sem_sobras(ro.PASTA_RESULTADOS)


# ---------- diário completo e concluído sem relatório: NÃO se arquiva como abortado ----------

@pytest.mark.parametrize("argv", [["--executar"], ["--executar", "--motivo-tecnico", "refazer"]],
                         ids=["sem_motivo", "com_motivo"])
def test_diario_completo_e_concluido_sem_relatorio_manda_usar_so_relatorio(
        argv, monkeypatch, clientes_criados, capsys):
    diario_valido(monkeypatch, clientes_criados)
    antes = (ro.PASTA_RESULTADOS / "diario.jsonl").read_bytes()
    clientes_criados.clear()
    capsys.readouterr()
    assert ro.main(argv) == 5
    assert "--so-relatorio" in capsys.readouterr().err
    assert (ro.PASTA_RESULTADOS / "diario.jsonl").read_bytes() == antes  # nada arquivado
    assert not ro.PASTA_ABORTADAS.exists()
    assert clientes_criados == [] and not ro.ARQUIVO_LOCK.exists()


@pytest.mark.parametrize("variante", ["194_itens", "sem_fim", "fim_abortado", "sem_inicio"])
def test_diario_incompleto_ou_abortado_com_motivo_e_arquivado(
        variante, monkeypatch, clientes_criados):
    linhas, _, _ = diario_valido(monkeypatch, clientes_criados)
    escrever_diario(VARIANTES_INVALIDAS[variante](linhas))
    antes = (ro.PASTA_RESULTADOS / "diario.jsonl").read_bytes()
    novo = cliente_bom()
    usar_cliente(monkeypatch, novo, clientes_criados)
    assert ro.main(["--executar", "--motivo-tecnico", "falha técnica simulada"]) == 0
    assert novo.chamadas == 195
    arquivadas = [p for p in ro.PASTA_ABORTADAS.iterdir() if p.is_dir()]
    assert len(arquivadas) == 1
    assert (arquivadas[0] / "diario.jsonl").read_bytes() == antes


# ---------- git real, em repositório criado em tmp_path ----------

def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                    "-c", "commit.gpgsign=false", *args],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    pasta = tmp_path / "repo_git"
    pasta.mkdir()
    for arquivo in ("prompts/a.md", "app/a.py", "avaliacao/a.py", "ajuste/a.py",
                    "avaliacao/resultados/v1/diario.jsonl", "avaliacao/abortadas/x/diario.jsonl",
                    "docs/a.md", "tests/a.py"):
        caminho = pasta / arquivo
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text("original\n", encoding="utf-8")
    _git(pasta, "init", "-q")
    _git(pasta, "add", "-A")
    _git(pasta, "commit", "-q", "-m", "inicial")
    return pasta


def test_git_head_devolve_o_hash_do_commit(repo):
    esperado = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                              capture_output=True, text=True).stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{40}", esperado)
    assert GIT_HEAD_REAL(repo) == esperado


def test_git_head_levanta_runtime_error_fora_de_repositorio(tmp_path):
    vazio = tmp_path / "nao_e_repositorio"
    vazio.mkdir()
    with pytest.raises(RuntimeError):
        GIT_HEAD_REAL(vazio)


def test_git_head_levanta_runtime_error_sem_commits(tmp_path):
    novo = tmp_path / "sem_commits"
    novo.mkdir()
    _git(novo, "init", "-q")
    with pytest.raises(RuntimeError):
        GIT_HEAD_REAL(novo)


def test_git_alteracoes_vazio_com_repositorio_limpo(repo):
    assert GIT_ALTERACOES_REAL(repo) == []


@pytest.mark.parametrize("arquivo", ["prompts/a.md", "app/a.py", "avaliacao/a.py", "ajuste/a.py"])
def test_git_alteracoes_mostra_arquivo_modificado_nas_quatro_pastas(repo, arquivo):
    (repo / arquivo).write_text("mudou\n", encoding="utf-8")
    alteracoes = GIT_ALTERACOES_REAL(repo)
    assert len(alteracoes) == 1
    assert arquivo in alteracoes[0]


@pytest.mark.parametrize("arquivo", ["prompts/novo.md", "app/novo.py", "avaliacao/novo.py",
                                     "ajuste/novo.py", "avaliacao/pasta_nova/novo.py"])
def test_git_alteracoes_mostra_arquivo_nao_rastreado(repo, arquivo):
    caminho = repo / arquivo
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text("novo\n", encoding="utf-8")
    alteracoes = GIT_ALTERACOES_REAL(repo)
    assert len(alteracoes) == 1
    assert arquivo in alteracoes[0]


@pytest.mark.parametrize("arquivo", [
    "avaliacao/resultados/v1/diario.jsonl",     # modificado
    "avaliacao/resultados/v1/relatorio.md",     # novo
    "avaliacao/abortadas/x/diario.jsonl",       # modificado
    "avaliacao/abortadas/y/motivo.json",        # novo
    "docs/a.md",                                # fora das quatro pastas
    "tests/a.py",
    "docs/novo.md",
])
def test_git_alteracoes_ignora_saidas_e_pastas_fora_do_escopo(repo, arquivo):
    caminho = repo / arquivo
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text("mudou\n", encoding="utf-8")
    assert GIT_ALTERACOES_REAL(repo) == []


def test_git_alteracoes_ignora_pastas_de_saida_mas_ve_o_resto(repo):
    (repo / "avaliacao/resultados/v1/diario.jsonl").write_text("mudou\n", encoding="utf-8")
    (repo / "avaliacao/abortadas/z").mkdir(parents=True)
    (repo / "avaliacao/abortadas/z/diario.jsonl").write_text("novo\n", encoding="utf-8")
    (repo / "app/a.py").write_text("mudou\n", encoding="utf-8")
    alteracoes = GIT_ALTERACOES_REAL(repo)
    assert len(alteracoes) == 1 and "app/a.py" in alteracoes[0]


# ---------- tag de congelamento em repositório git REAL (criado em tmp_path) ----------

@pytest.fixture
def repo_tag(tmp_path):
    """Repositório com os três arquivos congelados e outros, um commit inicial, SEM tag."""
    pasta = tmp_path / "repo_tag"
    pasta.mkdir()
    for arquivo in ("app/classificador.py", "app/contrato.py", "app/metricas.py",
                    "app/outro.py", "avaliacao/a.py", "docs/a.md"):
        caminho = pasta / arquivo
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text("original\n", encoding="utf-8")
    _git(pasta, "init", "-q")
    _git(pasta, "add", "-A")
    _git(pasta, "commit", "-q", "-m", "inicial")
    return pasta


def _commit(repo, arquivo, conteudo="mudou\n", mensagem="muda"):
    (repo / arquivo).write_text(conteudo, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", mensagem)


def _rev_parse(repo, ref):
    return subprocess.run(["git", "rev-parse", ref], cwd=repo, check=True, capture_output=True,
                          text=True).stdout.strip()


def test_conferir_tag_tudo_certo_passa_e_devolve_o_sha_do_commit_da_tag(repo_tag):
    _git(repo_tag, "tag", ro.TAG_CONGELAMENTO)
    sha = CONFERIR_TAG_REAL(repo_tag)
    assert re.fullmatch(r"[0-9a-f]{40}", sha)
    assert sha == _rev_parse(repo_tag, "HEAD")


def test_conferir_tag_anotada_tambem_passa_e_devolve_o_commit_nao_o_objeto_da_tag(repo_tag):
    _git(repo_tag, "tag", "-a", ro.TAG_CONGELAMENTO, "-m", "congelado")
    sha = CONFERIR_TAG_REAL(repo_tag)
    assert sha == _rev_parse(repo_tag, "HEAD")
    # a tag anotada é um objeto próprio; o que se guarda é o commit apontado por ela
    assert _rev_parse(repo_tag, f"refs/tags/{ro.TAG_CONGELAMENTO}") != sha


def test_conferir_tag_passa_com_commits_depois_da_tag_em_outros_arquivos(repo_tag):
    _git(repo_tag, "tag", ro.TAG_CONGELAMENTO)
    commit_da_tag = _rev_parse(repo_tag, "HEAD")
    _commit(repo_tag, "avaliacao/a.py")
    _commit(repo_tag, "docs/a.md")
    _commit(repo_tag, "app/outro.py")
    sha = CONFERIR_TAG_REAL(repo_tag)
    assert sha == commit_da_tag != _rev_parse(repo_tag, "HEAD")  # o da tag, não o HEAD


def test_conferir_tag_inexistente_falha(repo_tag):
    with pytest.raises(RuntimeError, match="não existe"):
        CONFERIR_TAG_REAL(repo_tag)


def test_conferir_tag_com_outro_nome_nao_vale(repo_tag):
    _git(repo_tag, "tag", "classificador-v2-congelado")
    with pytest.raises(RuntimeError, match="não existe"):
        CONFERIR_TAG_REAL(repo_tag)


def test_conferir_tag_falha_se_head_nao_descende_da_tag(repo_tag):
    _commit(repo_tag, "docs/a.md", mensagem="depois")
    _git(repo_tag, "tag", ro.TAG_CONGELAMENTO)  # a tag aponta para o commit "depois"
    _git(repo_tag, "reset", "-q", "--hard", "HEAD~1")  # HEAD volta ao inicial: a tag fica à frente
    with pytest.raises(RuntimeError, match="descende"):
        CONFERIR_TAG_REAL(repo_tag)


def test_conferir_tag_falha_em_ramo_que_divergiu_da_tag(repo_tag):
    _commit(repo_tag, "docs/a.md", mensagem="depois")
    _git(repo_tag, "tag", ro.TAG_CONGELAMENTO)
    _git(repo_tag, "checkout", "-q", "-b", "paralelo", "HEAD~1")
    _commit(repo_tag, "avaliacao/a.py", mensagem="outro caminho")
    with pytest.raises(RuntimeError, match="descende"):
        CONFERIR_TAG_REAL(repo_tag)


@pytest.mark.parametrize("arquivo", ["app/classificador.py", "app/contrato.py", "app/metricas.py"])
def test_conferir_tag_falha_se_arquivo_congelado_mudou_depois_da_tag(repo_tag, arquivo):
    _git(repo_tag, "tag", ro.TAG_CONGELAMENTO)
    _commit(repo_tag, arquivo, conteudo="SEGREDO-NO-CONTEUDO mudou depois da tag\n")
    with pytest.raises(RuntimeError, match="diferem") as exc:
        CONFERIR_TAG_REAL(repo_tag)
    assert "SEGREDO" not in str(exc.value)  # a mensagem não traz o diff


def test_conferir_tag_arquivo_congelado_que_mudou_e_voltou_passa(repo_tag):
    # Mudou e foi revertido: o conteúdo na tag e no HEAD é igual, então a trava passa.
    _git(repo_tag, "tag", ro.TAG_CONGELAMENTO)
    commit_da_tag = _rev_parse(repo_tag, "HEAD")
    _commit(repo_tag, "app/classificador.py", conteudo="outra coisa\n")
    _commit(repo_tag, "app/classificador.py", conteudo="original\n")
    assert CONFERIR_TAG_REAL(repo_tag) == commit_da_tag


def test_conferir_tag_fora_de_repositorio_falha(tmp_path):
    vazio = tmp_path / "nao_e_repositorio"
    vazio.mkdir()
    with pytest.raises(RuntimeError):
        CONFERIR_TAG_REAL(vazio)


def test_main_traduz_falha_da_tag_em_saida_7_mesmo_com_git_real(monkeypatch, clientes_criados, repo_tag):
    # Liga o main ao conferir_tag REAL apontando para o repo temporário (sem tag): sai 7.
    monkeypatch.setattr(ro, "conferir_tag", lambda raiz=None: CONFERIR_TAG_REAL(repo_tag))
    gravar_registro()
    assert ro.main(["--executar"]) == 7
    assert clientes_criados == []
    _git(repo_tag, "tag", ro.TAG_CONGELAMENTO)
    preparar(monkeypatch, clientes_criados)
    assert ro.main(["--executar"]) == 0
    assert linhas_do_diario()[0]["tag_sha"] == _rev_parse(repo_tag, "HEAD")


def test_so_relatorio_com_git_real_detecta_a_tag_movida_depois_da_execucao(
        monkeypatch, clientes_criados, repo_tag, capsys):
    monkeypatch.setattr(ro, "conferir_tag", lambda raiz=None: CONFERIR_TAG_REAL(repo_tag))
    _git(repo_tag, "tag", ro.TAG_CONGELAMENTO)
    sha_da_execucao = _rev_parse(repo_tag, "HEAD")
    linhas, _, _ = diario_valido(monkeypatch, clientes_criados)
    assert linhas[0]["tag_sha"] == sha_da_execucao
    bloquear_api_e_dados(monkeypatch)
    # mesma tag: o relatório sai e cita o commit da tag
    assert ro.main(["--so-relatorio"]) == 0
    md = (ro.PASTA_RESULTADOS / "relatorio.md").read_text(encoding="utf-8")
    assert f"- Commit da tag classificador-v1-congelado: {sha_da_execucao}" in md
    (ro.PASTA_RESULTADOS / "relatorio.md").unlink()
    (ro.PASTA_RESULTADOS / "relatorio.json").unlink()
    # a tag é movida para um commit novo (arquivos congelados iguais, HEAD ainda descende): sai 11
    _commit(repo_tag, "docs/a.md", mensagem="commit novo")
    _git(repo_tag, "tag", "-f", ro.TAG_CONGELAMENTO)
    capsys.readouterr()
    assert ro.main(["--so-relatorio"]) == 11
    assert MSG_TAG_SHA_DIVERGE in capsys.readouterr().err
    assert not (ro.PASTA_RESULTADOS / "relatorio.md").exists()


# ---------- ajuda do script: lista completa dos códigos de saída ----------

def test_help_traz_a_lista_completa_dos_codigos_de_saida(capsys):
    with pytest.raises(SystemExit) as exc:
        ro.main(["--help"])
    assert exc.value.code == 0
    saida = " ".join(capsys.readouterr().out.split())  # quebras de linha viram espaço simples
    esperados = {
        "0": "ok ou dry-run",
        "1": "aborto durante a execução OU falha ao gerar o relatório",
        "2": "congelamento diverge (também é o erro de argumentos do argparse)",
        "3": "alterações não commitadas ou git indisponível",
        "4": "avaliação já concluída",
        "5": "diário anterior sem --motivo-tecnico, motivo sem nada a arquivar, diário completo "
             "(use --so-relatorio) ou falha ao arquivar",
        "6": "conjunto inválido",
        "7": "tag/ancestralidade/arquivos congelados",
        "8": "hash dos dados diverge",
        "9": "outra execução em andamento ou não foi possível criar o lock",
        "10": "cliente não pôde ser criado",
        "11": "--so-relatorio inválido",
        "130": "Ctrl+C",
    }
    for codigo, descricao in esperados.items():
        assert f"{codigo} {descricao}" in saida, codigo


def test_help_nao_roda_nenhuma_trava(monkeypatch, clientes_criados):
    chamadas = []
    for nome in ("ler_congelamento", "hashes_atuais", "git_alteracoes", "git_head",
                 "conferir_tag", "carregar_conjunto", "carregar_prompt"):
        monkeypatch.setattr(ro, nome, lambda *a, _n=nome, **k: chamadas.append(_n))
    with pytest.raises(SystemExit):
        ro.main(["--help"])
    assert chamadas == [] and clientes_criados == []


def test_docstring_do_modulo_tem_a_mesma_lista_de_codigos():
    doc = " ".join(ro.__doc__.split())
    for trecho in ("1 aborto durante a execução OU falha ao gerar o relatório",
                   "5 diário anterior sem --motivo-tecnico, motivo sem nada a arquivar, diário "
                   "completo (use --so-relatorio) ou falha ao arquivar",
                   "9 outra execução em andamento ou não foi possível criar o lock",
                   "11 --so-relatorio inválido", "130 Ctrl+C"):
        assert trecho in doc, trecho


# ---------- --motivo-tecnico escapado no markdown ----------

def test_motivo_tecnico_com_quebra_de_linha_cerquilha_e_crases_vai_escapado_ao_relatorio(
        monkeypatch, clientes_criados):
    preparar(monkeypatch, clientes_criados)
    _diario_incompleto_no_lugar()
    motivo = "queda de rede\n# Título falso\n```bloco``` e `crase`\n## Outra seção"
    assert ro.main(["--executar", "--motivo-tecnico", motivo]) == 0
    md = (ro.PASTA_RESULTADOS / "relatorio.md").read_text(encoding="utf-8")
    dados = json.loads((ro.PASTA_RESULTADOS / "relatorio.json").read_text(encoding="utf-8"))
    assert f"motivo: {json.dumps(motivo, ensure_ascii=False)}" in md
    assert "\n# Título falso" not in md and "\n## Outra seção" not in md
    assert [l for l in md.splitlines() if l.startswith("# ")] == [md.splitlines()[0]]
    assert dados["abortadas"][0]["motivo"] == motivo  # nos dados, o texto original


# ---------- separação dos conjuntos no código ----------

def _literais(caminho):
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    return [n.value for n in ast.walk(arvore)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _modulos_vigiados():
    modulos = []
    for pasta in ("app", "ajuste", "avaliacao"):
        modulos += [p for p in (RAIZ / pasta).rglob("*.py") if "__pycache__" not in p.parts]
    return modulos


def test_so_o_executor_cita_os_arquivos_dos_conjuntos_de_avaliacao():
    vigiados = _modulos_vigiados()
    nomes = {p.name for p in vigiados}
    assert {"classificador.py", "metricas.py", "rodar_dev.py", "congelamento.py",
            "relatorio_oficial.py", "rodar_oficial.py"} <= nomes
    for caminho in vigiados:
        if caminho == RAIZ / "avaliacao" / "rodar_oficial.py":
            continue
        for literal in _literais(caminho):
            assert "geral.json" not in literal, (caminho.name, literal)
            assert "critico.json" not in literal, (caminho.name, literal)


def test_o_executor_e_o_unico_que_cita_esses_arquivos():
    literais = _literais(RAIZ / "avaliacao" / "rodar_oficial.py")
    assert any("geral.json" in l for l in literais)
    assert any("critico.json" in l for l in literais)
