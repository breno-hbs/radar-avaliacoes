"""Testes do congelamento do prompt (avaliacao/congelamento.py) e da regra de retentativa.

Dados simulados. Projeto independente, sem vínculo com o iFood.
Sem rede e sem os arquivos de avaliação. Arquivos falsos são criados em tmp_path.
O teste "registro de congelamento commitado bate com o código" lê o registro real
(prompts/classificador/v1.congelamento.md) e só passa depois que esse registro for escrito.
"""
import importlib.metadata
import json
from pathlib import Path

import anthropic
import httpx2
import pytest

from app import classificador
from app.classificador import (
    MAX_TENTATIVAS,
    PARAMETROS,
    REGRA_RETENTATIVA,
    SCHEMA,
    STATUS_REPETIVEIS,
    ErroPermanenteDaAPI,
    ErroTecnicoEsgotado,
)
from avaliacao import congelamento
from avaliacao.congelamento import comparar, hash_canonico, hashes_atuais, ler_congelamento

RAIZ = Path(__file__).resolve().parent.parent
HASH_PROMPT_V1 = "e84b4b75d8313505a205153e6937d2920d9088a299bb505b469760236d9f9491"

CHAVES_DE_HASHES = {
    "prompt_sha256", "schema_sha256", "parametros_sha256", "regra_retentativa_sha256",
    "modelo", "anthropic", "python_dotenv",
}
CHAVES_DE_DADOS = {"dados_geral_sha256", "dados_critico_sha256"}


def classificar(texto, cliente, prompt):
    """classificador.classificar com espera falsa: nenhum teste espera de verdade."""
    return classificador.classificar(texto, cliente, prompt, dormir=lambda segundos: None)


# ---------- objetos falsos (cliente sem rede) ----------

class Bloco:
    def __init__(self, type, text=None):
        self.type = type
        if text is not None:
            self.text = text


class Resposta:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason
        self._request_id = "req_falso"


class ClienteFalso:
    """Devolve (ou levanta) sempre a mesma coisa e conta as chamadas."""

    def __init__(self, fabrica):
        self.fabrica = fabrica
        self.chamadas = 0
        self.messages = self

    def create(self, **kwargs):
        self.chamadas += 1
        r = self.fabrica()
        if isinstance(r, BaseException):
            raise r
        return r


def _req():
    return httpx2.Request("POST", "https://x")


def _erro_status(status):
    resposta_http = httpx2.Response(status, request=_req(), headers={"request-id": "req_x"})
    return anthropic.APIStatusError("falha", response=resposta_http, body=None)


def _texto(t, stop_reason="end_turn"):
    return Resposta([Bloco("text", t)], stop_reason=stop_reason)


# ---------- REGRA_RETENTATIVA ----------

def test_regra_de_retentativa_exata():
    assert REGRA_RETENTATIVA == {
        "max_tentativas": 3,
        "espera_segundos": [2, 5],
        "status_transitorios": [408, 409, 429],
        "repete_5xx": True,
        "repete_sem_resposta_http": True,
        "repete_falhas_de_conteudo": ["max_tokens", "refusal", "sem_bloco_texto",
                                      "json_invalido", "saida_invalida"],
        "esgotar_falha_de_conteudo": "saida_invalida",
        "esgotar_erro_tecnico": "aborta_execucao",
        "demais_status": "aborta_execucao",
    }


def test_constantes_derivam_da_regra():
    assert MAX_TENTATIVAS == REGRA_RETENTATIVA["max_tentativas"]
    assert STATUS_REPETIVEIS == tuple(REGRA_RETENTATIVA["status_transitorios"])
    assert classificador.ESPERAS == tuple(REGRA_RETENTATIVA["espera_segundos"])


def test_comportamento_por_status_bate_com_a_regra():
    """Para cada status de 400 a 599: repete (3 chamadas) e, esgotado, ABORTA com
    ErroTecnicoEsgotado se e só se estiver na lista ou for 5xx; senão aborta na 1ª chamada
    com ErroPermanenteDaAPI."""
    assert REGRA_RETENTATIVA["esgotar_erro_tecnico"] == "aborta_execucao"
    assert REGRA_RETENTATIVA["demais_status"] == "aborta_execucao"
    divergencias = []
    for status in range(400, 600):
        cliente = ClienteFalso(lambda s=status: _erro_status(s))
        deve_repetir = (status in REGRA_RETENTATIVA["status_transitorios"]
                        or (REGRA_RETENTATIVA["repete_5xx"] and status >= 500))
        try:
            classificar("texto qualquer", cliente, "PROMPT")
            divergencias.append(status)  # nenhum status esgotado pode devolver resultado
        except ErroTecnicoEsgotado as e:
            chamadas_ok = cliente.chamadas == REGRA_RETENTATIVA["max_tentativas"]
            if not (deve_repetir and chamadas_ok and e.status == status):
                divergencias.append(status)
        except ErroPermanenteDaAPI as e:
            if deve_repetir or cliente.chamadas != 1 or e.status != status:
                divergencias.append(status)
    assert divergencias == []


def test_demais_status_abortam_conforme_a_regra():
    cliente = ClienteFalso(lambda: _erro_status(400))
    with pytest.raises(ErroPermanenteDaAPI):
        classificar("texto", cliente, "PROMPT")
    assert cliente.chamadas == 1


def test_falhas_de_conteudo_esgotadas_viram_none_conforme_a_regra():
    falhas = {
        "max_tokens": lambda: _texto('{"categorias": [], "criticidade": []}', "max_tokens"),
        "refusal": lambda: Resposta([], stop_reason="refusal"),
        "sem_bloco_texto": lambda: Resposta([Bloco("thinking")]),
        "json_invalido": lambda: _texto("{isso não é json"),
        "saida_invalida": lambda: _texto('{"categorias": ["inventada"], "criticidade": []}'),
    }
    # A lista da regra e as falhas exercitadas aqui são exatamente as mesmas.
    assert set(falhas) == set(REGRA_RETENTATIVA["repete_falhas_de_conteudo"])
    assert REGRA_RETENTATIVA["esgotar_falha_de_conteudo"] == "saida_invalida"
    for nome, fabrica in falhas.items():
        cliente = ClienteFalso(fabrica)
        r = classificar("texto", cliente, "PROMPT")  # não levanta
        assert r["saida"] is None, nome  # saída inválida: o ÚNICO caso
        assert cliente.chamadas == REGRA_RETENTATIVA["max_tentativas"], nome


def test_falha_sem_resposta_http_repete_e_esgotada_aborta_conforme_a_regra():
    assert REGRA_RETENTATIVA["repete_sem_resposta_http"] is True
    for fabrica in (lambda: anthropic.APITimeoutError(request=_req()),
                    lambda: anthropic.APIConnectionError(request=_req())):
        cliente = ClienteFalso(fabrica)
        with pytest.raises(ErroTecnicoEsgotado):
            classificar("texto", cliente, "PROMPT")
        assert cliente.chamadas == REGRA_RETENTATIVA["max_tentativas"]


def test_esperas_da_regra_sao_as_usadas_entre_as_tentativas():
    esperas = []
    cliente = ClienteFalso(lambda: _texto("{ruim"))
    classificador.classificar("texto", cliente, "PROMPT", dormir=esperas.append)
    assert esperas == REGRA_RETENTATIVA["espera_segundos"]
    assert cliente.chamadas == REGRA_RETENTATIVA["max_tentativas"]


# ---------- hash canônico ----------

def test_hash_canonico_independe_da_ordem_das_chaves():
    a = {"b": 1, "a": {"y": [1, 2], "x": "ç"}}
    b = {"a": {"x": "ç", "y": [1, 2]}, "b": 1}
    assert hash_canonico(a) == hash_canonico(b)


def test_hash_canonico_muda_se_um_valor_muda():
    base = {"a": 1, "b": [1, 2, 3]}
    assert hash_canonico(base) != hash_canonico({"a": 2, "b": [1, 2, 3]})
    assert hash_canonico(base) != hash_canonico({"a": 1, "b": [1, 3, 2]})  # a ordem da lista conta


def test_hash_canonico_e_sha256_do_json_compacto_utf8():
    import hashlib

    obj = {"z": "ação", "a": [1, None, True]}
    texto = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert texto == '{"a":[1,null,true],"z":"ação"}'
    assert hash_canonico(obj) == hashlib.sha256(texto.encode("utf-8")).hexdigest()


# ---------- hashes_atuais ----------

def test_hash_do_prompt_v1_atual():
    assert hashes_atuais("v1")["prompt_sha256"] == HASH_PROMPT_V1
    assert classificador.sha256_do_prompt("v1") == HASH_PROMPT_V1


def test_hashes_atuais_tem_as_chaves_e_os_valores_esperados():
    h = hashes_atuais("v1")
    assert set(h) == CHAVES_DE_HASHES
    assert set(congelamento.CHAVES_DE_HASHES) == CHAVES_DE_HASHES
    # Os hashes dos dados dependem dos arquivos de avaliação: NÃO fazem parte de hashes_atuais.
    assert not (set(h) & CHAVES_DE_DADOS)
    assert set(congelamento.CHAVES_DE_DADOS) == CHAVES_DE_DADOS
    assert congelamento.CHAVES_DE_DADOS == ("dados_geral_sha256", "dados_critico_sha256")
    assert h["schema_sha256"] == hash_canonico(SCHEMA)
    assert h["parametros_sha256"] == hash_canonico(PARAMETROS)
    assert h["regra_retentativa_sha256"] == hash_canonico(REGRA_RETENTATIVA)
    assert h["modelo"] == PARAMETROS["model"] == "claude-sonnet-5-5"
    assert h["anthropic"] == importlib.metadata.version("anthropic")
    assert h["python_dotenv"] == importlib.metadata.version("python-dotenv")


def test_hashes_atuais_mudam_quando_o_codigo_muda(monkeypatch, tmp_path):
    antes = hashes_atuais("v1")

    monkeypatch.setitem(PARAMETROS, "max_tokens", 401)
    depois = hashes_atuais("v1")
    assert depois["parametros_sha256"] != antes["parametros_sha256"]
    # O schema vive dentro de PARAMETROS, mas o hash do schema só muda se o schema mudar.
    assert depois["schema_sha256"] == antes["schema_sha256"]
    monkeypatch.undo()

    monkeypatch.setattr(classificador, "REGRA_RETENTATIVA",
                        {**REGRA_RETENTATIVA, "max_tentativas": 4})
    assert hashes_atuais("v1")["regra_retentativa_sha256"] != antes["regra_retentativa_sha256"]
    monkeypatch.undo()

    (tmp_path / "v1.md").write_text("outro prompt\n", encoding="utf-8")
    monkeypatch.setattr(classificador, "PASTA_PROMPTS", tmp_path)
    assert hashes_atuais("v1")["prompt_sha256"] != antes["prompt_sha256"]


def test_hashes_atuais_com_prompt_texto_nao_le_o_arquivo_e_hasheia_a_mesma_string(monkeypatch):
    def nao_deve_ler(versao):
        raise AssertionError("hashes_atuais com prompt_texto não pode reler o prompt")

    monkeypatch.setattr(classificador, "carregar_prompt", nao_deve_ler)
    h = hashes_atuais("v1", prompt_texto="texto lido uma vez\n")
    assert h["prompt_sha256"] == classificador.sha256_do_texto("texto lido uma vez\n")
    assert set(h) == CHAVES_DE_HASHES


def test_hashes_atuais_sem_prompt_texto_continua_lendo_o_arquivo():
    assert hashes_atuais("v1")["prompt_sha256"] == HASH_PROMPT_V1
    assert hashes_atuais("v1", prompt_texto=classificador.carregar_prompt("v1")) == hashes_atuais("v1")


def test_hashes_atuais_mudam_com_o_schema(monkeypatch):
    antes = hashes_atuais("v1")["schema_sha256"]
    monkeypatch.setattr(classificador, "SCHEMA", {**SCHEMA, "additionalProperties": True})
    assert hashes_atuais("v1")["schema_sha256"] != antes


def test_hashes_atuais_acompanham_versoes_de_pacotes(monkeypatch):
    real = importlib.metadata.version

    def falsa(nome):
        return {"anthropic": "9.9.9", "python-dotenv": "8.8.8"}.get(nome) or real(nome)

    monkeypatch.setattr(importlib.metadata, "version", falsa)
    h = hashes_atuais("v1")
    assert h["anthropic"] == "9.9.9"
    assert h["python_dotenv"] == "8.8.8"


# ---------- hash dos dados (arquivos FALSOS em tmp_path) ----------

def test_hash_dados_e_o_hash_canonico_do_json_do_arquivo(tmp_path):
    dados = [{"id": "x-1", "texto": "ação rápida", "n": [1, 2]}, {"id": "x-2", "texto": "ç"}]
    caminho = tmp_path / "falso.json"
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    assert congelamento.hash_dados(caminho) == hash_canonico(dados)
    assert congelamento.hash_dados(str(caminho)) == hash_canonico(dados)  # aceita str


def test_hash_dados_ignora_formatacao_e_ordem_das_chaves_do_arquivo(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text('[{"id": "x", "texto": "t"}]', encoding="utf-8")
    b.write_text('[ {' + chr(13) + chr(10) + ' "texto" : "t",' + chr(13) + chr(10) + ' "id":"x"' + chr(13) + chr(10) + '} ]' + chr(13) + chr(10), encoding="utf-8", newline="")
    assert congelamento.hash_dados(a) == congelamento.hash_dados(b)


def test_hash_dados_muda_se_um_valor_muda_ou_a_ordem_da_lista_muda(tmp_path):
    base = tmp_path / "base.json"
    base.write_text('[{"id": "x", "texto": "t"}, {"id": "y", "texto": "u"}]', encoding="utf-8")
    texto_mudou = tmp_path / "t.json"
    texto_mudou.write_text('[{"id": "x", "texto": "T"}, {"id": "y", "texto": "u"}]', encoding="utf-8")
    ordem_mudou = tmp_path / "o.json"
    ordem_mudou.write_text('[{"id": "y", "texto": "u"}, {"id": "x", "texto": "t"}]', encoding="utf-8")
    h = congelamento.hash_dados(base)
    assert h != congelamento.hash_dados(texto_mudou)
    assert h != congelamento.hash_dados(ordem_mudou)
    assert len(h) == 64 and set(h) <= set("0123456789abcdef")


def test_hash_dados_le_utf8_e_rejeita_json_invalido_ou_arquivo_ausente(tmp_path):
    ruim = tmp_path / "ruim.json"
    ruim.write_text("[{isso não é json", encoding="utf-8")
    with pytest.raises(ValueError):
        congelamento.hash_dados(ruim)
    with pytest.raises(OSError):
        congelamento.hash_dados(tmp_path / "nao_existe.json")


# ---------- comparar ----------

def _atuais_falsos():
    return {
        "prompt_sha256": "a" * 64,
        "schema_sha256": "b" * 64,
        "parametros_sha256": "c" * 64,
        "regra_retentativa_sha256": "d" * 64,
        "modelo": "claude-sonnet-5-5",
        "anthropic": "2.3.0",
        "python_dotenv": "1.2.4",
    }


def test_comparar_tudo_igual_devolve_lista_vazia():
    atuais = _atuais_falsos()
    assert comparar({**atuais, "status": "CONGELADO", "versao": "v1"}, atuais) == []


@pytest.mark.parametrize("chave", sorted(CHAVES_DE_HASHES))
def test_comparar_detecta_cada_divergencia(chave):
    atuais = _atuais_falsos()
    registrado = {**atuais, chave: "valor divergente"}
    assert comparar(registrado, atuais) == [chave]


def test_comparar_devolve_lista_ordenada_com_varias_divergencias():
    atuais = _atuais_falsos()
    registrado = {**atuais, "schema_sha256": "x", "anthropic": "x", "modelo": "x"}
    assert comparar(registrado, atuais) == ["anthropic", "modelo", "schema_sha256"]


def test_comparar_so_compara_hashes_atuais_e_ignora_chaves_de_dados_do_registro():
    atuais = _atuais_falsos()
    registrado = {**atuais, **_dados_falsos(), "dados_geral_sha256": "x" * 64}
    assert comparar(registrado, atuais) == []


def test_comparar_chave_ausente_no_registro_diverge():
    atuais = _atuais_falsos()
    registrado = {k: v for k, v in atuais.items() if k != "python_dotenv"}
    assert comparar(registrado, atuais) == ["python_dotenv"]


# ---------- ler_congelamento ----------

def _dados_falsos():
    return {"dados_geral_sha256": "e" * 64, "dados_critico_sha256": "f" * 64}


def _completos():
    """Valores de um registro falso completo: hashes do código e hashes dos dados."""
    return {**_atuais_falsos(), **_dados_falsos()}


def _registro_texto(valores, status="CONGELADO", versao="v1", com_crases=True, extras=""):
    def fmt(v):
        return f"`{v}`" if com_crases else str(v)

    linhas = [f"status: {status}", f"versao: {versao}"]
    linhas += [f"{k}: {fmt(v)}" for k, v in valores.items()]
    corpo = "\n".join(linhas)
    return ("# Registro de congelamento (falso)\n\nTexto livre antes.\n\n"
            "<!-- congelamento:inicio -->\n" + corpo + "\n" + extras +
            "<!-- congelamento:fim -->\n\nTexto livre depois: chave: valor ignorado.\n")


def _gravar(tmp_path, texto, nome="v1.congelamento.md"):
    caminho = tmp_path / nome
    caminho.write_text(texto, encoding="utf-8")
    return caminho


def test_ler_registro_falso_com_e_sem_crases(tmp_path):
    atuais = _atuais_falsos()
    completos = _completos()
    for com_crases in (True, False):
        caminho = _gravar(tmp_path, _registro_texto(completos, com_crases=com_crases))
        lido = ler_congelamento(caminho)
        assert lido["status"] == "CONGELADO"
        assert lido["versao"] == "v1"
        for k, v in completos.items():
            assert lido[k] == v
        # comparar olha só os hashes do código (hashes_atuais), nunca os dos dados
        assert comparar(lido, atuais) == []


def test_ler_ignora_linhas_em_branco_e_texto_fora_do_bloco(tmp_path):
    caminho = _gravar(tmp_path, _registro_texto(_completos(), extras="\n\n"))
    lido = ler_congelamento(caminho)
    assert "Texto livre depois" not in lido
    assert set(lido) == {"status", "versao"} | CHAVES_DE_HASHES | CHAVES_DE_DADOS


def test_ler_valor_com_dois_pontos_fica_inteiro(tmp_path):
    caminho = _gravar(tmp_path, _registro_texto(
        _completos(), extras="congelado_em: 2026-10-29T22:00:00-03:00\n"))
    assert ler_congelamento(caminho)["congelado_em"] == "2026-10-29T22:00:00-03:00"


def test_registro_sem_bloco_levanta_value_error(tmp_path):
    caminho = _gravar(tmp_path, "# Registro sem bloco\n\nstatus: CONGELADO\n")
    with pytest.raises(ValueError, match="congelamento:inicio"):
        ler_congelamento(caminho)


def test_registro_com_bloco_aberto_e_nao_fechado_levanta_value_error(tmp_path):
    caminho = _gravar(tmp_path, "<!-- congelamento:inicio -->\nstatus: CONGELADO\n")
    with pytest.raises(ValueError, match="congelamento:fim"):
        ler_congelamento(caminho)


@pytest.mark.parametrize("status", ["candidato", "congelado", "DESCONGELADO", ""])
def test_registro_com_status_diferente_de_congelado_levanta_value_error(tmp_path, status):
    caminho = _gravar(tmp_path, _registro_texto(_completos(), status=status))
    with pytest.raises(ValueError, match="CONGELADO"):
        ler_congelamento(caminho)


def test_registro_sem_status_levanta_value_error(tmp_path):
    texto = _registro_texto(_completos()).replace("status: CONGELADO\n", "")
    with pytest.raises(ValueError, match="status"):
        ler_congelamento(_gravar(tmp_path, texto))


def test_registro_sem_versao_levanta_value_error(tmp_path):
    texto = _registro_texto(_completos()).replace("versao: v1\n", "")
    with pytest.raises(ValueError, match="versao"):
        ler_congelamento(_gravar(tmp_path, texto))


@pytest.mark.parametrize("faltando", sorted(CHAVES_DE_HASHES | CHAVES_DE_DADOS))
def test_registro_com_chave_faltando_levanta_value_error(tmp_path, faltando):
    valores = {k: v for k, v in _completos().items() if k != faltando}
    with pytest.raises(ValueError, match=faltando):
        ler_congelamento(_gravar(tmp_path, _registro_texto(valores)))


def test_registro_sem_nenhuma_chave_de_dados_levanta_value_error(tmp_path):
    # O registro antigo (só com os hashes do código) deixa de valer: os dados passam a ser exigidos.
    with pytest.raises(ValueError, match="dados_"):
        ler_congelamento(_gravar(tmp_path, _registro_texto(_atuais_falsos())))


@pytest.mark.parametrize("chave", sorted(CHAVES_DE_DADOS))
@pytest.mark.parametrize("invalido", ["", "abc", "g" * 64, "A" * 64, "a" * 63, "a" * 65])
def test_hash_de_dados_precisa_ter_64_hex(tmp_path, chave, invalido):
    valores = {**_completos(), chave: invalido}
    with pytest.raises(ValueError, match=chave):
        ler_congelamento(_gravar(tmp_path, _registro_texto(valores)))


def test_registro_com_chave_repetida_levanta_value_error(tmp_path):
    caminho = _gravar(tmp_path, _registro_texto(_completos(), extras="modelo: outro\n"))
    with pytest.raises(ValueError, match="modelo"):
        ler_congelamento(caminho)


def test_registro_inexistente_levanta_erro_claro(tmp_path):
    with pytest.raises((ValueError, OSError)):
        ler_congelamento(tmp_path / "nao_existe.md")


# ---------- registro real, commitado ----------

def test_registro_de_congelamento_commitado_bate_com_o_codigo():
    """Falha até o registro prompts/classificador/v1.congelamento.md existir e bater com o código."""
    caminho = RAIZ / "prompts" / "classificador" / "v1.congelamento.md"
    assert caminho.exists(), f"registro de congelamento ausente: {caminho.name}"
    registrado = ler_congelamento(caminho)
    assert registrado["versao"] == "v1"
    assert comparar(registrado, hashes_atuais("v1")) == []
