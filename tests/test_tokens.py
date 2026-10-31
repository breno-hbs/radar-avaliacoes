"""Testes do token do painel (bloco 2.5).

Fonte da verdade: docs/ESPECIFICACAO.md (regras de segurança, tabela tokens_painel). Ponto de risco
do CLAUDE.md: expiração do link. Não há rota de painel real (Fase 4): a rota abaixo é montada só no
teste. O token TOKEN_FALSO é fictício e existe só nestes testes.
"""

import hashlib
import logging
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import Depends, Request
from fastapi.testclient import TestClient

from app import banco, main, tokens

RAIZ = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 10, 26, 11, 0, 0, tzinfo=timezone.utc)
TOKEN_FALSO = ("FALSO-conhecido_" * 3)[:43]
CORPO_404 = {"detail": "link inválido ou expirado"}


@pytest.fixture
def caminho(tmp_path):
    return tmp_path / "radar.db"


@pytest.fixture
def conn(caminho):
    with closing(banco.abrir(caminho)) as c:
        yield c


def _sha(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------- geração e armazenamento ----------

def test_token_tem_43_caracteres_url_safe_e_muda_a_cada_chamada(conn):
    gerados = {tokens.criar_token_painel(conn, agora=T0) for _ in range(20)}
    assert len(gerados) == 20
    for t in gerados:
        assert len(t) == 43
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}", t)


def test_usa_secrets_token_urlsafe_32(conn, monkeypatch):
    pedidos = []

    def falso(n):
        pedidos.append(n)
        return TOKEN_FALSO

    monkeypatch.setattr(tokens.secrets, "token_urlsafe", falso)
    assert tokens.criar_token_painel(conn, agora=T0) == TOKEN_FALSO
    assert pedidos == [32]


def test_hash_e_sha256_hex_minusculo_do_utf8():
    assert tokens.hash_do_token(TOKEN_FALSO) == _sha(TOKEN_FALSO)
    assert re.fullmatch(r"[0-9a-f]{64}", tokens.hash_do_token(TOKEN_FALSO))


def test_banco_guarda_so_o_hash_e_nunca_o_token_em_claro(conn, caminho):
    token = tokens.criar_token_painel(conn, agora=T0)
    linhas = conn.execute("SELECT * FROM tokens_painel").fetchall()
    assert len(linhas) == 1
    for valor in linhas[0]:
        assert token not in str(valor)
    assert _sha(token) in linhas[0]
    assert token.encode() not in Path(caminho).read_bytes()


def test_escopo_padrao_e_painel_e_datas_em_utc(conn):
    tokens.criar_token_painel(conn, agora=T0)
    escopo, criado, expira, revogado = conn.execute(
        "SELECT escopo, criado_em, expira_em, revogado_em FROM tokens_painel"
    ).fetchone()
    assert escopo == "painel"
    assert criado == "2026-10-26T11:00:00+00:00"
    assert expira == "2026-11-02T11:00:00+00:00"
    assert revogado is None


def test_agora_com_outro_fuso_vira_utc(conn):
    tokens.criar_token_painel(conn, agora=datetime(2026, 10, 26, 8, 0, tzinfo=timezone(timedelta(hours=-3))))
    assert conn.execute("SELECT criado_em FROM tokens_painel").fetchone()[0] == "2026-10-26T11:00:00+00:00"


def test_agora_sem_fuso_e_recusado(conn):
    with pytest.raises(ValueError):
        tokens.criar_token_painel(conn, agora=datetime(2026, 10, 26, 11, 0))
    token = tokens.criar_token_painel(conn, agora=T0)
    with pytest.raises(ValueError):
        tokens.validar_token(conn, token, datetime(2026, 10, 26, 11, 0))


# ---------- validade ----------

def test_token_valido_passa(conn):
    token = tokens.criar_token_painel(conn, agora=T0)
    assert tokens.validar_token(conn, token, T0) is True
    assert tokens.validar_token(conn, token, T0 + timedelta(days=3)) is True


def test_expirado_depois_de_7_dias(conn):
    token = tokens.criar_token_painel(conn, agora=T0)
    assert tokens.validar_token(conn, token, T0 + timedelta(days=7, seconds=1)) is False
    assert tokens.validar_token(conn, token, T0 + timedelta(days=30)) is False


def test_no_instante_exato_de_expira_em_ja_e_invalido(conn):
    token = tokens.criar_token_painel(conn, agora=T0)
    assert tokens.validar_token(conn, token, T0 + timedelta(days=7)) is False


def test_um_segundo_antes_de_expira_em_e_valido(conn):
    token = tokens.criar_token_painel(conn, agora=T0)
    assert tokens.validar_token(conn, token, T0 + timedelta(days=7) - timedelta(seconds=1)) is True


def test_um_microssegundo_antes_de_expira_em_e_valido(conn):
    token = tokens.criar_token_painel(conn, agora=T0)
    assert tokens.validar_token(conn, token, T0 + timedelta(days=7) - timedelta(microseconds=1)) is True


def test_fronteira_vale_com_agora_em_outro_fuso(conn):
    token = tokens.criar_token_painel(conn, agora=T0)
    vencimento_em_sp = (T0 + timedelta(days=7)).astimezone(timezone(timedelta(hours=-3)))
    assert tokens.validar_token(conn, token, vencimento_em_sp) is False
    assert tokens.validar_token(conn, token, vencimento_em_sp - timedelta(seconds=1)) is True


def test_inexistente_e_invalido(conn):
    assert tokens.validar_token(conn, TOKEN_FALSO, T0) is False


@pytest.mark.parametrize("malformado", [
    "", "curto", TOKEN_FALSO + "a", TOKEN_FALSO[:-1], TOKEN_FALSO[:-1] + "=",
    TOKEN_FALSO[:-1] + "/", TOKEN_FALSO[:-1] + "é", " " + TOKEN_FALSO[:-1], TOKEN_FALSO[:-1] + "\n",
    "a" * 64, None, 123,
])
def test_malformado_e_invalido_sem_consultar_o_banco(conn, malformado):
    class SemBanco:
        def execute(self, *a, **k):
            raise AssertionError("token malformado não pode chegar ao banco")

    assert tokens.validar_token(SemBanco(), malformado, T0) is False


def test_busca_e_pelo_hash_nunca_pelo_token(conn):
    token = tokens.criar_token_painel(conn, agora=T0)
    consultas = []
    conn.set_trace_callback(consultas.append)
    try:
        tokens.validar_token(conn, token, T0)
    finally:
        conn.set_trace_callback(None)
    assert consultas
    assert all(token not in q for q in consultas)
    assert any(_sha(token) in q for q in consultas)


# ---------- revogação ----------

def test_revogado_e_invalido(conn):
    token = tokens.criar_token_painel(conn, agora=T0)
    assert tokens.revogar_token(conn, token, T0 + timedelta(hours=1)) is True
    assert tokens.validar_token(conn, token, T0 + timedelta(hours=2)) is False


def test_revogar_duas_vezes_mantem_a_data_original(conn):
    token = tokens.criar_token_painel(conn, agora=T0)
    tokens.revogar_token(conn, token, T0 + timedelta(hours=1))
    tokens.revogar_token(conn, token, T0 + timedelta(hours=5))
    assert conn.execute("SELECT revogado_em FROM tokens_painel").fetchone()[0] == "2026-10-26T12:00:00+00:00"


def test_revogar_pelo_hash(conn):
    token = tokens.criar_token_painel(conn, agora=T0)
    assert tokens.revogar_token(conn, _sha(token), T0) is True
    assert tokens.validar_token(conn, token, T0 + timedelta(seconds=1)) is False


def test_revogar_inexistente_ou_malformado_devolve_false(conn):
    assert tokens.revogar_token(conn, TOKEN_FALSO, T0) is False
    assert tokens.revogar_token(conn, "lixo", T0) is False


def test_revogar_um_nao_afeta_outro(conn):
    a = tokens.criar_token_painel(conn, agora=T0)
    b = tokens.criar_token_painel(conn, agora=T0)
    tokens.revogar_token(conn, a, T0)
    assert tokens.validar_token(conn, b, T0 + timedelta(seconds=1)) is True


# ---------- dependência FastAPI e Referrer-Policy ----------

class Relogio:
    def __init__(self, instante):
        self.instante = instante

    def __call__(self):
        return self.instante


@pytest.fixture
def cenario(caminho, monkeypatch):
    """App real (main.criar_app) com uma rota de TESTE que usa exigir_token."""
    monkeypatch.setenv("RADAR_DB", str(caminho))
    monkeypatch.setenv("RADAR_API_KEY", "chave-ficticia-de-teste")
    app = main.criar_app()

    @app.get("/api/painel/{token}/teste")
    def rota_de_teste(token: str = Depends(main.exigir_token)):
        return {"ok": True}

    relogio = Relogio(T0)
    app.dependency_overrides[main.obter_relogio] = lambda: relogio
    with closing(banco.abrir(caminho)) as c:
        token = tokens.criar_token_painel(c, agora=T0)
        revogado = tokens.criar_token_painel(c, agora=T0)
        tokens.revogar_token(c, revogado, T0)
    with TestClient(app) as cliente:
        yield cliente, relogio, token, revogado


def test_token_valido_responde_200(cenario):
    cliente, _, token, _ = cenario
    r = cliente.get(f"/api/painel/{token}/teste")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_inexistente_malformado_expirado_e_revogado_dao_o_mesmo_404(cenario):
    cliente, relogio, token, revogado = cenario
    respostas = [
        cliente.get(f"/api/painel/{TOKEN_FALSO}/teste"),  # inexistente
        cliente.get("/api/painel/malformado/teste"),  # malformado
        cliente.get(f"/api/painel/{revogado}/teste"),  # revogado
    ]
    relogio.instante = T0 + timedelta(days=7)
    respostas.append(cliente.get(f"/api/painel/{token}/teste"))  # expirado, no instante exato
    for r in respostas:
        assert r.status_code == 404
        assert r.json() == CORPO_404
        assert r.content == respostas[0].content
    cabecalhos = [{k: v for k, v in r.headers.items() if k != "content-length"} for r in respostas]
    assert all(c == cabecalhos[0] for c in cabecalhos)


def test_um_segundo_antes_do_vencimento_ainda_passa_pela_rota(cenario):
    cliente, relogio, token, _ = cenario
    relogio.instante = T0 + timedelta(days=7) - timedelta(seconds=1)
    assert cliente.get(f"/api/painel/{token}/teste").status_code == 200


def test_referrer_policy_em_200_404_e_401(cenario):
    cliente, _, token, _ = cenario
    for r in (
        cliente.get(f"/api/painel/{token}/teste"),
        cliente.get(f"/api/painel/{TOKEN_FALSO}/teste"),
        cliente.get("/rota-que-nao-existe"),
        cliente.post("/avaliacoes", json={}),  # sem chave: 401
    ):
        assert r.headers.get("referrer-policy") == "no-referrer", r.status_code
    assert {r.status_code for r in (
        cliente.get(f"/api/painel/{token}/teste"), cliente.post("/avaliacoes", json={}),
    )} == {200, 401}


def test_nenhuma_rota_de_painel_real_existe():
    caminhos = {getattr(r, "path", "") for r in main.criar_app().routes}
    assert not any("painel" in c for c in caminhos)


# ---------- segredo nos logs ----------

def test_mascarar_troca_so_o_token_nos_caminhos_do_painel():
    assert tokens.mascarar_caminho(f"/painel/{TOKEN_FALSO}") == "/painel/***"
    assert tokens.mascarar_caminho(f"/api/painel/{TOKEN_FALSO}/cards") == "/api/painel/***/cards"
    assert tokens.mascarar_caminho(f"/api/painel/{TOKEN_FALSO}/acoes/7?x=1") == "/api/painel/***/acoes/7?x=1"
    assert tokens.mascarar_caminho(f"/painel/{TOKEN_FALSO}?a=b") == "/painel/***?a=b"
    assert tokens.mascarar_caminho("/avaliacoes") == "/avaliacoes"
    assert tokens.mascarar_caminho("/painel/x/y") == "/painel/***/y"  # qualquer trecho, mesmo malformado


def test_filtro_no_logger_de_acesso_do_uvicorn(caplog):
    caplog.set_level(logging.INFO)
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", f"/api/painel/{TOKEN_FALSO}/cards", "1.1", 404
    )
    assert TOKEN_FALSO not in caplog.text
    assert "/api/painel/***/cards" in caplog.text


def test_filtro_tambem_no_formatter_de_acesso_do_uvicorn():
    from uvicorn.logging import AccessFormatter

    registro = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", f"/painel/{TOKEN_FALSO}", "1.1", 404), None,
    )
    assert tokens.FiltroDeToken().filter(registro) is True
    texto = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False).format(registro)
    assert TOKEN_FALSO not in texto and "/painel/***" in texto


@pytest.mark.parametrize("nome", ["uvicorn.access", "uvicorn.error", "radar.api", "radar.alertas"])
def test_filtro_instalado_nos_loggers(nome, caplog):
    caplog.set_level(logging.DEBUG)
    logging.getLogger(nome).warning("caminho %s", f"/painel/{TOKEN_FALSO}")
    logging.getLogger(nome).warning(f"direto /api/painel/{TOKEN_FALSO}/historico")
    assert TOKEN_FALSO not in caplog.text


def test_log_do_app_com_a_url_da_requisicao_sai_mascarado(caminho, monkeypatch, caplog):
    """Força um log real: a rota de teste registra request.url (objeto URL, não str) no logger do app."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("RADAR_DB", str(caminho))
    app = main.criar_app()

    @app.get("/api/painel/{token}/log")
    def rota_que_loga(request: Request):
        logging.getLogger("radar.api").warning("pedido %s", request.url)
        logging.getLogger("radar.api").warning(request.url)  # msg que não é str
        return {"ok": True}

    with TestClient(app) as cliente:
        cliente.get(f"/api/painel/{TOKEN_FALSO}/log")
    do_app = [r for r in caplog.records if r.name == "radar.api"]
    assert len(do_app) == 2
    for r in do_app:
        assert TOKEN_FALSO not in r.getMessage()
        assert "/api/painel/***/log" in r.getMessage()


@pytest.mark.parametrize("caminho_logado,esperado", [
    (f"/painel//{TOKEN_FALSO}", "/painel//***"),
    (f"/api/painel//{TOKEN_FALSO}/cards", "/api/painel//***/cards"),
    (f"/api//painel/{TOKEN_FALSO}/cards", "/api//painel/***/cards"),
    (f"/Painel/{TOKEN_FALSO}", "/Painel/***"),
    (f"/API/PAINEL/{TOKEN_FALSO}/cards", "/API/PAINEL/***/cards"),
    (f"/raiz/painel/{TOKEN_FALSO}", "/raiz/painel/***"),
    (f"http://exemplo.teste/api/painel/{TOKEN_FALSO}/cards", "http://exemplo.teste/api/painel/***/cards"),
    # trecho fora do formato (token digitado errado): só a regex do caminho o pega
    ("/Painel//token-errado", "/Painel//***"),
    ("/API/painel//token-errado/cards", "/API/painel//***/cards"),
])
def test_mascarar_variantes_de_caminho(caminho_logado, esperado):
    assert tokens.mascarar_caminho(caminho_logado) == esperado


def test_mascarar_token_solto_em_qualquer_lugar_mas_nao_o_hash():
    assert TOKEN_FALSO not in tokens.mascarar_caminho(f"token {TOKEN_FALSO} fora de caminho")
    h = _sha(TOKEN_FALSO)
    assert tokens.mascarar_caminho(f"hash {h}") == f"hash {h}"  # 64 caracteres: não é um token


def test_filtro_mascara_args_dict_bytes_exc_info_e_stack_info():
    filtro = tokens.FiltroDeToken()
    r = logging.LogRecord("radar.api", logging.ERROR, __file__, 1, "%(c)s", ({"c": f"/painel/{TOKEN_FALSO}"},), None)
    filtro.filter(r)
    assert TOKEN_FALSO not in r.getMessage()

    r = logging.LogRecord("radar.api", logging.ERROR, __file__, 1, "%s", (f"/painel/{TOKEN_FALSO}".encode(),), None)
    filtro.filter(r)
    assert TOKEN_FALSO not in r.getMessage()

    try:
        raise RuntimeError(f"falhou em /api/painel/{TOKEN_FALSO}/cards")
    except RuntimeError:
        r = logging.LogRecord("radar.api", logging.ERROR, __file__, 1, "erro", (), sys.exc_info())
    r.stack_info = f"pilha /painel/{TOKEN_FALSO}"
    filtro.filter(r)
    texto = logging.Formatter().format(r)
    assert TOKEN_FALSO not in texto
    assert "RuntimeError" in texto


def test_filtro_tambem_nos_handlers_do_uvicorn_para_loggers_filhos(monkeypatch):
    handler = logging.StreamHandler()
    pai = logging.getLogger("uvicorn.error")
    monkeypatch.setattr(pai, "handlers", [handler])
    tokens.instalar_filtro_de_log()
    try:
        assert tokens._FILTRO in handler.filters
    finally:
        handler.removeFilter(tokens._FILTRO)


def test_filtro_tambem_nos_handlers_do_logger_raiz(monkeypatch):
    handler = logging.StreamHandler()
    monkeypatch.setattr(logging.getLogger(), "handlers", [handler])
    tokens.instalar_filtro_de_log()
    try:
        assert tokens._FILTRO in handler.filters
    finally:
        handler.removeFilter(tokens._FILTRO)


def test_500_sem_radar_db_tem_referrer_policy_e_corpo_generico(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.delenv("RADAR_DB", raising=False)
    app = main.criar_app()

    @app.get("/api/painel/{token}/teste")
    def rota_de_teste(token: str = Depends(main.exigir_token)):
        return {"ok": True}

    with TestClient(app, raise_server_exceptions=False) as cliente:
        r = cliente.get(f"/api/painel/{TOKEN_FALSO}/teste")
    assert r.status_code == 500
    assert r.json() == {"detail": "falha interna"}
    assert r.headers.get("referrer-policy") == "no-referrer"
    assert TOKEN_FALSO not in " | ".join(x.getMessage() for x in caplog.records if not x.name.startswith("httpx"))


def test_excecao_inesperada_em_rota_vira_500_generico_com_referrer_policy(monkeypatch):
    app = main.criar_app()

    @app.get("/explode")
    def explode():
        raise RuntimeError("detalhe interno")

    with TestClient(app, raise_server_exceptions=False) as cliente:
        r = cliente.get("/explode")
    assert r.status_code == 500
    assert "detalhe interno" not in r.text
    assert r.headers.get("referrer-policy") == "no-referrer"


def test_criar_token_exige_agora():
    import inspect

    assert inspect.signature(tokens.criar_token_painel).parameters["agora"].default is inspect.Parameter.empty


# ---------- uvicorn real ----------

def _porta_livre():
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_uvicorn_real_nao_escreve_o_token_no_log_de_acesso(tmp_path):
    """Sobe o uvicorn de verdade (subprocesso, configuração de log padrão) e lê a saída do log de acesso."""
    porta = _porta_livre()
    ambiente = {k: v for k, v in os.environ.items() if not k.startswith(("RADAR_", "ANTHROPIC_"))}
    ambiente["RADAR_DB"] = str(tmp_path / "radar.db")
    ambiente["PYTHONIOENCODING"] = "utf-8"
    saida = tmp_path / "uvicorn.log"
    with open(saida, "wb") as arquivo:
        processo = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(porta)],
            cwd=RAIZ, env=ambiente, stdout=arquivo, stderr=subprocess.STDOUT,
        )
        try:
            base = f"http://127.0.0.1:{porta}"
            for _ in range(100):
                try:
                    urllib.request.urlopen(f"{base}/pronto", timeout=1)
                except urllib.error.HTTPError as erro_http:
                    erro_http.close()  # o HTTPError guarda o socket da resposta
                    break  # respondeu (404): está no ar
                except OSError:
                    time.sleep(0.1)
            else:
                pytest.fail("uvicorn não subiu")
            for caminho in (f"/painel/{TOKEN_FALSO}", f"/api/painel/{TOKEN_FALSO}/cards?x=1"):
                with pytest.raises(urllib.error.HTTPError) as erro:
                    urllib.request.urlopen(base + caminho, timeout=5)
                erro.value.close()
                assert erro.value.headers.get("referrer-policy") == "no-referrer"
        finally:
            processo.terminate()
            processo.wait(timeout=10)
    log = saida.read_text(encoding="utf-8", errors="replace")
    assert "GET /pronto" in log  # o log de acesso está ligado e foi capturado
    assert "GET /painel/*** HTTP/1.1" in log
    assert "GET /api/painel/***/cards?x=1 HTTP/1.1" in log
    assert TOKEN_FALSO not in log


# ---------- escopo ----------

def test_validar_token_confere_o_escopo(conn):
    painel = tokens.criar_token_painel(conn, agora=T0)
    outro = tokens.criar_token_painel(conn, "outro", agora=T0)
    assert tokens.validar_token(conn, painel, T0) is True
    assert tokens.validar_token(conn, painel, T0, escopo="painel") is True
    assert tokens.validar_token(conn, outro, T0) is False
    assert tokens.validar_token(conn, outro, T0, escopo="outro") is True
    assert tokens.validar_token(conn, painel, T0, escopo="outro") is False


def test_escopo_tambem_vale_depois_da_validade_e_da_revogacao(conn):
    outro = tokens.criar_token_painel(conn, "outro", agora=T0)
    assert tokens.validar_token(conn, outro, T0 + timedelta(days=8), escopo="outro") is False  # expirado
    tokens.revogar_token(conn, outro, T0)
    assert tokens.validar_token(conn, outro, T0 + timedelta(seconds=1), escopo="outro") is False  # revogado
    # escopo errado continua recusado em qualquer estado
    assert tokens.validar_token(conn, outro, T0 + timedelta(days=8)) is False
    assert tokens.validar_token(conn, outro, T0 + timedelta(seconds=1)) is False


def test_escopo_fora_do_esperado_da_o_mesmo_404_que_inexistente(cenario, caminho):
    cliente, _, token, _ = cenario
    with closing(banco.abrir(caminho)) as c:
        outro = tokens.criar_token_painel(c, "outro", agora=T0)
    r_outro = cliente.get(f"/api/painel/{outro}/teste")
    r_inexistente = cliente.get(f"/api/painel/{TOKEN_FALSO}/teste")
    assert r_outro.status_code == 404 and r_outro.json() == CORPO_404
    assert r_outro.content == r_inexistente.content
    assert {k: v for k, v in r_outro.headers.items() if k != "content-length"} == \
        {k: v for k, v in r_inexistente.headers.items() if k != "content-length"}
    assert cliente.get(f"/api/painel/{token}/teste").status_code == 200  # escopo "painel" segue válido
