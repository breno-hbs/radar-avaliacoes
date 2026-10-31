"""Persistência após reinício, com o SERVIDOR REAL (uvicorn em subprocesso). Dados SIMULADOS.

Fonte da verdade: docs/ESPECIFICACAO.md (persistência; token do painel; duplicidade; recuperação de
alertas; migração das colunas `tardio` e `reconhecido_em`). Só testes: nenhum código de produção muda.

Cada teste sobe o servidor de verdade (`python -m uvicorn app.main:app`), derruba o processo (terminate/kill
do PRÓPRIO Popen, seguido de wait: simula uma queda) e sobe de novo, lendo o mesmo arquivo SQLite.

Segurança do ambiente do subprocesso:
- O app carrega o `.env` do projeto (python-dotenv, sem sobrescrever o que já existe no ambiente). Para um
  token real do `.env` nunca ligar o Telegram aqui, RADAR_TELEGRAM_TOKEN, RADAR_TELEGRAM_CHAT_ID e
  ANTHROPIC_API_KEY são definidas COMO VAZIAS (e não apenas removidas): o dotenv não as sobrescreve.
- Nenhuma chamada de rede externa: o classificador nunca é chamado (a duplicata é decidida antes) e o
  Telegram fica desligado (o log do subprocesso prova isso no cenário 4).
- A chave e a URL abaixo são fictícias.
"""

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import banco, tokens

RAIZ = Path(__file__).resolve().parent.parent
CHAVE = "chave-ficticia-do-reinicio"
SEMANA = "2026-10-26"  # segunda-feira; o resumo usa ?semana= para não depender do relógio
SEGUNDA = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)  # 12:00 em São Paulo, dentro da semana
LIMITE_DE_SUBIDA = 30  # segundos
SEM_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # localhost nunca por proxy

# DDL das tabelas ANTES do bloco 2.8 (sem tardio nem reconhecido_em), copiado de app/banco.py.
DDL_ANTIGO = """
CREATE TABLE avaliacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, id_externo TEXT NOT NULL UNIQUE, texto TEXT NOT NULL,
    nota INTEGER NOT NULL, recebida_em TEXT NOT NULL, prazo_resposta TEXT NOT NULL, categorias TEXT NOT NULL,
    criticidade TEXT NOT NULL,
    status_classificacao TEXT NOT NULL CHECK (status_classificacao IN ('ok', 'invalida'))
);
CREATE TABLE alertas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    avaliacao_id INTEGER NOT NULL UNIQUE REFERENCES avaliacoes (id) ON DELETE RESTRICT,
    tipos TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendente' CHECK (estado IN ('pendente', 'enviado', 'falhou')),
    tentativas INTEGER NOT NULL DEFAULT 0, criado_em TEXT NOT NULL, enviado_em TEXT
);
"""


# ---------- infraestrutura do servidor real ----------

def _porta_livre() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ambiente(caminho_db, porta) -> dict:
    ambiente = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("RADAR_", "ANTHROPIC_")) and "TELEGRAM" not in k.upper()
    }
    ambiente.update(
        RADAR_DB=str(caminho_db),
        RADAR_API_KEY=CHAVE,
        RADAR_URL_BASE=f"http://localhost:{porta}",
        # vazias, não removidas: o dotenv do app não sobrescreve variável existente (ver o docstring)
        RADAR_TELEGRAM_TOKEN="",
        RADAR_TELEGRAM_CHAT_ID="",
        ANTHROPIC_API_KEY="",
        PYTHONIOENCODING="utf-8",
    )
    return ambiente


def _pedir(base, metodo, caminho, corpo=None, chave=CHAVE):
    """(status, json). Fecha toda resposta, inclusive as de erro (o HTTPError guarda o socket)."""
    dados = None if corpo is None else json.dumps(corpo).encode("utf-8")
    cabecalhos = {"X-API-Key": chave}
    if dados is not None:
        cabecalhos["Content-Type"] = "application/json"
    pedido = urllib.request.Request(base + caminho, data=dados, method=metodo, headers=cabecalhos)
    try:
        with SEM_PROXY.open(pedido, timeout=20) as resposta:
            return resposta.status, json.loads(resposta.read())
    except urllib.error.HTTPError as erro:
        try:
            return erro.code, json.loads(erro.read())
        finally:
            erro.close()


class Servidor:
    def __init__(self, base, processo, log):
        self.base, self.processo, self.log = base, processo, log

    def pedir(self, metodo, caminho, corpo=None, chave=CHAVE):
        return _pedir(self.base, metodo, caminho, corpo, chave)

    def texto_do_log(self) -> str:
        return Path(self.log).read_text(encoding="utf-8", errors="replace")


def _esperar_no_ar(base, processo, log):
    fim = time.monotonic() + LIMITE_DE_SUBIDA
    while time.monotonic() < fim:
        if processo.poll() is not None:
            pytest.fail(f"o uvicorn encerrou na subida (código {processo.returncode})")
        try:
            _pedir(base, "GET", "/pronto")  # qualquer resposta HTTP (aqui, 404) = no ar
            return
        except OSError:
            time.sleep(0.1)
    pytest.fail("o uvicorn não subiu a tempo")


@contextmanager
def servidor(tmp_path, caminho_db, nome_do_log):
    """Sobe o uvicorn real e, na saída, o derruba (terminate/kill do próprio Popen + wait)."""
    porta = _porta_livre()
    log = tmp_path / nome_do_log
    arquivo = open(log, "wb")
    processo = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(porta)],
        cwd=RAIZ, env=_ambiente(caminho_db, porta), stdout=arquivo, stderr=subprocess.STDOUT,
    )
    try:
        base = f"http://127.0.0.1:{porta}"
        _esperar_no_ar(base, processo, log)
        yield Servidor(base, processo, log)
    finally:
        try:
            if processo.poll() is None:
                processo.terminate()  # no Windows equivale a kill: simula a queda do processo
                try:
                    processo.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    processo.kill()
                    processo.wait(timeout=10)
        finally:
            arquivo.close()
        assert processo.poll() is not None, "o processo do servidor não foi encerrado"


def _esperar(condicao, limite=15.0):
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.1)
    return False


# ---------- dados semeados direto com app.banco (sem classificar) ----------

@pytest.fixture
def caminho(tmp_path):
    caminho = tmp_path / "radar.db"
    with closing(banco.abrir(caminho)):
        pass
    return caminho


def _semear_semana(caminho):
    """22 avaliações da semana de 26/10: 20 neutras, 1 com card (atraso) e 1 crítica (alerta pendente)."""
    with closing(banco.abrir(caminho)) as conn:
        for i in range(20):
            banco.gravar_avaliacao(
                conn, id_externo=f"neutra-{i}", texto="texto neutro", nota=5,
                recebida_em=(SEGUNDA + timedelta(hours=i)).isoformat(), categorias=[], criticidade=[],
                status_classificacao="ok",
            )
        banco.gravar_avaliacao(
            conn, id_externo="com-card", texto="demorou", nota=2, recebida_em=(SEGUNDA + timedelta(days=1)).isoformat(),
            categorias=["atraso"], criticidade=[], status_classificacao="ok",
        )
        banco.gravar_avaliacao(
            conn, id_externo="critica-1", texto="passei mal", nota=1,
            recebida_em=(SEGUNDA + timedelta(days=2)).isoformat(), categorias=[], criticidade=["saude"],
            status_classificacao="ok", alerta_criado_em=(SEGUNDA + timedelta(days=2)).isoformat(),
        )


def _linhas(caminho, tabela):
    with closing(sqlite3.connect(caminho)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(l) for l in conn.execute(f"SELECT * FROM {tabela} ORDER BY id")]


def _contagens(caminho):
    return {t: len(_linhas(caminho, t)) for t in ("avaliacoes", "alertas", "acoes")}


def _sem_link(corpo):
    """Resposta do resumo sem o que muda a cada chamada (o link novo e a linha dele na mensagem)."""
    copia = dict(corpo)
    copia.pop("link_painel")
    copia["mensagem"] = "\n".join(l for l in copia["mensagem"].splitlines() if not l.startswith("Painel:"))
    return copia


def _token_do_link(link):
    assert link.startswith("http://localhost:")
    return link.rsplit("/", 1)[1]


def _alerta_com_estado(conn, id_externo, estado, tentativas, enviado_em=None):
    r = banco.gravar_avaliacao(
        conn, id_externo=id_externo, texto="t", nota=1, recebida_em=SEGUNDA.isoformat(), categorias=[],
        criticidade=["saude"], status_classificacao="ok", alerta_criado_em=SEGUNDA.isoformat(),
    )
    conn.execute(
        "UPDATE alertas SET estado = ?, tentativas = ?, enviado_em = ? WHERE id = ?",
        (estado, tentativas, enviado_em, r.alerta_id),
    )
    return r.alerta_id


# ---------- 1. dados e resumo ----------

def test_dados_e_resumo_iguais_depois_do_reinicio(tmp_path, caminho):
    _semear_semana(caminho)
    antes_do_banco = _contagens(caminho)

    with servidor(tmp_path, caminho, "log1.txt") as s:
        status, primeiro = s.pedir("GET", f"/resumo-semanal?semana={SEMANA}")
    assert status == 200 and primeiro["enviar"] is True
    assert primeiro["total"] == 22 and len(primeiro["criticos"]) == 1 and primeiro["cards"]
    assert primeiro["criticos"][0]["estado_alerta"] == "pendente"
    depois_do_primeiro = _contagens(caminho)

    with servidor(tmp_path, caminho, "log2.txt") as s:  # novo processo, mesmo arquivo
        status, segundo = s.pedir("GET", f"/resumo-semanal?semana={SEMANA}")
    assert status == 200

    assert _sem_link(primeiro) == _sem_link(segundo)
    assert primeiro["link_painel"] != segundo["link_painel"]  # cada resumo gera um link novo
    assert antes_do_banco == depois_do_primeiro == _contagens(caminho)  # o resumo não grava dados
    assert len(_linhas(caminho, "tokens_painel")) == 2  # a única escrita da rota: um token por chamada


# ---------- 2. token ----------

def test_token_continua_valido_depois_do_reinicio_e_expira_em_7_dias(tmp_path, caminho):
    _semear_semana(caminho)
    with servidor(tmp_path, caminho, "log1.txt") as s:
        status, corpo = s.pedir("GET", f"/resumo-semanal?semana={SEMANA}")
    assert status == 200
    token = _token_do_link(corpo["link_painel"])
    criado_ate = datetime.now(timezone.utc)

    with servidor(tmp_path, caminho, "log2.txt") as s:
        log = s.texto_do_log()
    with closing(banco.abrir(caminho)) as conn:
        assert tokens.validar_token(conn, token, datetime.now(timezone.utc)) is True
        assert tokens.validar_token(conn, token, criado_ate + timedelta(days=6, hours=23)) is True
        assert tokens.validar_token(conn, token, criado_ate + timedelta(days=7, minutes=1)) is False

    linhas = _linhas(caminho, "tokens_painel")
    assert len(linhas) == 1
    assert all(token not in str(v) for v in linhas[0].values())  # só o hash
    assert tokens.hash_do_token(token) == linhas[0]["token_hash"]
    assert token.encode() not in Path(caminho).read_bytes()
    assert token not in log and token not in (tmp_path / "log1.txt").read_text(encoding="utf-8", errors="replace")


# ---------- 3. duplicata ----------

def test_duplicata_depois_do_reinicio_responde_200_sem_criar_nada(tmp_path, caminho):
    _semear_semana(caminho)
    antes = _contagens(caminho)
    corpo = {"id_externo": "critica-1", "texto": "passei mal de novo", "nota": 1, "recebida_em": SEGUNDA.isoformat()}

    with servidor(tmp_path, caminho, "log1.txt"):
        pass  # sobe e cai: o reinício acontece antes do POST
    with servidor(tmp_path, caminho, "log2.txt") as s:
        # a duplicata é decidida ANTES do classificador (app/main.py, _processar): sem chave da Anthropic
        # no ambiente, qualquer tentativa de classificar daria 500
        status, resposta = s.pedir("POST", "/avaliacoes", corpo)
        status2, resposta2 = s.pedir("POST", "/avaliacoes", corpo)
    assert status == status2 == 200
    assert resposta["criada"] is False and resposta == resposta2
    assert "alerta_id" not in resposta
    assert _contagens(caminho) == antes
    assert [l["id_externo"] for l in _linhas(caminho, "avaliacoes")].count("critica-1") == 1


# ---------- 4. estado do alerta ----------

def test_estados_dos_alertas_mantidos_e_varredura_na_subida(tmp_path, caminho):
    with closing(banco.abrir(caminho)) as conn:
        enviado = _alerta_com_estado(conn, "a-enviado", "enviado", 1, "2026-10-26T15:00:04+00:00")
        falhou = _alerta_com_estado(conn, "a-falhou", "falhou", 2)
        pendente = _alerta_com_estado(conn, "a-pendente", "pendente", 0)
        esgotado = _alerta_com_estado(conn, "a-esgotado", "pendente", 5)
    antes = {l["id"]: l for l in _linhas(caminho, "alertas")}

    with servidor(tmp_path, caminho, "log1.txt") as s:
        # a recuperação da subida roda em segundo plano: espera a varredura terminar
        assert _esperar(lambda: {l["id"]: l for l in _linhas(caminho, "alertas")}[esgotado]["estado"] == "falhou")
        log = s.texto_do_log()
    depois = {l["id"]: l for l in _linhas(caminho, "alertas")}

    for id_, esperado in ((enviado, "enviado"), (falhou, "falhou"), (pendente, "pendente")):
        assert depois[id_] == antes[id_], esperado  # estado, tentativas, enviado_em, tardio: tudo igual
    assert depois[esgotado]["estado"] == "falhou"  # varredura: pendente com 5 tentativas vira falhou
    assert depois[esgotado]["tentativas"] == 5 and depois[esgotado]["tardio"] == 0
    assert depois[esgotado]["enviado_em"] is None  # sem envio
    assert all(l["tardio"] == 0 for l in depois.values())
    assert "não configurado" in log  # prova que o Telegram ficou desligado no subprocesso

    with servidor(tmp_path, caminho, "log2.txt"):  # segundo reinício: nada muda de novo
        pass
    assert {l["id"]: l for l in _linhas(caminho, "alertas")} == depois


# ---------- 5. revogação e migração ----------

def test_token_revogado_continua_invalido_depois_do_reinicio(tmp_path, caminho):
    agora = datetime.now(timezone.utc)
    with closing(banco.abrir(caminho)) as conn:
        revogado = tokens.criar_token_painel(conn, agora=agora)
        valido = tokens.criar_token_painel(conn, agora=agora)
        assert tokens.revogar_token(conn, revogado, agora) is True

    with servidor(tmp_path, caminho, "log1.txt"):
        pass
    with servidor(tmp_path, caminho, "log2.txt"):
        pass
    with closing(banco.abrir(caminho)) as conn:
        depois = datetime.now(timezone.utc)
        assert tokens.validar_token(conn, revogado, depois) is False
        assert tokens.validar_token(conn, valido, depois) is True  # o controle: só o revogado caiu
    assert [l["revogado_em"] is not None for l in _linhas(caminho, "tokens_painel")] == [True, False]


def test_banco_com_o_ddl_antigo_e_migrado_na_subida_real_sem_perder_dados(tmp_path):
    antigo = tmp_path / "antigo.db"
    with closing(sqlite3.connect(antigo)) as conn:
        conn.executescript(DDL_ANTIGO)
        conn.execute(
            "INSERT INTO avaliacoes (id_externo, texto, nota, recebida_em, prazo_resposta, categorias, "
            "criticidade, status_classificacao) VALUES ('antiga', 'texto antigo', 1, '2026-10-01T12:00:00+00:00', "
            "'2026-10-06T12:00:00+00:00', '[\"atraso\"]', '[\"saude\"]', 'ok')"
        )
        conn.execute(
            "INSERT INTO alertas (avaliacao_id, tipos, estado, tentativas, criado_em, enviado_em) "
            "VALUES (1, '[\"saude\"]', 'enviado', 1, '2026-10-01T12:00:00+00:00', '2026-10-01T12:00:03+00:00')"
        )
        conn.commit()
    avaliacao_antes = _linhas(antigo, "avaliacoes")

    def migrado():
        with closing(sqlite3.connect(antigo)) as conn:
            return {c[1] for c in conn.execute("PRAGMA table_info(alertas)")} >= {"tardio", "reconhecido_em"}

    with servidor(tmp_path, antigo, "log1.txt"):
        assert _esperar(migrado), "a subida real não migrou o banco"
    alerta = _linhas(antigo, "alertas")
    assert len(alerta) == 1
    assert (alerta[0]["estado"], alerta[0]["tentativas"], alerta[0]["enviado_em"]) == (
        "enviado", 1, "2026-10-01T12:00:03+00:00")
    assert alerta[0]["tardio"] == 0 and alerta[0]["reconhecido_em"] is None
    assert _linhas(antigo, "avaliacoes") == avaliacao_antes  # a avaliação antiga não mudou

    with servidor(tmp_path, antigo, "log2.txt"):  # segunda subida: idempotente
        pass
    assert _linhas(antigo, "alertas") == alerta
