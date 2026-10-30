"""Classificador de avaliações: uma chamada ao modelo por avaliação.

Dados simulados. Projeto independente, sem vínculo com o iFood.

REGRA DE RETENTATIVA (vigente; vale para o ajuste e para a avaliação oficial). A constante
REGRA_RETENTATIVA, abaixo, é esta regra em forma de dado: tem o hash conferido no congelamento
(avaliacao/congelamento.py) e um teste confere que ela e o comportamento de classificar() batem.

Há dois tipos de falha que pedem nova tentativa:
- Falha de CONTEÚDO da resposta:
  (a) stop_reason "max_tokens" ou "refusal";
  (b) resposta sem nenhum bloco do tipo "text";
  (c) texto que não é JSON (json.loads falha);
  (d) JSON fora do contrato (contrato.SaidaInvalida: categoria inventada, chave extra,
      item repetido etc.).
- Erro TÉCNICO transitório: exceção anthropic.APIError sem resposta HTTP (tempo esgotado, falha
  de conexão) ou com status HTTP 408, 409, 429 ou qualquer 5xx.

Regras:
- Qualquer das duas gera nova tentativa, até 3 chamadas no total (MAX_TENTATIVAS), com exatamente
  os mesmos argumentos. Nada é alterado entre as tentativas.
- Espera fixa antes da 2ª tentativa (2 s) e antes da 3ª (5 s); nenhuma espera antes da 1ª. O tempo
  é injetável: classificar(texto, cliente, prompt, dormir=time.sleep).
- Esgotadas as 3 chamadas, a regra é decidida pela ÚLTIMA falha:
  * se a última falha foi de CONTEÚDO, a saída é None (saída inválida; é o ÚNICO caso de saída
    inválida nas métricas);
  * se a última falha foi TÉCNICA, levanta ErroTecnicoEsgotado, que ABORTA a execução inteira.
    Erro técnico esgotado nunca vira saída inválida, e o chamador não deve gravar resultados
    parciais nem tratá-los como métrica.
  Falhas mistas seguem a mesma regra: técnica, técnica, conteúdo dá None; conteúdo, conteúdo,
  técnica aborta.
- Qualquer OUTRO status HTTP (400, 401, 403, 404, 422...) é erro permanente: NÃO é repetido e
  levanta ErroPermanenteDaAPI, que também aborta a execução inteira.
- Não há troca de modelo nem fallback em caso de recusa.
- Qualquer outra exceção (erro de programação) sobe sem nova tentativa.
- O SDK é criado com max_retries=0: as únicas novas tentativas são as daqui.

Registro de erro: só "tentativa", "tipo", "status", "request_id" e "stop_reason", conforme o
caso. Nunca a mensagem da exceção, cabeçalhos ou o texto bruto da resposta. As exceções que
abortam (ErroTecnicoEsgotado, ErroPermanenteDaAPI) guardam só tipo, status e request_id e são
levantadas fora de qualquer except, sem __context__ nem __cause__.

O modelo recebe só o texto da avaliação (nada de nota, id, gabarito ou justificativa).
"""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from app import contrato

RAIZ = Path(__file__).resolve().parent.parent
PASTA_PROMPTS = RAIZ / "prompts" / "classificador"

# Regra de retentativa em forma de dado, para ser registrada e ter o hash conferido no
# congelamento da avaliação (avaliacao/congelamento.py). Descreve o comportamento de classificar();
# um teste confere que as duas coisas batem.
REGRA_RETENTATIVA = {
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

MAX_TENTATIVAS = REGRA_RETENTATIVA["max_tentativas"]
STATUS_REPETIVEIS = tuple(REGRA_RETENTATIVA["status_transitorios"])  # além de qualquer 5xx
ESPERAS = tuple(REGRA_RETENTATIVA["espera_segundos"])  # antes da 2ª e da 3ª tentativa


class ErroTecnicoDaAPI(Exception):
    """Base dos erros da API que ABORTAM a execução inteira (permanente ou técnico esgotado).

    Carrega só o status e o request_id; nunca a mensagem nem o corpo do erro da API. Não herda de
    anthropic.APIError, para não ser capturada por engano como falha técnica comum.
    """

    def __init__(self, mensagem, status, request_id):
        super().__init__(mensagem)
        self.status = status
        self.request_id = request_id


class ErroPermanenteDaAPI(ErroTecnicoDaAPI):
    """Status HTTP que repetir não resolve (chave inválida, pedido malformado, modelo inexistente).

    Carrega só o status e o request_id; nunca a mensagem nem o corpo do erro da API.
    """

    def __init__(self, status, request_id):
        super().__init__(f"erro permanente da API (status {status}, request_id {request_id})",
                         status, request_id)


class ErroTecnicoEsgotado(ErroTecnicoDaAPI):
    """As 3 chamadas falharam e a ÚLTIMA falha foi técnica (tempo esgotado, conexão, 408, 409,
    429 ou 5xx). Aborta a execução inteira: não vira saída inválida.

    Guarda só o tipo da exceção da última falha, o status (None se não houve resposta HTTP) e o
    request_id (None se não houve resposta HTTP); nunca a mensagem nem o corpo do erro.
    """

    def __init__(self, tipo_excecao, status, request_id):
        super().__init__(
            f"erro técnico esgotado após {MAX_TENTATIVAS} tentativas "
            f"(tipo {tipo_excecao}, status {status}, request_id {request_id})",
            status, request_id)
        self.tipo_excecao = tipo_excecao


def _repetivel(e):
    if isinstance(e, anthropic.APIStatusError):
        return e.status_code in STATUS_REPETIVEIS or e.status_code >= 500
    return True  # tempo esgotado e falha de conexão

SCHEMA = {
    "type": "object",
    "properties": {
        "categorias": {
            "type": "array",
            "items": {"type": "string", "enum": list(contrato.CATEGORIAS)},
        },
        "criticidade": {
            "type": "array",
            "items": {"type": "string", "enum": list(contrato.CRITICIDADES)},
        },
    },
    "required": ["categorias", "criticidade"],
    "additionalProperties": False,
}

# Parâmetros fixos de toda chamada. temperature, top_p e top_k NÃO são enviados:
# este modelo os rejeita (e o SDK 2.3.0 nem os aceita em messages.create).
# A variação é medida repetindo as rodadas, não controlada por amostragem.
PARAMETROS = {
    "model": "claude-sonnet-5-5",
    "max_tokens": 400,
    "thinking": {"type": "between_tools"},
    "output_config": {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": SCHEMA},
    },
}

_VERSAO_VALIDA = re.compile(r"v\d+")


def carregar_prompt(versao):
    """Lê prompts/classificador/{versao}.md, o texto exato do system prompt."""
    if not isinstance(versao, str) or not _VERSAO_VALIDA.fullmatch(versao):
        raise ValueError("versão do prompt deve ter a forma v0, v1, v2...")
    return (PASTA_PROMPTS / f"{versao}.md").read_text(encoding="utf-8")


def sha256_do_texto(texto):
    """SHA-256 (hex) de uma string de prompt, em UTF-8, sem nenhuma normalização.

    É o hash da MESMA string que vai à API: quem lê o prompt uma vez calcula o hash dessa string e
    envia essa string (avaliacao/rodar_oficial.py).
    """
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def sha256_do_prompt(versao):
    """SHA-256 (hex) do texto do prompt tal como carregar_prompt o devolve (UTF-8, quebras LF).

    Não depende de o arquivo ter CRLF no disco (Windows): carregar_prompt já lê com quebras
    universais, e o texto enviado à API é esse. É o hash que o registro de congelamento guarda e que
    a avaliação oficial confere (avaliacao/congelamento.py).
    """
    return sha256_do_texto(carregar_prompt(versao))


def criar_cliente():
    """Cliente real. A chave vem só do .env (ANTHROPIC_API_KEY) e nunca é exibida."""
    load_dotenv(RAIZ / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY ausente. Defina-a no .env (o valor nunca é exibido).")
    return anthropic.Anthropic(max_retries=0, timeout=60)


def _erro_de_excecao(tentativa, e):
    if isinstance(e, anthropic.APIStatusError):
        return {"tentativa": tentativa, "tipo": type(e).__name__,
                "status": e.status_code, "request_id": e.request_id}
    # Sem resposta HTTP (conexão, tempo esgotado): só o tipo da exceção.
    return {"tentativa": tentativa, "tipo": type(e).__name__, "status": None, "request_id": None}


def _erro_de_resposta(tentativa, tipo, resposta):
    return {"tentativa": tentativa, "tipo": tipo,
            "request_id": getattr(resposta, "_request_id", None),
            "stop_reason": getattr(resposta, "stop_reason", None)}


def _uso(tentativa, resposta):
    uso = getattr(resposta, "usage", None)
    return {"tentativa": tentativa,
            "input_tokens": getattr(uso, "input_tokens", None),
            "output_tokens": getattr(uso, "output_tokens", None)}


def _interpretar(resposta):
    """Devolve (saida, None) ou (None, tipo_do_erro)."""
    if resposta.stop_reason in ("max_tokens", "refusal"):
        return None, "stop_reason"
    textos = [bloco.text for bloco in resposta.content if bloco.type == "text"]
    if not textos:
        return None, "sem_bloco_texto"
    try:
        obj = json.loads("".join(textos))
    except json.JSONDecodeError:
        return None, "json_invalido"
    try:
        return contrato.validar_saida(obj), None
    except contrato.SaidaInvalida:
        return None, "saida_invalida"


def classificar(texto, cliente, prompt, dormir=time.sleep):
    """Classifica UMA avaliação.

    Devolve {"saida": dict validado ou None, "tentativas": int, "erros": [registros],
    "usos": [{"tentativa", "input_tokens", "output_tokens"}]}.
    "usos" guarda response.usage de cada tentativa que recebeu resposta (inclusive as
    respostas ruins, que também gastam tokens); tentativas que levantaram exceção não têm
    resposta e ficam fora. Valor ausente no usage vira None.

    Regra de retentativa: ver o docstring do módulo. Resumo: até 3 chamadas, com espera de
    ESPERAS[0] e ESPERAS[1] segundos (via `dormir`) antes da 2ª e da 3ª; esgotadas, a ÚLTIMA falha
    decide: de conteúdo devolve saida None; técnica levanta ErroTecnicoEsgotado. Status HTTP
    permanente levanta ErroPermanenteDaAPI na hora.

    `cliente` é injetado: precisa só de .messages.create(**kwargs). `dormir(segundos)` também.
    """
    if contrato.texto_vazio(texto):
        return {"saida": contrato.saida_texto_vazio(), "tentativas": 0, "erros": [], "usos": []}

    argumentos = {
        **PARAMETROS,
        "system": prompt,
        "messages": [{"role": "user", "content": texto}],
    }
    erros = []
    usos = []
    ultima_tecnica = None  # (tipo, status, request_id) se a última falha foi técnica
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        if tentativa > 1:
            dormir(ESPERAS[tentativa - 2])
        permanente = None
        tecnica = None
        try:
            resposta = cliente.messages.create(**argumentos)
        except anthropic.APIError as e:
            if _repetivel(e):
                registro = _erro_de_excecao(tentativa, e)
                erros.append(registro)
                tecnica = (registro["tipo"], registro["status"], registro["request_id"])
            else:
                permanente = (e.status_code, e.request_id)
        if permanente is not None:
            # Fora do except: a exceção não herda __context__ com a mensagem e o corpo originais.
            raise ErroPermanenteDaAPI(*permanente)
        if tecnica is not None:
            ultima_tecnica = tecnica
            continue
        usos.append(_uso(tentativa, resposta))
        saida, tipo = _interpretar(resposta)
        if saida is not None:
            return {"saida": saida, "tentativas": tentativa, "erros": erros, "usos": usos}
        erros.append(_erro_de_resposta(tentativa, tipo, resposta))
        ultima_tecnica = None  # a última falha foi de conteúdo
    if ultima_tecnica is not None:
        # Fora do except, como o permanente: sem __context__ nem __cause__.
        raise ErroTecnicoEsgotado(*ultima_tecnica)
    return {"saida": None, "tentativas": MAX_TENTATIVAS, "erros": erros, "usos": usos}
