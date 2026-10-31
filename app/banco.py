"""Camada de persistência SQLite (bloco 2.1 da Fase 2).

Fonte da verdade: docs/ESPECIFICACAO.md, seção "Contratos e dados". Só a biblioteca padrão.
Listas (categorias, criticidade, tipos) são guardadas como JSON em texto.

Datas: toda data que entra precisa ter fuso e é gravada normalizada em UTC, em ISO 8601 com
"+00:00" (aceita "Z" e outros deslocamentos na entrada). Entrada sem fuso levanta ValueError.
"""

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

from app.contrato import CATEGORIAS, CRITICIDADES

PRAZO_RESPOSTA_DIAS = 5
VALIDADE_TOKEN_DIAS = 7
ESTADOS_ALERTA = ("pendente", "enviado", "falhou")
STATUS_CLASSIFICACAO = ("ok", "invalida")

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS avaliacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_externo TEXT NOT NULL UNIQUE,
    texto TEXT NOT NULL,
    nota INTEGER NOT NULL,
    recebida_em TEXT NOT NULL,
    prazo_resposta TEXT NOT NULL,
    categorias TEXT NOT NULL,
    criticidade TEXT NOT NULL,
    status_classificacao TEXT NOT NULL CHECK (status_classificacao IN ('ok', 'invalida'))
);

CREATE TABLE IF NOT EXISTS alertas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    avaliacao_id INTEGER NOT NULL UNIQUE REFERENCES avaliacoes (id) ON DELETE RESTRICT,
    tipos TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendente' CHECK (estado IN ('pendente', 'enviado', 'falhou')),
    tentativas INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL,
    enviado_em TEXT,
    tardio INTEGER NOT NULL DEFAULT 0 CHECK (tardio IN (0, 1)),
    reconhecido_em TEXT
);

CREATE TABLE IF NOT EXISTS acoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    categoria TEXT NOT NULL,
    semana_inicio TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendente'
        CHECK (estado IN ('pendente', 'em_andamento', 'concluida')),
    criada_em TEXT NOT NULL,
    concluida_em TEXT,
    nota TEXT
);

CREATE TABLE IF NOT EXISTS acao_avaliacoes (
    acao_id INTEGER NOT NULL REFERENCES acoes (id) ON DELETE RESTRICT,
    avaliacao_id INTEGER NOT NULL REFERENCES avaliacoes (id) ON DELETE RESTRICT,
    trecho_exibido TEXT NOT NULL,
    posicao INTEGER NOT NULL CHECK (posicao BETWEEN 1 AND 3),
    PRIMARY KEY (acao_id, avaliacao_id),
    UNIQUE (acao_id, posicao)
);

CREATE TABLE IF NOT EXISTS tokens_painel (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,
    escopo TEXT NOT NULL,
    criado_em TEXT NOT NULL,
    expira_em TEXT NOT NULL,
    revogado_em TEXT
);
"""


class Resultado(NamedTuple):
    avaliacao_id: int
    criada: bool
    alerta_id: Optional[int]


def abrir(caminho) -> sqlite3.Connection:
    """Abre a conexão com foreign_keys ligado e garante o esquema.

    O SQLite só aplica chaves estrangeiras se o PRAGMA for ligado em cada conexão, por isso
    toda conexão da camada nasce aqui. isolation_level=None desliga a transação implícita do
    módulo sqlite3: as transações são abertas de forma explícita por _transacao.
    """
    conn = sqlite3.connect(caminho, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_ESQUEMA)
    _migrar(conn)
    return conn


# Colunas acrescentadas depois da primeira versão do esquema (bloco 2.8: recuperação de alertas).
# Um banco antigo as recebe ao ser aberto; o esquema novo já nasce com elas.
_COLUNAS_NOVAS_ALERTAS = (
    ("tardio", "INTEGER NOT NULL DEFAULT 0 CHECK (tardio IN (0, 1))"),
    ("reconhecido_em", "TEXT"),
)


def _colunas_alertas(conn) -> set:
    return {linha[1] for linha in conn.execute("PRAGMA table_info(alertas)")}


def _migrar(conn) -> None:
    """ALTER TABLE ADD COLUMN para o que faltar, numa transação. Idempotente e sem perder dados.

    A conferência fora da transação evita um BEGIN IMMEDIATE a cada abertura; a de dentro cobre
    duas conexões migrando ao mesmo tempo.
    """
    if all(nome in _colunas_alertas(conn) for nome, _ in _COLUNAS_NOVAS_ALERTAS):
        return
    with _transacao(conn):
        existentes = _colunas_alertas(conn)
        for nome, definicao in _COLUNAS_NOVAS_ALERTAS:
            if nome not in existentes:
                conn.execute(f"ALTER TABLE alertas ADD COLUMN {nome} {definicao}")


@contextmanager
def _transacao(conn):
    """BEGIN IMMEDIATE ... COMMIT, ou ROLLBACK se algo falhar, inclusive o próprio COMMIT.

    O erro original nunca é escondido: se o ROLLBACK também falhar (por exemplo, porque o
    SQLite já desfez a transação), esse segundo erro é descartado e o primeiro é propagado.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def _exigir_texto(valor, nome: str) -> str:
    if not isinstance(valor, str):
        raise ValueError(f"{nome} deve ser texto, recebido {type(valor).__name__}")
    return valor


def _utc(valor, nome: str) -> datetime:
    """Lê um instante ISO 8601 com fuso e o devolve em UTC."""
    _exigir_texto(valor, nome)
    try:
        instante = datetime.fromisoformat(valor)
    except ValueError:
        raise ValueError(f"{nome} não é uma data ISO 8601 legível: {valor!r}") from None
    if instante.utcoffset() is None:
        raise ValueError(f"{nome} precisa ter fuso (por exemplo -03:00 ou Z): {valor!r}")
    return instante.astimezone(timezone.utc)


def _exigir_inteiro(valor, nome: str) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ValueError(f"{nome} deve ser inteiro, recebido {type(valor).__name__}")
    return valor


def _validar_lista(valor, permitidos, nome: str) -> list:
    if not isinstance(valor, (list, tuple)):
        raise ValueError(f"{nome} deve ser uma lista, recebido {type(valor).__name__}")
    itens = list(valor)
    for item in itens:
        if not isinstance(item, str) or item not in permitidos:
            raise ValueError(f"{nome} contém valor fora do conjunto: {item!r}")
    if len(set(itens)) != len(itens):
        raise ValueError(f"{nome} contém valor repetido: {itens!r}")
    return itens


def _id_por_externo(conn, id_externo: str) -> Optional[int]:
    linha = conn.execute("SELECT id FROM avaliacoes WHERE id_externo = ?", (id_externo,)).fetchone()
    return None if linha is None else linha["id"]


def _inserir_alerta(conn, avaliacao_id: int, tipos: list, criado_em: str) -> int:
    cur = conn.execute(
        "INSERT INTO alertas (avaliacao_id, tipos, estado, tentativas, criado_em) "
        "VALUES (?, ?, 'pendente', 0, ?)",
        (avaliacao_id, json.dumps(tipos, ensure_ascii=False), criado_em),
    )
    return cur.lastrowid


def gravar_avaliacao(
    conn,
    *,
    id_externo: str,
    texto: str,
    nota: int,
    recebida_em: str,
    categorias: list,
    criticidade: list,
    status_classificacao: str,
    alerta_criado_em: Optional[str] = None,
) -> Resultado:
    """Grava a avaliação e, se criticidade não for vazia, um único alerta pendente.

    O alerta é derivado de criticidade (tipos = criticidade) e gravado na mesma transação da
    avaliação: se uma gravação falhar, nenhuma das duas fica. Duplicata (mesmo id_externo) não
    grava nada e devolve a avaliação existente com criada=False e sem alerta.
    """
    _exigir_texto(id_externo, "id_externo")
    _exigir_texto(texto, "texto")
    _exigir_inteiro(nota, "nota")
    recebida = _utc(recebida_em, "recebida_em")
    if status_classificacao not in STATUS_CLASSIFICACAO:
        raise ValueError(f"status_classificacao inválido: {status_classificacao!r}")
    categorias = _validar_lista(categorias, CATEGORIAS, "categorias")
    criticidade = _validar_lista(criticidade, CRITICIDADES, "criticidade")
    if status_classificacao == "invalida" and (categorias or criticidade):
        raise ValueError("avaliação invalida deve ter categorias e criticidade vazias")
    alerta_criado = (
        _utc(alerta_criado_em, "alerta_criado_em")
        if alerta_criado_em is not None
        else datetime.now(timezone.utc)
    )

    # A consulta por id_externo e o INSERT rodam sob o mesmo BEGIN IMMEDIATE. Uma segunda
    # conexão espera o lock e, quando entra, a consulta já enxerga a linha gravada: não há
    # corrida entre as duas, e qualquer IntegrityError aqui é um erro real, que se propaga.
    with _transacao(conn):
        existente = _id_por_externo(conn, id_externo)
        if existente is not None:
            return Resultado(existente, False, None)

        cur = conn.execute(
            "INSERT INTO avaliacoes (id_externo, texto, nota, recebida_em, prazo_resposta, "
            "categorias, criticidade, status_classificacao) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                id_externo,
                texto,
                nota,
                recebida.isoformat(),
                (recebida + timedelta(days=PRAZO_RESPOSTA_DIAS)).isoformat(),
                json.dumps(categorias, ensure_ascii=False),
                json.dumps(criticidade, ensure_ascii=False),
                status_classificacao,
            ),
        )
        avaliacao_id = cur.lastrowid
        alerta_id = None
        if criticidade:
            alerta_id = _inserir_alerta(conn, avaliacao_id, criticidade, alerta_criado.isoformat())
        return Resultado(avaliacao_id, True, alerta_id)


def buscar_avaliacao_por_id_externo(conn, id_externo: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM avaliacoes WHERE id_externo = ?", (id_externo,)
    ).fetchone()


def reprocessar_avaliacao(
    conn, avaliacao_id: int, *, categorias: list, criticidade: list, alerta_criado_em: str
) -> Optional[int]:
    """Bloco 2.9: uma avaliação `invalida` reclassificada com sucesso vira `ok`.

    Numa única transação: atualiza `status_classificacao`, `categorias` e `criticidade` e, se houver
    marcação crítica, cria o alerta `pendente` com `tardio` = 1 (fora do critério 5) e `criado_em` igual
    ao instante em que o script o grava. Se algo falhar no meio, nada fica. Devolve o alerta_id ou None.
    """
    categorias = _validar_lista(categorias, CATEGORIAS, "categorias")
    criticidade = _validar_lista(criticidade, CRITICIDADES, "criticidade")
    criado = _utc(alerta_criado_em, "alerta_criado_em").isoformat()
    with _transacao(conn):
        cur = conn.execute(
            "UPDATE avaliacoes SET status_classificacao = 'ok', categorias = ?, criticidade = ? "
            "WHERE id = ? AND status_classificacao = 'invalida'",
            (json.dumps(categorias, ensure_ascii=False), json.dumps(criticidade, ensure_ascii=False), avaliacao_id),
        )
        if cur.rowcount != 1:
            raise LookupError(f"avaliação invalida inexistente: {avaliacao_id}")
        if not criticidade:
            return None
        cur = conn.execute(
            "INSERT INTO alertas (avaliacao_id, tipos, estado, tentativas, criado_em, tardio) "
            "VALUES (?, ?, 'pendente', 0, ?, 1)",
            (avaliacao_id, json.dumps(criticidade, ensure_ascii=False), criado),
        )
        return cur.lastrowid


def buscar_alerta_por_avaliacao(conn, avaliacao_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM alertas WHERE avaliacao_id = ?", (avaliacao_id,)
    ).fetchone()


def mudar_estado_alerta(
    conn,
    alerta_id: int,
    estado: str,
    *,
    incrementar_tentativas: bool = False,
    enviado_em: Optional[str] = None,
) -> None:
    """Muda o estado do alerta. Não há regra de transição: a especificação não define nenhuma.

    A contagem de tentativas é incrementada no próprio UPDATE, sem ler e reescrever o valor.
    """
    if estado not in ESTADOS_ALERTA:
        raise ValueError(f"estado de alerta inválido: {estado!r}")
    enviado = None if enviado_em is None else _utc(enviado_em, "enviado_em").isoformat()
    with _transacao(conn):
        cur = conn.execute(
            "UPDATE alertas SET estado = ?, tentativas = tentativas + ?, "
            "enviado_em = COALESCE(?, enviado_em) WHERE id = ?",
            (estado, 1 if incrementar_tentativas else 0, enviado, alerta_id),
        )
        if cur.rowcount == 0:
            raise LookupError(f"alerta inexistente: {alerta_id}")


def registrar_token_painel(conn, token_hash: str, escopo: str, criado_em: str) -> int:
    """Guarda só o hash SHA-256 (hexadecimal minúsculo) do token; o token nunca chega aqui."""
    _exigir_texto(token_hash, "token_hash")
    if not _SHA256_HEX.fullmatch(token_hash):
        raise ValueError("token_hash deve ser um SHA-256 em hexadecimal minúsculo (64 caracteres)")
    _exigir_texto(escopo, "escopo")
    criado = _utc(criado_em, "criado_em")
    expira = criado + timedelta(days=VALIDADE_TOKEN_DIAS)
    with _transacao(conn):
        cur = conn.execute(
            "INSERT INTO tokens_painel (token_hash, escopo, criado_em, expira_em) "
            "VALUES (?, ?, ?, ?)",
            (token_hash, escopo, criado.isoformat(), expira.isoformat()),
        )
    return cur.lastrowid
