"""API do Radar de Avaliações. Bloco 2.2 da Fase 2: POST /avaliacoes.

Fonte da verdade: docs/ESPECIFICACAO.md. Servidor: uvicorn app.main:app

Variáveis de ambiente (nunca no código nem no Git):
- RADAR_API_KEY: chave esperada no cabeçalho X-API-Key. Ausente ou vazia, toda requisição é recusada.
- RADAR_DB: caminho do arquivo SQLite.
- ANTHROPIC_API_KEY: lida por app/classificador.py (criar_cliente) a partir do .env.
"""

import hmac
import logging
import os
from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, StrictInt, StrictStr, field_validator

from app import banco, classificador
from app.classificador import ErroPermanenteDaAPI, ErroTecnicoEsgotado

VERSAO_PROMPT = "v1"  # prompt congelado (prompts/classificador/v1.md)

logger = logging.getLogger("radar.api")


class ErroDeConfiguracao(Exception):
    """Variável de ambiente ou chave ausente. Vira 500 genérico; o detalhe fica fora da resposta."""


class NovaAvaliacao(BaseModel):
    id_externo: StrictStr = Field(min_length=1)
    texto: StrictStr
    nota: StrictInt
    recebida_em: StrictStr

    @field_validator("recebida_em")
    @classmethod
    def _data_iso_com_fuso(cls, valor):
        try:
            instante = datetime.fromisoformat(valor)
        except ValueError:
            raise ValueError("recebida_em deve ser uma data ISO 8601") from None
        if instante.utcoffset() is None:
            raise ValueError("recebida_em precisa ter fuso (por exemplo -03:00 ou Z)")
        try:  # o mesmo cálculo que a camada do banco fará, antes de gastar uma chamada ao modelo
            instante.astimezone(timezone.utc) + timedelta(days=banco.PRAZO_RESPOSTA_DIAS)
        except OverflowError:
            raise ValueError("recebida_em fora do intervalo aceito") from None
        return valor


def _nao_autorizado():
    return HTTPException(
        status_code=401,
        detail="não autorizado",
        headers={"WWW-Authenticate": "X-API-Key"},
    )


@lru_cache(maxsize=1)
def _avisar_chave_ausente():
    """Avisa uma única vez (por processo) que a chave não está configurada, sem inundar o log."""
    logger.warning("RADAR_API_KEY ausente ou vazia: toda requisição será recusada com 401")


def exigir_chave(x_api_key: Optional[str]) -> None:
    """Confere o X-API-Key. A chave esperada vem de RADAR_API_KEY, lida a cada requisição.

    Ausente ou vazia, a variável recusa tudo: uma chave vazia nunca é aceita. A comparação é feita
    em bytes e em tempo constante, o que também evita TypeError com caracteres não ASCII.
    """
    esperada = os.environ.get("RADAR_API_KEY", "")
    if not esperada:
        _avisar_chave_ausente()
        raise _nao_autorizado()
    if not x_api_key:
        raise _nao_autorizado()
    if not hmac.compare_digest(x_api_key.encode("utf-8"), esperada.encode("utf-8")):
        raise _nao_autorizado()


class RotaComChave(APIRoute):
    """Rota que exige o X-API-Key ANTES de ler o corpo.

    O FastAPI decodifica o JSON do corpo antes de resolver as dependências; uma dependência de
    autenticação deixaria um corpo malformado, sem chave, responder 422 em vez de 401. Conferir a
    chave aqui, antes de qualquer leitura do corpo, mantém a regra "sem chave, 401".
    """

    def get_route_handler(self):
        tratar = super().get_route_handler()

        async def tratar_com_chave(request: Request):
            exigir_chave(request.headers.get("x-api-key"))
            return await tratar(request)

        return tratar_com_chave


@lru_cache(maxsize=1)
def _recursos():
    """Cliente do modelo e prompt congelado, criados uma vez e só quando a primeira avaliação chega."""
    try:
        cliente = classificador.criar_cliente()
    except SystemExit:  # criar_cliente encerra o processo se a chave da Anthropic faltar
        raise ErroDeConfiguracao("chave da Anthropic ausente") from None
    return cliente, classificador.carregar_prompt(VERSAO_PROMPT)


def _classificar_com_o_modelo(texto):
    cliente, prompt = _recursos()
    return classificador.classificar(texto, cliente, prompt)


def obter_classificador():
    """Dependência substituível: os testes a trocam por um falso e nunca chamam a API real."""
    return _classificar_com_o_modelo


def _processar(nova: NovaAvaliacao, resposta: Response, classificar):
    caminho = os.environ.get("RADAR_DB")
    if not caminho:
        raise ErroDeConfiguracao("RADAR_DB não definida")

    with closing(banco.abrir(caminho)) as conn:
        existente = banco.buscar_avaliacao_por_id_externo(conn, nova.id_externo)
        if existente is not None:  # duplicata: ignora, sem gastar uma chamada ao modelo
            resposta.status_code = 200
            return {"avaliacao_id": existente["id"], "criada": False}

        saida = classificar(nova.texto)["saida"]
        if saida is None:  # conteúdo ruim depois da regra de retentativa
            status, categorias, criticidade = "invalida", [], []
        else:
            status, categorias, criticidade = "ok", saida["categorias"], saida["criticidade"]

        gravado = banco.gravar_avaliacao(
            conn,
            id_externo=nova.id_externo,
            texto=nova.texto,
            nota=nova.nota,
            recebida_em=nova.recebida_em,
            categorias=categorias,
            criticidade=criticidade,
            status_classificacao=status,
        )

    if not gravado.criada:  # outra requisição gravou o mesmo id_externo enquanto esta classificava
        resposta.status_code = 200
        return {"avaliacao_id": gravado.avaliacao_id, "criada": False}
    return {"avaliacao_id": gravado.avaliacao_id, "criada": True, "alerta_id": gravado.alerta_id}


def criar_app() -> FastAPI:
    # Sem /docs, /redoc e /openapi.json: a documentação automática não precisa ficar aberta sem chave.
    app = FastAPI(title="Radar de Avaliações", docs_url=None, redoc_url=None, openapi_url=None)
    com_chave = APIRouter(route_class=RotaComChave)

    @com_chave.post("/avaliacoes", status_code=201)
    def receber_avaliacao(
        nova: NovaAvaliacao,
        resposta: Response,
        classificar=Depends(obter_classificador),
    ):
        # Nenhuma resposta nem log leva mensagem de exceção, corpo de erro da API ou chave:
        # só o tipo e, para erros da API do classificador, o status e o request_id.
        try:
            return _processar(nova, resposta, classificar)
        except ErroTecnicoEsgotado as e:
            logger.warning(
                "classificador indisponível depois das retentativas (status=%s, request_id=%s)",
                e.status, e.request_id,
            )
            raise HTTPException(
                status_code=503,
                detail="classificador indisponível; reenvie a avaliação mais tarde",
            ) from None
        except ErroPermanenteDaAPI as e:
            logger.error(
                "erro permanente da API do classificador (status=%s, request_id=%s)",
                e.status, e.request_id,
            )
            raise HTTPException(status_code=500, detail="falha interna") from None
        except Exception as e:  # noqa: BLE001 - qualquer outra falha vira 500 genérico
            logger.error("falha inesperada ao receber a avaliação (%s)", type(e).__name__)
            raise HTTPException(status_code=500, detail="falha interna") from None

    app.include_router(com_chave)
    return app


app = criar_app()
