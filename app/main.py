"""API do Radar de Avaliações. POST /avaliacoes (bloco 2.2), alerta crítico via Telegram (bloco 2.3),
token do painel (bloco 2.5: dependência exigir_token, Referrer-Policy e filtro do log de acesso) e
GET /resumo-semanal (bloco 2.7) e recuperação de alertas (bloco 2.8: POST /alertas/reenviar e
reenvio dos pendentes na subida do servidor).

Fonte da verdade: docs/ESPECIFICACAO.md. Servidor: uvicorn app.main:app --env-file .env

Variáveis de ambiente (nunca no código nem no Git):
- RADAR_API_KEY: chave esperada no cabeçalho X-API-Key. Ausente ou vazia, toda requisição é recusada.
- RADAR_DB: caminho do arquivo SQLite.
- RADAR_URL_BASE: endereço público do app (http ou https, sem barra final), base do link do painel.
- ANTHROPIC_API_KEY: lida por app/classificador.py (criar_cliente) a partir do .env.
- RADAR_TELEGRAM_TOKEN e RADAR_TELEGRAM_CHAT_ID: lidas por app/telegram.py a partir do .env.
"""

import hmac
import json
import logging
import os
import re
import threading
from contextlib import asynccontextmanager, closing
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from app import (
    alertas, banco, classificador, config_ranking, consultas, ranking, resumo, semana, telegram, tokens, trechos,
)
from app.classificador import ErroPermanenteDaAPI, ErroTecnicoEsgotado

VERSAO_PROMPT = "v1"  # prompt congelado (prompts/classificador/v1.md)

logger = logging.getLogger("radar.api")

# O token do painel vai no caminho e o uvicorn registra o caminho no log de acesso: mascarado aqui.
# Instalado na importação do app, que o uvicorn faz depois de configurar o próprio logging.
tokens.instalar_filtro_de_log()

LINK_INVALIDO = "link inválido ou expirado"


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


def obter_notificador():
    """Dependência substituível: o Telegram de produção, ou None se não estiver configurado."""
    return telegram.notificador_do_ambiente()


def _agora_utc():
    return datetime.now(timezone.utc)


def obter_relogio():
    """Dependência substituível: devolve a função que dá o instante atual em UTC."""
    return _agora_utc


def obter_config_ranking():
    """Dependência substituível: a configuração do ranking, lida e validada a cada resumo."""
    return config_ranking.carregar_config()


_DATA = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


def _semana_pedida(texto: str) -> datetime:
    """?semana=AAAA-MM-DD, que precisa ser uma segunda-feira (data local de São Paulo)."""
    if not _DATA.fullmatch(texto):
        raise HTTPException(status_code=422, detail="semana deve ser uma data AAAA-MM-DD")
    try:
        dia = date.fromisoformat(texto)
    except ValueError:
        raise HTTPException(status_code=422, detail="semana deve ser uma data AAAA-MM-DD") from None
    if dia.weekday() != 0:
        raise HTTPException(status_code=422, detail="semana deve ser uma segunda-feira (AAAA-MM-DD)")
    inicio = datetime.combine(dia, time(0), semana.FUSO)
    try:  # a semana anterior e o fim precisam caber no calendário
        semana.intervalo_utc(inicio - timedelta(days=7))
        semana.intervalo_utc(inicio)
    except OverflowError:
        raise HTTPException(status_code=422, detail="semana fora do intervalo aceito") from None
    return inicio


def _url_base() -> str:
    """RADAR_URL_BASE: http ou https, com host, sem barra final, sem query nem fragmento."""
    valor = os.environ.get("RADAR_URL_BASE", "")
    partes = urlsplit(valor)
    if (
        partes.scheme not in ("http", "https")
        or not partes.netloc
        or valor.endswith("/")
        or "?" in valor
        or "#" in valor
        or any(c.isspace() for c in valor)
    ):
        raise ErroDeConfiguracao("RADAR_URL_BASE ausente ou inválida")
    return valor


def _ranking_da_semana(conn, inicio: datetime, config):
    """(resultado do ranking, intervalo UTC) da semana; a semana anterior vazia vai como None.

    Comum ao resumo semanal e aos cards do painel: os dois calculam o ranking do mesmo jeito.
    """
    intervalo = semana.intervalo_utc(inicio)
    inicio_anterior = datetime.combine(inicio.date() - timedelta(days=7), time(0), semana.FUSO)
    atual = consultas.contar_semana(conn, *intervalo)
    anterior = consultas.contar_semana(conn, *semana.intervalo_utc(inicio_anterior))
    return ranking.calcular_ranking(atual, anterior if anterior.total > 0 else None, config), intervalo


def _cards_do_painel(semana_param, relogio, config) -> dict:
    """GET /api/painel/{token}/cards. Só lê o banco."""
    inicio = _semana_pedida(semana_param) if semana_param is not None else semana.ultima_semana_completa(relogio())
    caminho = _caminho_do_banco_existente()
    with closing(banco.abrir(caminho)) as conn:
        conn.execute("BEGIN")
        try:
            resultado, intervalo = _ranking_da_semana(conn, inicio, config)
            cards = []
            for c in resultado.cards:
                card = resumo.card_json(c)
                card["evidencias"] = [
                    {"avaliacao_id": e["avaliacao_id"], "posicao": posicao, "trecho": trechos.trecho(e["texto"]),
                     "nota": e["nota"], "critica": e["critica"]}
                    for posicao, e in enumerate(consultas.listar_evidencias(conn, *intervalo, c.categoria), start=1)
                ]
                card["acao"] = consultas.acao_da_semana(conn, c.categoria, intervalo[0])
                cards.append(card)
            ultima = consultas.ultima_leitura(conn)
        finally:
            conn.execute("COMMIT")  # só leitura
    cobertura = resumo.cobertura(resultado)
    return {
        "periodo": resumo.periodo(inicio),
        "cards": cards,
        "outros_temas": [{"categoria": c.categoria, "mencoes": c.mencoes} for c in resultado.outros_temas],
        "temas_sem_acao": cobertura["temas_sem_acao"],
        "cobertura": cobertura,
        "ultima_leitura": ultima,
    }


# ---------- ações do painel (bloco 4.2) ----------

LIMITE_NOTA = 500
MAIOR_ID = 2 ** 63 - 1  # maior inteiro do SQLite: acima disso o sqlite3 levantaria OverflowError
_ID_ASCII = re.compile(r"-?[0-9]+", re.ASCII)  # inteiro em dígitos ASCII; negativo existe como inteiro (404)


class Conflito(Exception):
    """409 com o corpo no topo (id e estado da ação existente, ou o estado atual na transição inválida)."""

    def __init__(self, conteudo: dict):
        super().__init__("conflito")
        self.conteudo = conteudo


class EvidenciaDaAcao(BaseModel):
    """Só avaliacao_id e posicao: o trecho é derivado pelo servidor; qualquer outro campo é recusado."""

    model_config = ConfigDict(extra="forbid")
    avaliacao_id: StrictInt = Field(ge=1, le=MAIOR_ID)
    posicao: StrictInt = Field(ge=1, le=3)


def _nota_vazia_vira_nulo(nota):
    """Nota vazia ou só com espaços vira None; nota com texto fica exatamente como chegou (sem strip)."""
    return None if nota is not None and not nota.strip() else nota


class NovaAcao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categoria: StrictStr
    semana_inicio: StrictStr
    evidencias: list[EvidenciaDaAcao] = Field(min_length=1, max_length=3)
    nota: Optional[StrictStr] = Field(None, max_length=LIMITE_NOTA)

    _nota = field_validator("nota")(_nota_vazia_vira_nulo)

    @model_validator(mode="after")
    def _distintas(self):
        if len({e.posicao for e in self.evidencias}) != len(self.evidencias):
            raise ValueError("as posições das evidências devem ser distintas")
        if len({e.avaliacao_id for e in self.evidencias}) != len(self.evidencias):
            raise ValueError("as avaliações das evidências devem ser distintas")
        return self


class MudancaDaAcao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    estado: Optional[Literal["pendente", "em_andamento", "concluida"]] = None
    nota: Optional[StrictStr] = Field(None, max_length=LIMITE_NOTA)

    _nota = field_validator("nota")(_nota_vazia_vira_nulo)

    @model_validator(mode="after")
    def _pelo_menos_um(self):
        if not self.model_fields_set:
            raise ValueError("informe estado e/ou nota")
        if "estado" in self.model_fields_set and self.estado is None:
            raise ValueError("estado não pode ser nulo")
        return self


async def _corpo_validado(request: Request, modelo):
    """Lê e valida o corpo DEPOIS do token (o FastAPI validaria antes das dependências). Só mensagens curtas."""
    try:
        dados = await request.json()
    except ValueError:
        raise HTTPException(status_code=422, detail="o corpo deve ser um JSON válido") from None
    try:
        return modelo.model_validate(dados)
    except ValidationError as e:
        # Só o local e o tipo de cada erro: nunca o valor enviado (pode ser a nota ou texto do cliente).
        raise HTTPException(
            status_code=422, detail=[{"campo": ".".join(map(str, x["loc"])), "erro": x["type"]} for x in e.errors()]
        ) from None


def _acao_json(acao: dict) -> dict:
    return {
        "id": acao["id"],
        "categoria": acao["categoria"],
        "semana_inicio": _em_sao_paulo(acao["semana_inicio"]),
        "estado": acao["estado"],
        "criada_em": _em_sao_paulo(acao["criada_em"]),
        "concluida_em": _em_sao_paulo(acao["concluida_em"]),
        "nota": acao["nota"],
        "evidencias": acao["evidencias"],
    }


def _registrar_acao(nova: NovaAcao, relogio, config) -> dict:
    agora = relogio()
    if nova.categoria not in config.pesos:  # entregador e categorias desconhecidas não participam do ranking
        raise HTTPException(status_code=422, detail="categoria não participa do ranking")
    inicio = _semana_pedida(nova.semana_inicio)
    if inicio > semana.inicio_da_semana(agora):
        raise HTTPException(status_code=422, detail="semana_inicio não pode ser posterior à semana atual")
    caminho = _caminho_do_banco_existente()
    inicio_utc, fim_utc = semana.intervalo_utc(inicio)
    with closing(banco.abrir(caminho)) as conn:
        evidencias = []
        for e in sorted(nova.evidencias, key=lambda x: x.posicao):
            linha = conn.execute(
                "SELECT texto, recebida_em, categorias FROM avaliacoes WHERE id = ?", (e.avaliacao_id,)
            ).fetchone()
            if linha is None:
                raise HTTPException(status_code=422, detail="evidência: avaliação inexistente")
            recebida = datetime.fromisoformat(linha["recebida_em"])
            if not (inicio_utc <= recebida < fim_utc):
                raise HTTPException(status_code=422, detail="evidência: avaliação de outra semana")
            if nova.categoria not in json.loads(linha["categorias"]):
                raise HTTPException(status_code=422, detail="evidência: avaliação de outra categoria")
            evidencias.append((e.avaliacao_id, e.posicao, trechos.trecho(linha["texto"])))  # derivado aqui
        try:
            acao_id = banco.registrar_acao(
                conn, categoria=nova.categoria, semana_inicio=inicio_utc.isoformat(),
                criada_em=agora.isoformat(), nota=nova.nota, evidencias=evidencias,
            )
        except banco.AcaoDuplicada as dup:
            raise Conflito({
                "detail": "já existe uma ação para esta categoria e semana", "id": dup.acao_id, "estado": dup.estado,
            }) from None
        return _acao_json(banco.buscar_acao(conn, acao_id))


def _mudar_acao(acao_id_texto: str, mudanca: MudancaDaAcao, relogio) -> dict:
    # Só dígitos ASCII: int() aceitaria " 7", "+7", "1_0" e dígitos de outras escritas.
    if not _ID_ASCII.fullmatch(acao_id_texto):
        raise HTTPException(status_code=422, detail="o id da ação deve ser um inteiro")
    acao_id = int(acao_id_texto)
    if acao_id > MAIOR_ID:  # não cabe no banco: não existe
        raise HTTPException(status_code=404, detail="ação não encontrada")
    campos = {k: getattr(mudanca, k) for k in mudanca.model_fields_set}
    with closing(banco.abrir(_caminho_do_banco_existente())) as conn:
        try:
            banco.mudar_acao(conn, acao_id, agora=relogio().isoformat(), **campos)
        except banco.AcaoInexistente:
            raise HTTPException(status_code=404, detail="ação não encontrada") from None
        except banco.TransicaoInvalida as t:
            raise Conflito({"detail": "transição inválida", "estado": t.estado}) from None
        return _acao_json(banco.buscar_acao(conn, acao_id))


def _caminho_do_painel(request) -> bool:
    return request.url.path.startswith(("/api/painel/", "/painel/"))


# ---------- página do painel (bloco 4.3) ----------

PASTA_PAINEL = Path(__file__).resolve().parent / "painel"
# Só estes dois arquivos são servidos como estáticos; os HTML saem pela rota com token.
ESTATICOS_DO_PAINEL = {"painel.js": "text/javascript; charset=utf-8", "painel.css": "text/css; charset=utf-8"}
CSP_DO_PAINEL = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _pagina_do_painel(arquivo: str, status: int) -> Response:
    """HTML estático do painel, sem nenhum dado: o JS busca tudo na API e escreve com textContent."""
    return Response(
        content=(PASTA_PAINEL / arquivo).read_bytes(), status_code=status, media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store", "Content-Security-Policy": CSP_DO_PAINEL,
                 "X-Content-Type-Options": "nosniff"},
    )


def _em_sao_paulo(texto):
    """ISO gravado em UTC -> ISO com fuso America/Sao_Paulo (None continua None)."""
    return None if texto is None else datetime.fromisoformat(texto).astimezone(semana.FUSO).isoformat()


def _historico_do_painel() -> dict:
    """GET /api/painel/{token}/historico. Só lê o banco; o trecho exibido é o gravado."""
    with closing(banco.abrir(_caminho_do_banco_existente())) as conn:
        conn.execute("BEGIN")  # as consultas do histórico veem o mesmo estado do banco
        try:
            semanas = consultas.listar_historico(conn)
        finally:
            conn.execute("COMMIT")  # só leitura
    for s in semanas:
        inicio_local = datetime.fromisoformat(s["semana_inicio"]).astimezone(semana.FUSO)
        s["semana_inicio"] = inicio_local.isoformat()
        s["rotulo"] = resumo.periodo(inicio_local)["rotulo"]
    for s in semanas:
        for a in s["acoes"]:  # o banco continua em UTC; a resposta sai no fuso de São Paulo
            a["criada_em"] = _em_sao_paulo(a["criada_em"])
            a["concluida_em"] = _em_sao_paulo(a["concluida_em"])
    return {"semanas": [{"semana_inicio": s["semana_inicio"], "rotulo": s["rotulo"], "acoes": s["acoes"]}
                        for s in semanas]}


def _resumo_semanal(semana_param, relogio, config) -> dict:
    agora = relogio()  # lido uma vez: semana padrão e criado_em do token usam o mesmo instante
    inicio = _semana_pedida(semana_param) if semana_param is not None else semana.ultima_semana_completa(agora)
    url_base = _url_base()  # antes de qualquer gravação: sem URL válida, nenhum token é criado
    caminho = os.environ.get("RADAR_DB")
    if not caminho:
        raise ErroDeConfiguracao("RADAR_DB não definida")
    if not os.path.isfile(caminho):  # banco.abrir criaria um banco vazio e o resumo sairia "0 lidas"
        raise ErroDeConfiguracao("RADAR_DB aponta para um arquivo que não existe")
    with closing(banco.abrir(caminho)) as conn:
        # As leituras veem o mesmo estado do banco (um POST no meio não divide a contagem).
        conn.execute("BEGIN")
        try:
            resultado, intervalo = _ranking_da_semana(conn, inicio, config)
            criticos = consultas.listar_criticos(conn, *intervalo)
            invalidas = consultas.listar_invalidas(conn, *intervalo)
            falhos = consultas.listar_alertas_falhos(conn)  # de qualquer semana: ?semana= não filtra
            acoes = consultas.listar_acoes_pendentes(conn)
        finally:
            conn.execute("COMMIT")  # só leitura: nada a desfazer; libera antes do BEGIN IMMEDIATE do token
        enviar = resumo.deve_enviar(resultado, criticos, acoes, config, invalidas, falhos)
        curto = resumo.aviso_curto(resultado, criticos, acoes, config, invalidas, falhos)

        def montar(link):
            return resumo.montar(inicio, resultado, criticos, acoes, amostra_minima=config.amostra_minima,
                                 enviar=enviar, link_painel=link, invalidas=invalidas,
                                 alertas_falhos=falhos, curto=curto)

        corpo = montar(None)  # tudo calculado e montado antes do token: uma falha aqui não deixa token
        if not enviar or curto:  # aviso curto: sem link e sem token
            return corpo
        token = tokens.criar_token_painel(conn, agora=agora)  # a única escrita da rota
    return montar(f"{url_base}/painel/{token}")


def exigir_token(token: str, relogio=Depends(obter_relogio)) -> str:
    """Dependência para rotas com {token} no caminho.

    Inexistente, expirado, revogado ou malformado: todos dão o mesmo 404, sem revelar o motivo.
    """
    if not tokens.formato_valido(token):
        raise HTTPException(status_code=404, detail=LINK_INVALIDO)
    try:
        # O banco precisa existir: banco.abrir criaria um vazio, e o link responderia 404 em vez do 500.
        with closing(banco.abrir(_caminho_do_banco_existente())) as conn:
            valido = tokens.validar_token(conn, token, relogio(), escopo="painel")
    except Exception as e:  # noqa: BLE001 - configuração ou banco: 500 genérico, só o tipo no log
        logger.error("falha ao validar o link do painel (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="falha interna") from None
    if not valido:
        raise HTTPException(status_code=404, detail=LINK_INVALIDO)
    return token


def _processar(nova: NovaAvaliacao, resposta: Response, classificar, entrada, agendar_alerta):
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
            alerta_criado_em=entrada.isoformat(),  # instante de entrada: base do critério 5
        )

    if not gravado.criada:  # outra requisição gravou o mesmo id_externo enquanto esta classificava
        resposta.status_code = 200
        return {"avaliacao_id": gravado.avaliacao_id, "criada": False}
    if gravado.alerta_id is not None:
        mensagem = alertas.montar_mensagem(gravado.avaliacao_id, criticidade, nova.texto)
        agendar_alerta(caminho, gravado.alerta_id, mensagem)
    return {"avaliacao_id": gravado.avaliacao_id, "criada": True, "alerta_id": gravado.alerta_id}


def _caminho_do_banco_existente() -> str:
    """RADAR_DB definida e apontando para um arquivo que existe (banco.abrir criaria um banco vazio)."""
    caminho = os.environ.get("RADAR_DB")
    if not caminho:
        raise ErroDeConfiguracao("RADAR_DB não definida")
    if not os.path.isfile(caminho):
        raise ErroDeConfiguracao("RADAR_DB aponta para um arquivo que não existe")
    return caminho


def _recuperar_na_subida_padrao() -> None:
    """Recuperação de alertas da subida do servidor de produção. Nunca levanta exceção (só registra)."""
    try:
        caminho = _caminho_do_banco_existente()
    except ErroDeConfiguracao as e:
        logger.warning("recuperação de alertas na subida não rodou: %s", e)
        return
    alertas.recuperar_na_subida(caminho, telegram.notificador_do_ambiente(), _agora_utc)


def _rodar_recuperacao(funcao) -> None:
    try:
        funcao()
    except Exception as e:  # noqa: BLE001 - a recuperação não pode derrubar a subida
        logger.error("falha na recuperação de alertas na subida do servidor (%s)", type(e).__name__)


def criar_app(*, recuperar_na_subida=None) -> FastAPI:
    """Monta o app. `recuperar_na_subida` (função sem argumentos) roda em segundo plano na subida.

    Sem ela, nada é reenviado na subida: os testes criam o app assim. O `app` do módulo, que o uvicorn
    serve, liga a recuperação de produção.
    """

    @asynccontextmanager
    async def ciclo_de_vida(app_):
        if recuperar_na_subida is not None:
            # Em thread própria: a subida não espera a recuperação nem cai se ela falhar.
            threading.Thread(
                target=_rodar_recuperacao, args=(recuperar_na_subida,), name="recuperacao-alertas", daemon=True
            ).start()
        yield

    # Sem /docs, /redoc e /openapi.json: a documentação automática não precisa ficar aberta sem chave.
    app = FastAPI(
        title="Radar de Avaliações", docs_url=None, redoc_url=None, openapi_url=None, lifespan=ciclo_de_vida
    )
    app.state.recuperar_na_subida = recuperar_na_subida
    com_chave = APIRouter(route_class=RotaComChave)

    @app.middleware("http")
    async def sem_referer(request: Request, chamar_proxima):
        # O token do painel vai na URL: sem Referer, ele não vaza para links externos.
        resposta = await chamar_proxima(request)
        resposta.headers["Referrer-Policy"] = "no-referrer"
        if _caminho_do_painel(request):  # 200, 404, 422 e 500 das rotas do painel: nada de cache
            resposta.headers["Cache-Control"] = "no-store"
        return resposta

    @app.exception_handler(Exception)
    async def falha_inesperada(request: Request, erro: Exception):
        # Roda no ServerErrorMiddleware, por fora do middleware acima: o cabeçalho vai aqui também.
        logger.error("falha inesperada (%s)", type(erro).__name__)
        cabecalhos = {"Referrer-Policy": "no-referrer"}
        if _caminho_do_painel(request):
            cabecalhos["Cache-Control"] = "no-store"
        return JSONResponse(status_code=500, content={"detail": "falha interna"}, headers=cabecalhos)

    @app.exception_handler(Conflito)
    async def conflito(request: Request, erro: Conflito):
        return JSONResponse(status_code=409, content=erro.conteudo)

    @app.exception_handler(ErroDeConfiguracao)
    async def configuracao_ausente(request: Request, erro: ErroDeConfiguracao):
        # A mensagem diz qual variável falta ou está inválida, nunca o valor dela.
        logger.error("configuração do servidor: %s", erro)
        return JSONResponse(
            status_code=500, content={"detail": "falha interna"}, headers={"Referrer-Policy": "no-referrer"}
        )

    @app.exception_handler(config_ranking.ErroNaConfiguracao)
    async def configuracao_invalida(request: Request, erro: config_ranking.ErroNaConfiguracao):
        # A mensagem da validação é curta e só fala da configuração: pode ir para o log.
        logger.error("configuração do ranking inválida: %s", erro)
        return JSONResponse(
            status_code=500, content={"detail": "falha interna"}, headers={"Referrer-Policy": "no-referrer"}
        )

    @com_chave.get("/resumo-semanal")
    def resumo_semanal(
        resposta: Response,
        semana_param: Optional[str] = Query(None, alias="semana"),
        relogio=Depends(obter_relogio),
        config=Depends(obter_config_ranking),
    ):
        # Cria um token novo a cada chamada com algo a enviar: a resposta não pode ficar em cache.
        resposta.headers["Cache-Control"] = "no-store"
        return _resumo_semanal(semana_param, relogio, config)

    # Painel (bloco 4.1): o token do caminho é conferido primeiro (exigir_token, declarado antes das outras
    # dependências), então link inválido dá o 404 uniforme antes de qualquer validação de ?semana=.
    @app.get("/api/painel/{token}/cards")
    def painel_cards(
        resposta: Response,
        token: str = Depends(exigir_token),
        semana_param: Optional[str] = Query(None, alias="semana"),
        relogio=Depends(obter_relogio),
        config=Depends(obter_config_ranking),
    ):
        resposta.headers["Cache-Control"] = "no-store"
        return _cards_do_painel(semana_param, relogio, config)

    # Página do painel (bloco 4.3): o mesmo token das rotas da API. Link inválido, por qualquer motivo, dá a
    # mesma página estática de "link inválido ou expirado" com 404; falha de banco continua 500 genérico.
    @app.get("/painel/{token}")
    def painel_pagina(token: str, relogio=Depends(obter_relogio)):
        try:
            exigir_token(token, relogio)
        except HTTPException as e:
            if e.status_code != 404:
                raise
            return _pagina_do_painel("link-invalido.html", 404)
        return _pagina_do_painel("index.html", 200)

    @app.get("/painel/{resto:path}")
    def painel_caminho_invalido(resto: str):
        # /painel/ vazio ou com mais de um trecho: nunca é um link válido; a mesma página do 404 uniforme.
        return _pagina_do_painel("link-invalido.html", 404)

    @app.get("/estatico/painel/{arquivo}")
    def painel_estatico(arquivo: str):
        if arquivo not in ESTATICOS_DO_PAINEL:  # lista fechada: nada de caminho vindo da URL
            raise HTTPException(status_code=404, detail="não encontrado")
        return Response(
            content=(PASTA_PAINEL / arquivo).read_bytes(), media_type=ESTATICOS_DO_PAINEL[arquivo],
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get("/api/painel/{token}/historico")
    def painel_historico(resposta: Response, token: str = Depends(exigir_token)):
        resposta.headers["Cache-Control"] = "no-store"
        return _historico_do_painel()

    # Ações do painel (bloco 4.2): o corpo é lido no handler, DEPOIS de exigir_token, para que um link
    # inválido dê o 404 uniforme mesmo com corpo inválido (o FastAPI validaria o corpo antes).
    @app.post("/api/painel/{token}/acoes", status_code=201)
    async def painel_registrar_acao(
        request: Request,
        token: str = Depends(exigir_token),
        relogio=Depends(obter_relogio),
        config=Depends(obter_config_ranking),
    ):
        nova = await _corpo_validado(request, NovaAcao)
        return await run_in_threadpool(_registrar_acao, nova, relogio, config)

    @app.patch("/api/painel/{token}/acoes/{acao_id}")
    async def painel_mudar_acao(request: Request, acao_id: str, token: str = Depends(exigir_token),
                                relogio=Depends(obter_relogio)):
        mudanca = await _corpo_validado(request, MudancaDaAcao)
        return await run_in_threadpool(_mudar_acao, acao_id, mudanca, relogio)

    @com_chave.post("/alertas/reenviar")
    def reenviar_alertas(notificador=Depends(obter_notificador), relogio=Depends(obter_relogio)):
        # Varre e tenta uma vez cada alerta pendente ou falhou elegível; ver app/alertas.py.
        return alertas.reenviar_elegiveis(
            _caminho_do_banco_existente(), notificador, relogio, estados=alertas.ESTADOS_REENVIO
        )

    @com_chave.post("/avaliacoes", status_code=201)
    def receber_avaliacao(
        nova: NovaAvaliacao,
        resposta: Response,
        tarefas: BackgroundTasks,
        classificar=Depends(obter_classificador),
        notificador=Depends(obter_notificador),
        relogio=Depends(obter_relogio),
    ):
        entrada = relogio()  # lido uma vez, no início: é o criado_em do alerta

        def agendar_alerta(caminho, alerta_id, mensagem):
            # Roda depois da resposta: o código HTTP e o corpo não dependem do Telegram.
            tarefas.add_task(alertas.enviar_alerta, caminho, alerta_id, mensagem, notificador, relogio)

        # Nenhuma resposta nem log leva mensagem de exceção, corpo de erro da API ou chave:
        # só o tipo e, para erros da API do classificador, o status e o request_id.
        try:
            return _processar(nova, resposta, classificar, entrada, agendar_alerta)
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


app = criar_app(recuperar_na_subida=_recuperar_na_subida_padrao)
