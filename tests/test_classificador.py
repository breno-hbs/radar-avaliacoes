"""Testes do classificador (app/classificador.py), sem rede.

Dados simulados. Projeto independente, sem vínculo com o iFood.
O cliente da API é sempre um objeto falso com respostas programadas.
O SDK anthropic 2.3.0 usa o pacote httpx2 (não httpx) para Request e Response.
"""
import copy
import inspect
import json
import time
import traceback

import anthropic
import httpx2
import pytest

from app import classificador
from app.classificador import MAX_TENTATIVAS, PARAMETROS, SCHEMA
from app.contrato import CATEGORIAS, CRITICIDADES

PROMPT = "PROMPT DE TESTE"
CHAVES_DE_ERRO = {"tentativa", "tipo", "status", "request_id", "stop_reason"}


# ---------- objetos falsos ----------

class Bloco:
    def __init__(self, type, text=None):
        self.type = type
        if text is not None:
            self.text = text


class Uso:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class Resposta:
    def __init__(self, content, stop_reason="end_turn", request_id="req_falso", usage=None):
        self.content = content
        self.stop_reason = stop_reason
        self._request_id = request_id
        if usage is not None:
            self.usage = usage


class ClienteFalso:
    """Devolve (ou levanta) as respostas na ordem e guarda cópia dos argumentos."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = []
        self.messages = self

    def create(self, **kwargs):
        self.chamadas.append(copy.deepcopy(kwargs))
        r = self.respostas.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def _req():
    return httpx2.Request("POST", "https://x")


def classificar(texto, cliente, prompt, dormir=None):
    """classificador.classificar com espera falsa: nenhum teste espera de verdade.

    Os testes que conferem as esperas passam o próprio `dormir`.
    """
    return classificador.classificar(texto, cliente, prompt,
                                     dormir=dormir or (lambda segundos: None))


def ok(categorias=("atraso",), criticidade=()):
    corpo = {"categorias": list(categorias), "criticidade": list(criticidade)}
    return Resposta([Bloco("thinking"), Bloco("text", json.dumps(corpo))])


def texto(t, stop_reason="end_turn"):
    return Resposta([Bloco("text", t)], stop_reason=stop_reason)


# Cada caso: (nome, fábrica da falha, tipo esperado no registro, stop_reason esperado)
FALHAS = [
    ("json_invalido", lambda: texto("{isso não é json"), "json_invalido", None),
    ("categoria_inventada",
     lambda: texto('{"categorias": ["preco"], "criticidade": []}'), "saida_invalida", None),
    ("chave_extra",
     lambda: texto('{"categorias": [], "criticidade": [], "justificativa": "x"}'),
     "saida_invalida", None),
    ("item_repetido",
     lambda: texto('{"categorias": ["atraso", "atraso"], "criticidade": []}'),
     "saida_invalida", None),
    ("sem_bloco_text", lambda: Resposta([Bloco("thinking")]), "sem_bloco_texto", None),
    ("stop_max_tokens",
     lambda: texto('{"categorias": [], "criticidade": []}', stop_reason="max_tokens"),
     "stop_reason", "max_tokens"),
    ("stop_refusal", lambda: Resposta([], stop_reason="refusal"), "stop_reason", "refusal"),
    ("timeout", lambda: anthropic.APITimeoutError(request=_req()), "APITimeoutError", None),
    ("conexao", lambda: anthropic.APIConnectionError(request=_req()), "APIConnectionError", None),
]
IDS_FALHAS = [f[0] for f in FALHAS]
# Falha de CONTEÚDO da resposta (esgotada, vira None) x falha TÉCNICA sem resposta (esgotada, aborta).
FALHAS_DE_CONTEUDO = [f for f in FALHAS if f[0] not in ("timeout", "conexao")]
FALHAS_SEM_RESPOSTA = [f for f in FALHAS if f[0] in ("timeout", "conexao")]


# ---------- parâmetros e requisição ----------

def test_parametros_exatos():
    assert PARAMETROS == {
        "model": "claude-sonnet-5-5",
        "max_tokens": 400,
        "thinking": {"type": "between_tools"},
        "output_config": {
            "effort": "medium",
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
    }
    for proibido in ("temperature", "top_p", "top_k"):
        assert proibido not in PARAMETROS


def test_schema_usa_enums_do_contrato():
    assert SCHEMA["type"] == "object"
    assert SCHEMA["additionalProperties"] is False
    assert set(SCHEMA["required"]) == {"categorias", "criticidade"}
    assert set(SCHEMA["properties"]) == {"categorias", "criticidade"}
    cat = SCHEMA["properties"]["categorias"]
    crit = SCHEMA["properties"]["criticidade"]
    assert cat["type"] == "array" and cat["items"]["type"] == "string"
    assert crit["type"] == "array" and crit["items"]["type"] == "string"
    assert tuple(cat["items"]["enum"]) == CATEGORIAS
    assert tuple(crit["items"]["enum"]) == CRITICIDADES


def test_maximo_de_tres_tentativas():
    assert MAX_TENTATIVAS == 3


def test_requisicao_envia_so_o_texto():
    item = {
        "id_externo": "dev-ID-QUE-NAO-PODE-IR",
        "nota": 4,
        "texto": "o pedido demorou bastante",
        "justificativa": "JUSTIFICATIVA-QUE-NAO-PODE-IR",
        "fora_do_escopo": False,
    }
    cliente = ClienteFalso([ok()])
    classificar(item["texto"], cliente, PROMPT)

    assert len(cliente.chamadas) == 1
    kwargs = cliente.chamadas[0]
    assert kwargs == {**PARAMETROS, "system": PROMPT,
                      "messages": [{"role": "user", "content": item["texto"]}]}
    for proibido in ("temperature", "top_p", "top_k"):
        assert proibido not in kwargs
    bruto = repr(kwargs)
    for vazado in ("dev-ID-QUE-NAO-PODE-IR", "JUSTIFICATIVA-QUE-NAO-PODE-IR",
                   "nota", "justificativa", "fora_do_escopo", "id_externo"):
        assert vazado not in bruto


def test_resposta_valida_em_uma_tentativa():
    cliente = ClienteFalso([ok(["atraso", "embalagem"], ["saude"])])
    r = classificar("texto qualquer", cliente, PROMPT)
    assert r == {
        "saida": {"categorias": ["atraso", "embalagem"], "criticidade": ["saude"]},
        "tentativas": 1,
        "erros": [],
        "usos": [{"tentativa": 1, "input_tokens": None, "output_tokens": None, "modelo": None}],
    }


# ---------- registro de tokens (response.usage) ----------

def _com_uso(resposta, entrada, saida):
    resposta.usage = Uso(entrada, saida)
    return resposta


def test_uso_de_tokens_e_devolvido_por_tentativa():
    cliente = ClienteFalso([_com_uso(ok(), 2500, 40)])
    r = classificar("texto", cliente, PROMPT)
    assert r["usos"] == [{"tentativa": 1, "input_tokens": 2500, "output_tokens": 40,
                          "modelo": None}]


def test_uso_inclui_tentativas_com_resposta_ruim_e_pula_excecoes():
    cliente = ClienteFalso([
        anthropic.APITimeoutError(request=_req()),          # sem resposta: sem usage
        _com_uso(texto("{não é json"), 2500, 12),            # resposta ruim, mas gasta tokens
        _com_uso(ok(), 2500, 40),
    ])
    r = classificar("texto", cliente, PROMPT)
    assert r["tentativas"] == 3
    assert r["usos"] == [
        {"tentativa": 2, "input_tokens": 2500, "output_tokens": 12, "modelo": None},
        {"tentativa": 3, "input_tokens": 2500, "output_tokens": 40, "modelo": None},
    ]


def test_uso_registra_o_modelo_informado_pela_api_por_tentativa():
    # response.model é o que a API informou; sem o atributo, o modelo fica None.
    ruim = _com_uso(texto("{não é json"), 10, 1)
    ruim.model = "modelo-informado-A"
    bom = _com_uso(ok(), 10, 2)
    bom.model = "modelo-informado-B"
    r = classificar("texto", ClienteFalso([ruim, bom]), PROMPT)
    assert [u["modelo"] for u in r["usos"]] == ["modelo-informado-A", "modelo-informado-B"]


def test_registrar_o_modelo_nao_muda_a_requisicao():
    resposta = _com_uso(ok(), 1, 1)
    resposta.model = "qualquer"
    cliente = ClienteFalso([resposta])
    classificar("texto", cliente, PROMPT)
    assert cliente.chamadas[0] == {**PARAMETROS, "system": PROMPT,
                                   "messages": [{"role": "user", "content": "texto"}]}


def test_uso_em_tres_falhas_com_resposta():
    cliente = ClienteFalso([_com_uso(texto("x"), 100, 5) for _ in range(3)])
    r = classificar("texto", cliente, PROMPT)
    assert r["saida"] is None
    assert [u["tentativa"] for u in r["usos"]] == [1, 2, 3]
    assert sum(u["input_tokens"] for u in r["usos"]) == 300


def test_texto_vazio_nao_tem_uso():
    r = classificar("   ", ClienteFalso([]), PROMPT)
    assert r["usos"] == []


def test_registro_de_uso_nao_muda_a_requisicao():
    cliente = ClienteFalso([_com_uso(ok(), 1, 1)])
    classificar("texto", cliente, PROMPT)
    assert cliente.chamadas[0] == {**PARAMETROS, "system": PROMPT,
                                   "messages": [{"role": "user", "content": "texto"}]}


def test_varios_blocos_de_texto_sao_concatenados():
    resposta = Resposta([Bloco("text", '{"categorias": [], '), Bloco("text", '"criticidade": []}')])
    r = classificar("texto", ClienteFalso([resposta]), PROMPT)
    assert r["saida"] == {"categorias": [], "criticidade": []}


# ---------- retentativa ----------

@pytest.mark.parametrize("nome, falha, tipo, stop", FALHAS, ids=IDS_FALHAS)
def test_erro_tecnico_provoca_nova_tentativa(nome, falha, tipo, stop):
    cliente = ClienteFalso([falha(), ok(["entregador"])])
    r = classificar("texto", cliente, PROMPT)
    assert r["saida"] == {"categorias": ["entregador"], "criticidade": []}
    assert r["tentativas"] == 2
    assert len(cliente.chamadas) == 2
    assert len(r["erros"]) == 1
    erro = r["erros"][0]
    assert erro["tentativa"] == 1
    assert erro["tipo"] == tipo
    assert set(erro) <= CHAVES_DE_ERRO
    if stop is not None:
        assert erro["stop_reason"] == stop


def test_erro_de_status_http_registra_status_e_request_id():
    resposta_http = httpx2.Response(503, request=_req(), headers={"request-id": "req_503"})
    erro = anthropic.APIStatusError("falha", response=resposta_http, body=None)
    cliente = ClienteFalso([erro, ok()])
    r = classificar("texto", cliente, PROMPT)
    assert r["tentativas"] == 2
    assert r["erros"] == [
        {"tentativa": 1, "tipo": "APIStatusError", "status": 503, "request_id": "req_503"}
    ]


def test_sucesso_na_terceira_tentativa():
    cliente = ClienteFalso([texto("{ruim"), anthropic.APITimeoutError(request=_req()), ok()])
    r = classificar("texto", cliente, PROMPT)
    assert r["saida"] == {"categorias": ["atraso"], "criticidade": []}
    assert r["tentativas"] == 3
    assert [e["tentativa"] for e in r["erros"]] == [1, 2]


@pytest.mark.parametrize("nome, falha, tipo, stop", FALHAS_DE_CONTEUDO,
                         ids=[f[0] for f in FALHAS_DE_CONTEUDO])
def test_tres_falhas_de_conteudo_esgotam_em_none_com_argumentos_identicos(nome, falha, tipo, stop):
    # A 4ª resposta é válida e NÃO pode ser consumida. Falha de conteúdo esgotada NÃO aborta.
    cliente = ClienteFalso([falha(), falha(), falha(), ok()])
    r = classificar("o lanche chegou frio", cliente, PROMPT)
    assert r["saida"] is None
    assert r["tentativas"] == 3
    assert len(cliente.chamadas) == 3
    assert cliente.chamadas[0] == cliente.chamadas[1] == cliente.chamadas[2]
    assert [e["tentativa"] for e in r["erros"]] == [1, 2, 3]
    assert all(e["tipo"] == tipo for e in r["erros"])
    assert len(cliente.respostas) == 1


def test_excecao_que_nao_e_da_api_nao_e_repetida():
    cliente = ClienteFalso([RuntimeError("bug local"), ok()])
    with pytest.raises(RuntimeError):
        classificar("texto", cliente, PROMPT)
    assert len(cliente.chamadas) == 1


@pytest.mark.parametrize("vazio", ["", "   ", "\n\t", None])
def test_texto_vazio_nao_chama_a_api(vazio):
    cliente = ClienteFalso([ok()])
    r = classificar(vazio, cliente, PROMPT)
    assert r == {"saida": {"categorias": [], "criticidade": []}, "tentativas": 0, "erros": [],
                 "usos": []}
    assert cliente.chamadas == []


# ---------- registro seguro de erro ----------

def test_registro_de_erro_nao_guarda_mensagem_nem_texto_bruto():
    resposta_http = httpx2.Response(500, request=_req(), headers={"request-id": "req_500"})
    falhas = [
        anthropic.APIConnectionError(message="SEGREDO na conexão", request=_req()),
        anthropic.APIStatusError("SEGREDO no status", response=resposta_http,
                                 body={"error": {"message": "SEGREDO no corpo"}}),
        texto('{"categorias": ["SEGREDO"], "criticidade": []}'),
    ]
    cliente = ClienteFalso(falhas)
    r = classificar("texto", cliente, PROMPT)
    assert r["saida"] is None
    assert "SEGREDO" not in repr(r)
    for erro in r["erros"]:
        assert set(erro) <= CHAVES_DE_ERRO


def test_registro_nao_guarda_texto_bruto_de_json_invalido():
    cliente = ClienteFalso([texto("SEGREDO {"), texto("SEGREDO {"), texto("SEGREDO {")])
    r = classificar("texto", cliente, PROMPT)
    assert "SEGREDO" not in repr(r)


# ---------- status HTTP: o que repete e o que aborta ----------

def _erro_status(status, mensagem="falha", request_id="req_x"):
    resposta_http = httpx2.Response(status, request=_req(), headers={"request-id": request_id})
    return anthropic.APIStatusError(mensagem, response=resposta_http, body=None)


def test_limite_entre_499_e_500():
    # 499 aborta; 500 repete (o limite dos 5xx).
    with pytest.raises(classificador.ErroPermanenteDaAPI):
        classificar("texto", ClienteFalso([_erro_status(499), ok()]), PROMPT)
    cliente = ClienteFalso([_erro_status(500), ok()])
    assert classificar("texto", cliente, PROMPT)["tentativas"] == 2


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 529])
def test_status_repetivel_gera_nova_tentativa(status):
    cliente = ClienteFalso([_erro_status(status), ok(["atraso"])])
    r = classificar("texto", cliente, PROMPT)
    assert r["saida"] == {"categorias": ["atraso"], "criticidade": []}
    assert r["tentativas"] == 2
    assert r["erros"][0]["status"] == status
    assert len(cliente.chamadas) == 2


def test_status_repetivel_esgotado_levanta_erro_tecnico_esgotado():
    # Regra nova: erro técnico esgotado ABORTA (antes virava None).
    cliente = ClienteFalso([_erro_status(503) for _ in range(3)])
    with pytest.raises(classificador.ErroTecnicoEsgotado) as exc:
        classificar("texto", cliente, PROMPT)
    assert len(cliente.chamadas) == MAX_TENTATIVAS
    assert exc.value.status == 503


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 413, 422, 499])
def test_status_permanente_aborta_sem_repetir(status):
    cliente = ClienteFalso([_erro_status(status, request_id="req_perm"), ok()])
    with pytest.raises(classificador.ErroPermanenteDaAPI) as exc:
        classificar("texto", cliente, PROMPT)
    assert len(cliente.chamadas) == 1
    assert exc.value.status == status
    assert exc.value.request_id == "req_perm"


def test_status_permanente_depois_de_erro_repetivel_tambem_aborta():
    cliente = ClienteFalso([_erro_status(429), _erro_status(401), ok()])
    with pytest.raises(classificador.ErroPermanenteDaAPI):
        classificar("texto", cliente, PROMPT)
    assert len(cliente.chamadas) == 2


def test_erro_permanente_nao_vaza_mensagem_nem_corpo():
    cliente = ClienteFalso([_erro_status(401, mensagem="SEGREDO sk-falso")])
    with pytest.raises(classificador.ErroPermanenteDaAPI) as exc:
        classificar("texto", cliente, PROMPT)
    assert "SEGREDO" not in str(exc.value) and "SEGREDO" not in repr(exc.value)
    assert "401" in str(exc.value)


# ---------- hash do prompt (texto como carregado por carregar_prompt) ----------

def _prompt_em_pasta(tmp_path, monkeypatch, conteudo_bytes, nome="v9.md"):
    monkeypatch.setattr(classificador, "PASTA_PROMPTS", tmp_path)
    (tmp_path / nome).write_bytes(conteudo_bytes)


def test_sha256_do_prompt_e_o_hash_do_texto_utf8_com_lf(tmp_path, monkeypatch):
    import hashlib

    texto_lf = "linha um\nlinha dois com acento: ação\n"
    _prompt_em_pasta(tmp_path, monkeypatch, texto_lf.encode("utf-8"))
    esperado = hashlib.sha256(texto_lf.encode("utf-8")).hexdigest()
    assert classificador.sha256_do_prompt("v9") == esperado


def test_sha256_do_prompt_nao_muda_com_crlf_no_arquivo(tmp_path, monkeypatch):
    texto_lf = "linha um\nlinha dois com acento: ação\n"
    _prompt_em_pasta(tmp_path, monkeypatch, texto_lf.encode("utf-8"), "v8.md")
    _prompt_em_pasta(tmp_path, monkeypatch, texto_lf.replace("\n", "\r\n").encode("utf-8"), "v9.md")
    assert classificador.sha256_do_prompt("v9") == classificador.sha256_do_prompt("v8")


def test_sha256_do_prompt_e_do_mesmo_texto_que_carregar_prompt(tmp_path, monkeypatch):
    import hashlib

    _prompt_em_pasta(tmp_path, monkeypatch, "a\r\nb\r\n".encode("utf-8"))
    carregado = classificador.carregar_prompt("v9")
    assert "\r" not in carregado
    assert classificador.sha256_do_prompt("v9") == hashlib.sha256(carregado.encode("utf-8")).hexdigest()


def test_sha256_do_prompt_rejeita_versao_invalida():
    with pytest.raises(ValueError):
        classificador.sha256_do_prompt("../v0")


def test_sha256_do_texto_e_o_hash_exato_da_string_em_utf8():
    import hashlib

    texto_qualquer = "avaliação\ncom acento: ç\r\nnão normaliza nada"
    assert classificador.sha256_do_texto(texto_qualquer) == \
        hashlib.sha256(texto_qualquer.encode("utf-8")).hexdigest()
    # Hash da MESMA string que seria enviada: sem normalização escondida.
    assert classificador.sha256_do_texto("a\r\nb") != classificador.sha256_do_texto("a\nb")


def test_sha256_do_prompt_usa_sha256_do_texto_sobre_o_texto_carregado(tmp_path, monkeypatch):
    _prompt_em_pasta(tmp_path, monkeypatch, "um\r\ndois\r\n".encode("utf-8"))
    recebidos = []
    real = classificador.sha256_do_texto
    monkeypatch.setattr(classificador, "sha256_do_texto",
                        lambda t: recebidos.append(t) or real(t))
    assert classificador.sha256_do_prompt("v9") == real(classificador.carregar_prompt("v9"))
    assert recebidos == [classificador.carregar_prompt("v9")]


def test_docstring_do_sha256_do_prompt_nao_diz_mais_que_nao_e_usado():
    doc = classificador.sha256_do_prompt.__doc__
    assert "Ainda não é usado" not in doc
    assert "congelamento" in doc.lower()


def test_erro_permanente_nao_guarda_o_erro_original_em_nenhum_lugar():
    import traceback

    segredo = "SEGREDO-NA-MENSAGEM sk-falso"
    cliente = ClienteFalso([_erro_status(401, mensagem=segredo)])
    with pytest.raises(classificador.ErroPermanenteDaAPI) as exc:
        classificar("texto", cliente, PROMPT)
    e = exc.value
    # Levantada fora do bloco except: nada de contexto implícito nem causa explícita.
    assert e.__context__ is None
    assert e.__cause__ is None
    completo = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    for texto_visivel in (str(e), repr(e), completo, repr(e.args)):
        assert "SEGREDO" not in texto_visivel and "sk-falso" not in texto_visivel
    assert "401" in completo


def test_erro_permanente_nao_e_value_error_nem_api_error():
    # Não pode ser capturado por engano como falha técnica comum.
    assert not issubclass(classificador.ErroPermanenteDaAPI, anthropic.APIError)
    assert issubclass(classificador.ErroPermanenteDaAPI, Exception)


# ---------- regra nova: conteúdo x erro técnico, esperas e aborto ----------

TECNICAS = [
    ("timeout", lambda: anthropic.APITimeoutError(request=_req())),
    ("conexao", lambda: anthropic.APIConnectionError(request=_req())),
    ("status_408", lambda: _erro_status(408)),
    ("status_409", lambda: _erro_status(409)),
    ("status_429", lambda: _erro_status(429)),
    ("status_500", lambda: _erro_status(500)),
    ("status_503", lambda: _erro_status(503)),
    ("status_529", lambda: _erro_status(529)),
]
IDS_TECNICAS = [t[0] for t in TECNICAS]


class ClienteComEventos(ClienteFalso):
    """Como ClienteFalso, mas registra cada chamada numa lista compartilhada com o dormir."""

    def __init__(self, respostas, eventos):
        super().__init__(respostas)
        self.eventos = eventos

    def create(self, **kwargs):
        self.eventos.append("chamada")
        return super().create(**kwargs)


def test_constantes_derivadas_da_regra_incluindo_esperas():
    regra = classificador.REGRA_RETENTATIVA
    assert classificador.MAX_TENTATIVAS == regra["max_tentativas"] == 3
    assert classificador.STATUS_REPETIVEIS == tuple(regra["status_transitorios"]) == (408, 409, 429)
    assert classificador.ESPERAS == tuple(regra["espera_segundos"]) == (2, 5)
    # uma espera antes de cada tentativa, menos da primeira
    assert len(classificador.ESPERAS) == classificador.MAX_TENTATIVAS - 1


def test_dormir_padrao_e_time_sleep():
    assert inspect.signature(classificador.classificar).parameters["dormir"].default is time.sleep


@pytest.mark.parametrize("nome, falha, tipo, stop", FALHAS_DE_CONTEUDO,
                         ids=[f[0] for f in FALHAS_DE_CONTEUDO])
def test_conteudo_esgotado_vira_none_sem_aborto_e_espera_2_e_5(nome, falha, tipo, stop):
    esperas = []
    cliente = ClienteFalso([falha(), falha(), falha(), ok()])
    r = classificar("texto", cliente, PROMPT, dormir=esperas.append)
    assert r["saida"] is None and r["tentativas"] == 3
    assert len(cliente.chamadas) == 3
    assert esperas == [2, 5]


@pytest.mark.parametrize("nome, falha", TECNICAS, ids=IDS_TECNICAS)
def test_erro_tecnico_esgotado_aborta_com_tres_chamadas_e_esperas_2_e_5(nome, falha):
    esperas = []
    cliente = ClienteFalso([falha(), falha(), falha(), ok()])  # a 4ª NÃO pode ser consumida
    with pytest.raises(classificador.ErroTecnicoEsgotado):
        classificar("texto", cliente, PROMPT, dormir=esperas.append)
    assert len(cliente.chamadas) == 3
    assert cliente.chamadas[0] == cliente.chamadas[1] == cliente.chamadas[2]
    assert esperas == [2, 5]
    assert len(cliente.respostas) == 1


@pytest.mark.parametrize("nome, falha, tipo, stop", FALHAS_SEM_RESPOSTA,
                         ids=[f[0] for f in FALHAS_SEM_RESPOSTA])
def test_excecao_sem_resposta_http_esgotada_tambem_aborta(nome, falha, tipo, stop):
    cliente = ClienteFalso([falha(), falha(), falha()])
    with pytest.raises(classificador.ErroTecnicoEsgotado) as exc:
        classificar("texto", cliente, PROMPT)
    assert exc.value.tipo_excecao == tipo
    assert exc.value.status is None and exc.value.request_id is None


def test_erro_tecnico_esgotado_guarda_o_da_ultima_falha():
    cliente = ClienteFalso([anthropic.APITimeoutError(request=_req()),
                            _erro_status(503, request_id="req_503"),
                            _erro_status(529, request_id="req_529")])
    with pytest.raises(classificador.ErroTecnicoEsgotado) as exc:
        classificar("texto", cliente, PROMPT)
    assert exc.value.tipo_excecao == "APIStatusError"
    assert exc.value.status == 529
    assert exc.value.request_id == "req_529"
    assert "529" in str(exc.value) and "req_529" in str(exc.value)


# Falha mista: a ÚLTIMA falha decide. conteúdo -> None; técnica -> aborta.

def test_mista_tecnica_tecnica_conteudo_vira_none():
    cliente = ClienteFalso([anthropic.APITimeoutError(request=_req()), _erro_status(503),
                            texto("{ruim")])
    r = classificar("texto", cliente, PROMPT)
    assert r["saida"] is None and r["tentativas"] == 3
    assert [e["tipo"] for e in r["erros"]] == ["APITimeoutError", "APIStatusError", "json_invalido"]


def test_mista_conteudo_conteudo_tecnica_aborta():
    cliente = ClienteFalso([texto("{ruim"), texto("{ruim"),
                            anthropic.APITimeoutError(request=_req())])
    with pytest.raises(classificador.ErroTecnicoEsgotado) as exc:
        classificar("texto", cliente, PROMPT)
    assert exc.value.tipo_excecao == "APITimeoutError"
    assert len(cliente.chamadas) == 3


def test_mista_tecnica_conteudo_tecnica_aborta_e_conteudo_tecnica_conteudo_vira_none():
    with pytest.raises(classificador.ErroTecnicoEsgotado):
        classificar("texto", ClienteFalso([_erro_status(500), texto("{ruim"), _erro_status(502)]),
                    PROMPT)
    r = classificar("texto", ClienteFalso([texto("{ruim"), _erro_status(500), texto("{ruim")]),
                    PROMPT)
    assert r["saida"] is None


def test_sucesso_na_primeira_nao_espera_nada():
    esperas = []
    r = classificar("texto", ClienteFalso([ok()]), PROMPT, dormir=esperas.append)
    assert r["tentativas"] == 1
    assert esperas == []


@pytest.mark.parametrize("falha", [lambda: texto("{ruim"),
                                   lambda: anthropic.APITimeoutError(request=_req()),
                                   lambda: _erro_status(503)],
                         ids=["conteudo", "timeout", "status_503"])
def test_sucesso_na_segunda_espera_so_2(falha):
    esperas = []
    r = classificar("texto", ClienteFalso([falha(), ok(["embalagem"])]), PROMPT,
                    dormir=esperas.append)
    assert r["tentativas"] == 2 and r["saida"]["categorias"] == ["embalagem"]
    assert esperas == [2]


def test_sucesso_na_terceira_espera_2_e_5_e_para():
    esperas = []
    cliente = ClienteFalso([_erro_status(429), texto("{ruim"), ok()])
    r = classificar("texto", cliente, PROMPT, dormir=esperas.append)
    assert r["tentativas"] == 3
    assert esperas == [2, 5]


def test_espera_acontece_antes_de_cada_nova_chamada_e_nunca_antes_da_primeira():
    eventos = []
    cliente = ClienteComEventos([texto("{ruim"), texto("{ruim"), ok()], eventos)
    classificar("texto", cliente, PROMPT, dormir=lambda s: eventos.append(("espera", s)))
    assert eventos == ["chamada", ("espera", 2), "chamada", ("espera", 5), "chamada"]


def test_permanente_na_primeira_nao_espera_nada():
    esperas = []
    with pytest.raises(classificador.ErroPermanenteDaAPI):
        classificar("texto", ClienteFalso([_erro_status(401), ok()]), PROMPT,
                    dormir=esperas.append)
    assert esperas == []


def test_permanente_na_segunda_so_espera_antes_da_chamada_que_a_trouxe():
    eventos = []
    cliente = ClienteComEventos([_erro_status(503), _erro_status(401), ok()], eventos)
    with pytest.raises(classificador.ErroPermanenteDaAPI):
        classificar("texto", cliente, PROMPT, dormir=lambda s: eventos.append(("espera", s)))
    # nenhuma espera depois do erro permanente, nenhuma 3ª chamada
    assert eventos == ["chamada", ("espera", 2), "chamada"]


@pytest.mark.parametrize("vazio", ["", "   ", None])
def test_texto_vazio_nao_chama_nem_espera(vazio):
    esperas = []
    cliente = ClienteFalso([ok()])
    classificar(vazio, cliente, PROMPT, dormir=esperas.append)
    assert cliente.chamadas == [] and esperas == []


def test_erro_tecnico_esgotado_nao_guarda_o_erro_original_em_nenhum_lugar():
    segredo = "SEGREDO-NA-MENSAGEM sk-falso"
    falhas = [anthropic.APIConnectionError(message=segredo, request=_req()),
              _erro_status(503, mensagem=segredo),
              anthropic.APIConnectionError(message=segredo, request=_req())]
    with pytest.raises(classificador.ErroTecnicoEsgotado) as exc:
        classificar("texto", ClienteFalso(falhas), PROMPT)
    e = exc.value
    # Levantada fora do bloco except: nada de contexto implícito nem causa explícita.
    assert e.__context__ is None
    assert e.__cause__ is None
    completo = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    for visivel in (str(e), repr(e), completo, repr(e.args)):
        assert "SEGREDO" not in visivel and "sk-falso" not in visivel
    assert "APIConnectionError" in completo


def test_erro_tecnico_esgotado_com_status_tambem_nao_vaza_corpo():
    falhas = [_erro_status(503, mensagem="SEGREDO corpo", request_id="req_a") for _ in range(3)]
    with pytest.raises(classificador.ErroTecnicoEsgotado) as exc:
        classificar("texto", ClienteFalso(falhas), PROMPT)
    e = exc.value
    assert e.__context__ is None and e.__cause__ is None
    completo = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    assert "SEGREDO" not in completo
    assert "503" in completo and "req_a" in completo


def test_hierarquia_dos_erros_que_abortam():
    base = classificador.ErroTecnicoDaAPI
    assert issubclass(base, Exception)
    assert issubclass(classificador.ErroPermanenteDaAPI, base)
    assert issubclass(classificador.ErroTecnicoEsgotado, base)
    # irmãs: uma não é a outra
    assert not issubclass(classificador.ErroTecnicoEsgotado, classificador.ErroPermanenteDaAPI)
    assert not issubclass(classificador.ErroPermanenteDaAPI, classificador.ErroTecnicoEsgotado)
    # nenhuma pode ser capturada por engano como APIError
    for classe in (base, classificador.ErroPermanenteDaAPI, classificador.ErroTecnicoEsgotado):
        assert not issubclass(classe, anthropic.APIError)
        assert not issubclass(classe, ValueError)


def test_docstring_do_modulo_descreve_a_regra_decidida_pela_ultima_falha():
    doc = classificador.__doc__
    assert "decidida pela ÚLTIMA falha" in doc
    assert "ErroTecnicoEsgotado" in doc
    for espera in ("2 s", "5 s"):
        assert espera in doc


# ---------- cliente real (construído sem rede e sem ler o .env) ----------

def test_criar_cliente_sem_chave_encerra_sem_mostrar_valor(monkeypatch):
    chamados = []
    monkeypatch.setattr(classificador, "load_dotenv", lambda caminho: chamados.append(caminho))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        classificador.criar_cliente()
    assert "ANTHROPIC_API_KEY" in str(exc.value)
    assert chamados == [classificador.RAIZ / ".env"]


def test_criar_cliente_sem_retentativa_do_sdk_e_timeout_60(monkeypatch):
    monkeypatch.setattr(classificador, "load_dotenv", lambda caminho: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-falsa-de-teste")
    cliente = classificador.criar_cliente()
    assert cliente.max_retries == 0
    assert cliente.timeout == 60
