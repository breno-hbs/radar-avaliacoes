"""Critério 7 no nível da API, com o servidor real (uvicorn em subprocesso), no padrão de
test_reinicio_servidor.py: registrar uma ação, derrubar o processo, subir de novo e recuperá-la pelo histórico.

O ambiente do subprocesso define RADAR_TELEGRAM_TOKEN, RADAR_TELEGRAM_CHAT_ID e ANTHROPIC_API_KEY como vazias
(o dotenv do app não sobrescreve variáveis existentes). Sem rede externa. O token é criado por
tokens.criar_token_painel.
"""

from contextlib import closing
from datetime import datetime, timezone

from app import banco, tokens, trechos
from test_reinicio_servidor import servidor


def test_acao_registrada_sobrevive_ao_reinicio(tmp_path):
    caminho = tmp_path / "radar.db"
    textos = ["a entrega atrasou uma hora " * 10, "chegou fria e atrasada"]
    with closing(banco.abrir(caminho)) as conn:
        ids = [
            banco.gravar_avaliacao(
                conn, id_externo=f"p-{i}", texto=t, nota=1, recebida_em=f"2026-10-2{7 + i}T15:00:00+00:00",
                categorias=["atraso"], criticidade=[], status_classificacao="ok",
            ).avaliacao_id
            for i, t in enumerate(textos)
        ]
        token = tokens.criar_token_painel(conn, agora=datetime.now(timezone.utc))
    corpo = {"categoria": "atraso", "semana_inicio": "2026-10-26", "nota": "nota-do-dono-fora-do-log",
             "evidencias": [{"avaliacao_id": ids[1], "posicao": 1}, {"avaliacao_id": ids[0], "posicao": 2}]}

    with servidor(tmp_path, caminho, "log1.txt") as s:
        st_post, criada = s.pedir("POST", f"/api/painel/{token}/acoes", corpo)
        st_patch, mudada = s.pedir("PATCH", f"/api/painel/{token}/acoes/{criada['id']}", {"estado": "em_andamento"})
        log1 = s.texto_do_log()
    assert st_post == 201 and st_patch == 200 and mudada["estado"] == "em_andamento"

    with servidor(tmp_path, caminho, "log2.txt") as s:  # novo processo, mesmo arquivo
        st_hist, hist = s.pedir("GET", f"/api/painel/{token}/historico")
        st_dup, dup = s.pedir("POST", f"/api/painel/{token}/acoes", corpo)  # a unicidade também sobrevive
        log2 = s.texto_do_log()
    assert st_hist == 200
    acao = hist["semanas"][0]["acoes"][0]
    assert acao["id"] == criada["id"] and acao["estado"] == "em_andamento" and acao["nota"] == corpo["nota"]
    assert hist["semanas"][0]["semana_inicio"] == "2026-10-26T00:00:00-03:00"
    assert acao["evidencias"] == [
        {"posicao": 1, "trecho_exibido": trechos.trecho(textos[1]), "texto_original": textos[1]},
        {"posicao": 2, "trecho_exibido": trechos.trecho(textos[0]), "texto_original": textos[0]},
    ]
    assert st_dup == 409 and dup["id"] == criada["id"] and dup["estado"] == "em_andamento"
    for log in (log1, log2):
        assert token not in log and corpo["nota"] not in log
        assert "/api/painel/***/acoes" in log or "/api/painel/***/historico" in log
