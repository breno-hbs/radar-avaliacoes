"""Testes da página do painel, GET /painel/{token} (bloco 4.3). Dados SIMULADOS.

Fonte da verdade: docs/ESPECIFICACAO.md (token do painel e a decisão de 31/10/2026 sobre a página do painel).
Os tokens são sempre criados por tokens.criar_token_painel, nunca fixos. Nenhum teste usa rede externa.
"""

import re
import secrets
import urllib.error
import urllib.request
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

from app import banco, config_ranking, consultas, resumo, tokens
from test_painel_leitura import _avaliacao, _token, token  # noqa: F401 - fixture usada pelos testes
from test_reinicio_servidor import SEM_PROXY, servidor
from test_resumo_semanal import (  # noqa: F401 - fixtures usadas pelos testes
    RESUMO_DA_SEGUNDA, SEGUNDA, _varias, ambiente, app, caminho, cliente, relogio,
)

PAINEL = Path(__file__).resolve().parent.parent / "app" / "painel"
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
TEXTOS_DA_TELA_07 = (
    "Link inválido ou expirado",
    "Este link não funciona mais. Peça um novo ao responsável pelo Radar.",
    "Por segurança, o painel não mostra nada sem um link válido.",
)
PROIBIDOS_NO_JS = (
    "innerHTML", "insertAdjacentHTML", "outerHTML", "document.write", "eval", "new Function",
    "localStorage", "sessionStorage", "document.cookie", "console.log",
    "createContextualFragment", "DOMParser", "setHTML", "srcdoc", "javascript:",
)


def _js():
    return (PAINEL / "painel.js").read_text(encoding="utf-8")


def _confere_cabecalhos(r):
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["content-security-policy"] == CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-type"].startswith("text/html")


def _sem_script_nem_estilo_inline(html):
    for tag in re.findall(r"<script\b[^>]*>", html, re.IGNORECASE):
        assert re.search(r"\bsrc=\"/estatico/painel/painel\.js\"", tag), tag  # só o script externo
    corpos = re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.IGNORECASE | re.DOTALL)
    assert all(not c.strip() for c in corpos)  # nenhum script com corpo
    assert re.search(r"\sstyle\s*=", html, re.IGNORECASE) is None
    assert re.search(r"\son[a-z]+\s*=", html, re.IGNORECASE) is None
    assert "<style" not in html.lower()


# ---------- rota da página ----------

def test_token_valido_devolve_a_pagina_com_os_cabecalhos(cliente, token):
    r = cliente.get(f"/painel/{token}")
    assert r.status_code == 200
    _confere_cabecalhos(r)
    assert '<html lang="pt-BR">' in r.text
    assert "<title>Radar de Avaliações</title>" in r.text
    assert '<meta name="robots" content="noindex">' in r.text
    assert '<meta name="viewport"' in r.text
    _sem_script_nem_estilo_inline(r.text)


def test_404_uniforme_com_a_tela_de_link_invalido(cliente, ambiente):
    inexistente = secrets.token_urlsafe(32)
    expirado = _token(ambiente, agora=RESUMO_DA_SEGUNDA - timedelta(days=8))
    revogado = _token(ambiente)
    with closing(banco.abrir(ambiente)) as conn:
        tokens.revogar_token(conn, revogado, RESUMO_DA_SEGUNDA)
    outro_escopo = _token(ambiente, escopo="outro")
    casos = (inexistente, expirado, revogado, "malformado", outro_escopo)
    respostas = [cliente.get(f"/painel/{t}") for t in casos]
    cabecalhos = [{k: v for k, v in r.headers.items() if k != "content-length"} for r in respostas]
    for t, r, c in zip(casos, respostas, cabecalhos):
        assert r.status_code == 404
        _confere_cabecalhos(r)
        assert r.content == respostas[0].content  # o mesmo corpo, qualquer que seja o motivo
        assert c == cabecalhos[0]
        assert t not in r.text
        for texto in TEXTOS_DA_TELA_07:
            assert texto in r.text
        _sem_script_nem_estilo_inline(r.text)
    assert "segunda-feira" not in respostas[0].text  # não promete link novo (o n8n ainda não existe)


def test_token_expira_no_instante_exato(cliente, ambiente, relogio):
    t = _token(ambiente, agora=RESUMO_DA_SEGUNDA - timedelta(days=7))
    assert cliente.get(f"/painel/{t}").status_code == 404
    relogio.instante = RESUMO_DA_SEGUNDA - timedelta(seconds=1)
    assert cliente.get(f"/painel/{t}").status_code == 200


def test_html_nao_traz_dado_de_avaliacao(cliente, ambiente, token):
    marcador = "marcador-do-texto-da-avaliacao-que-nao-vai-no-html"
    _varias(ambiente, 20)
    _avaliacao(ambiente, SEGUNDA + timedelta(days=1), texto=marcador)
    r = cliente.get(f"/painel/{token}")
    assert r.status_code == 200
    assert marcador not in r.text
    assert token not in r.text  # o JS lê o token do endereço; o HTML não o repete


def test_banco_ausente_da_500_generico(cliente, ambiente, token, monkeypatch, tmp_path):
    monkeypatch.setenv("RADAR_DB", str(tmp_path / "nao-existe.db"))
    r = cliente.get(f"/painel/{token}")
    assert r.status_code == 500
    assert r.headers["cache-control"] == "no-store"
    assert token not in r.text


# ---------- arquivos estáticos ----------

@pytest.mark.parametrize("arquivo, tipo", [("painel.js", "text/javascript"), ("painel.css", "text/css")])
def test_arquivos_estaticos(cliente, arquivo, tipo):
    r = cliente.get(f"/estatico/painel/{arquivo}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(tipo)
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.text == (PAINEL / arquivo).read_text(encoding="utf-8")


@pytest.mark.parametrize("caminho_pedido", [
    "/estatico/painel/index.html", "/estatico/painel/link-invalido.html", "/estatico/painel/nada.js",
    "/estatico/painel/painel.JS", "/estatico/painel/painel.js.bak", "/estatico/painel/%2e%2e%2fmain.py",
    "/estatico/painel/..%5cmain.py",
])
def test_so_o_js_e_o_css_sao_estaticos(cliente, caminho_pedido):
    assert cliente.get(caminho_pedido).status_code == 404


def test_html_so_referencia_os_estaticos_do_painel():
    for nome in ("index.html", "link-invalido.html"):
        html = (PAINEL / nome).read_text(encoding="utf-8")
        referencias = re.findall(r"(?:src|href)=\"([^\"]+)\"", html)
        assert referencias, nome
        assert all(ref.startswith("/estatico/painel/") for ref in referencias), referencias
        _sem_script_nem_estilo_inline(html)


@pytest.mark.parametrize("caminho_pedido", ["/painel/", "/painel/a/b", f"/painel/{'A' * 43}/extra"])
def test_caminho_de_painel_fora_do_formato_da_a_tela_07(cliente, caminho_pedido):
    r = cliente.get(caminho_pedido)
    assert r.status_code == 404
    _confere_cabecalhos(r)
    assert r.content == (PAINEL / "link-invalido.html").read_bytes()


def test_tela_07_igual_no_html_da_pagina_e_no_404():
    pagina = (PAINEL / "index.html").read_text(encoding="utf-8")
    invalido = (PAINEL / "link-invalido.html").read_text(encoding="utf-8")
    for texto in TEXTOS_DA_TELA_07:
        assert texto in pagina and texto in invalido


# ---------- varredura estática do JS ----------

@pytest.mark.parametrize("termo", PROIBIDOS_NO_JS)
def test_js_nao_usa_termo_proibido(termo):
    assert termo not in _js()


def test_js_nao_usa_console_nem_atributo_de_evento_nem_estilo_inline():
    js = _js()
    assert re.search(r"\bconsole\s*\.", js) is None
    assert re.search(r"setAttribute\(\s*[\"'](?:on[a-z]+|style)[\"']", js) is None
    assert re.search(r"\.on[a-z]+\s*=", js) is None  # só addEventListener


def _mapa_do_js(nome):
    bloco = re.search(rf"const {nome} = Object\.freeze\(\{{(.*?)\}}\);", _js(), re.DOTALL)
    assert bloco, nome
    return dict(re.findall(r"^\s*(\w+): \"([^\"]*)\",?$", bloco.group(1), re.MULTILINE))


def test_nomes_e_recomendacoes_do_js_iguais_aos_do_servidor():
    assert _mapa_do_js("PROBLEMAS") == resumo.PROBLEMAS
    assert _mapa_do_js("RECOMENDACOES") == resumo.RECOMENDACOES
    assert _mapa_do_js("NOMES_SEM_ACAO") == resumo.NOMES_SEM_ACAO


def test_limite_do_historico_no_js_igual_ao_do_servidor():
    assert f"Mostrando até {consultas.MAX_SEMANAS_HISTORICO} semanas com ação registrada" in _js()


def test_post_do_js_envia_so_avaliacao_id_e_posicao_e_omite_nota_vazia():
    js = _js()
    assert "evidencias: card.evidencias.map((e) => ({ avaliacao_id: e.avaliacao_id, posicao: e.posicao }))" in js
    assert 'if (nota.area.value.trim() !== "") corpo.nota = nota.area.value;' in js  # sem strip no que vai
    registro = re.search(r"function abrirRegistro.*?\n  }\n", js, re.DOTALL).group(0)
    assert "trecho_exibido" not in registro and "trecho:" not in registro  # o trecho nunca vai no corpo


def test_amostra_minima_do_js_igual_a_da_configuracao():
    valor = re.search(r"const AMOSTRA_MINIMA = (\d+);", _js())
    assert valor and int(valor.group(1)) == config_ranking.carregar_config().amostra_minima


# ---------- servidor real: o token não vai para o log ----------

def _abrir(base, caminho):
    try:
        with SEM_PROXY.open(urllib.request.Request(base + caminho), timeout=20) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as erro:
        try:
            return erro.code, erro.read().decode("utf-8")
        finally:
            erro.close()


def test_token_nao_aparece_no_log_ao_abrir_a_pagina(tmp_path):
    from datetime import datetime, timezone

    caminho_db = tmp_path / "radar.db"
    with closing(banco.abrir(caminho_db)) as conn:
        valido = tokens.criar_token_painel(conn, agora=datetime.now(timezone.utc))
    with servidor(tmp_path, caminho_db, "log.txt") as s:
        st_valido, html = _abrir(s.base, f"/painel/{valido}")
        st_invalido, _ = _abrir(s.base, f"/painel/{secrets.token_urlsafe(32)}")
        st_js, _ = _abrir(s.base, "/estatico/painel/painel.js")
        log = s.texto_do_log()
    assert st_valido == 200 and "Radar de Avaliações" in html
    assert st_invalido == 404 and st_js == 200
    assert valido not in log
    assert "GET /painel/*** HTTP" in log  # a requisição da página, e não a do arquivo estático
