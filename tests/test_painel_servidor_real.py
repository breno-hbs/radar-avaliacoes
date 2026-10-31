"""Rota real do painel com o servidor real (uvicorn em subprocesso), no padrão de test_reinicio_servidor.py.

O ambiente do subprocesso define RADAR_TELEGRAM_TOKEN, RADAR_TELEGRAM_CHAT_ID e ANTHROPIC_API_KEY como vazias
(o dotenv do app não sobrescreve variáveis existentes). Sem rede externa. Os tokens são criados por
tokens.criar_token_painel, nunca fixos.
"""

from contextlib import closing
from datetime import datetime, timedelta, timezone

from app import banco, tokens
from test_reinicio_servidor import servidor


def test_link_expirado_404_e_valido_200_no_servidor_real(tmp_path):
    caminho = tmp_path / "radar.db"
    agora = datetime.now(timezone.utc)
    marcador = "texto-marcador-do-cliente-que-nao-vai-para-o-log"
    with closing(banco.abrir(caminho)) as conn:
        banco.gravar_avaliacao(
            conn, id_externo="p-1", texto=marcador, nota=1, recebida_em="2026-10-27T15:00:00+00:00",
            categorias=["atraso"], criticidade=[], status_classificacao="ok",
        )
        valido = tokens.criar_token_painel(conn, agora=agora)
        expirado = tokens.criar_token_painel(conn, agora=agora - timedelta(days=8))
    with servidor(tmp_path, caminho, "log.txt") as s:
        st_valido, corpo = s.pedir("GET", f"/api/painel/{valido}/cards?semana=2026-10-26")
        st_hist, hist = s.pedir("GET", f"/api/painel/{valido}/historico")
        st_expirado, erro = s.pedir("GET", f"/api/painel/{expirado}/cards")
        log = s.texto_do_log()
    assert st_valido == 200 and corpo["cards"][0]["evidencias"][0]["trecho"] == marcador
    assert corpo["ultima_leitura"] == "2026-10-27T12:00:00-03:00"
    assert st_hist == 200 and hist == {"semanas": []}
    assert st_expirado == 404 and erro == {"detail": "link inválido ou expirado"}
    assert marcador not in log  # o texto de avaliação sai como dado, nunca no log
    assert valido not in log and expirado not in log  # o filtro mascara o token no log de acesso real
    assert "/api/painel/***/cards" in log
