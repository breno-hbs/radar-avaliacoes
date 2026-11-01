"""Semana sintética fixa do teste de 2 minutos (critério 8). Dados SIMULADOS.

Fonte da verdade: docs/ESPECIFICACAO.md ("Teste dos 2 minutos"): uma semana sintética fixa, com 3 cards, igual
para todos os testadores. Os testes vieram antes de scripts/semana_de_teste.py e cobrem: determinismo, exatamente
3 cards, token só no link (nunca em arquivo nem em outra linha da saída) e banco utilizável pelo servidor real.
"""

import os
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import banco, config_ranking, consultas, ranking, semana, tokens
from test_reinicio_servidor import servidor

RAIZ = Path(__file__).resolve().parent.parent
SCRIPT = RAIZ / "scripts" / "semana_de_teste.py"
sys.path.insert(0, str(RAIZ / "scripts"))

import semana_de_teste  # noqa: E402

AGORA = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)  # sábado: a última semana completa começa em 19/10
URL_BASE = "http://localhost:8000"
CATEGORIAS_DOS_CARDS = {"item_faltando", "qualidade_temperatura", "atendimento"}


def _token_do_link(link: str) -> str:
    assert link.startswith(URL_BASE + "/painel/")
    return link.rsplit("/", 1)[1]


def _avaliacoes(caminho):
    with closing(banco.abrir(caminho)) as conn:
        return [tuple(l) for l in conn.execute("SELECT * FROM avaliacoes ORDER BY id")]


def _ranking_da_semana(caminho, agora=AGORA):
    inicio = semana.ultima_semana_completa(agora)
    with closing(banco.abrir(caminho)) as conn:
        atual = consultas.contar_semana(conn, *semana.intervalo_utc(inicio))
    return ranking.calcular_ranking(atual, None, config_ranking.carregar_config()), atual


def _rodar(caminho, url_base=URL_BASE):
    ambiente = {k: v for k, v in os.environ.items() if not k.startswith(("RADAR_", "ANTHROPIC_"))}
    ambiente["PYTHONIOENCODING"] = "utf-8"
    if url_base is not None:
        ambiente["RADAR_URL_BASE"] = url_base
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(caminho)], cwd=RAIZ, env=ambiente, capture_output=True, text=True,
        encoding="utf-8", timeout=60,
    )


# ---------- determinismo ----------

def test_duas_execucoes_geram_as_mesmas_avaliacoes_e_os_mesmos_3_cards(tmp_path):
    semana_de_teste.gerar(tmp_path / "a.db", url_base=URL_BASE, agora=AGORA)
    semana_de_teste.gerar(tmp_path / "b.db", url_base=URL_BASE, agora=AGORA)

    assert _avaliacoes(tmp_path / "a.db") == _avaliacoes(tmp_path / "b.db")
    resultado_a, _ = _ranking_da_semana(tmp_path / "a.db")
    resultado_b, _ = _ranking_da_semana(tmp_path / "b.db")
    assert resultado_a == resultado_b
    assert [c.categoria for c in resultado_a.cards] == [c.categoria for c in resultado_b.cards]


def test_o_conteudo_e_o_mesmo_em_qualquer_semana_so_as_datas_andam(tmp_path):
    semana_de_teste.gerar(tmp_path / "a.db", url_base=URL_BASE, agora=AGORA)
    semana_de_teste.gerar(tmp_path / "b.db", url_base=URL_BASE, agora=datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc))

    def conteudo(caminho):
        return [(l[1], l[2], l[3], l[6], l[7], l[8]) for l in _avaliacoes(caminho)]  # sem recebida_em/prazo

    assert conteudo(tmp_path / "a.db") == conteudo(tmp_path / "b.db")
    assert _avaliacoes(tmp_path / "a.db") != _avaliacoes(tmp_path / "b.db")  # as datas seguem o relógio


# ---------- a semana ----------

def test_exatamente_3_cards_e_mais_de_20_avaliacoes_na_ultima_semana_completa(tmp_path):
    semana_de_teste.gerar(tmp_path / "radar.db", url_base=URL_BASE, agora=AGORA)

    resultado, atual = _ranking_da_semana(tmp_path / "radar.db")
    assert [(c.categoria, c.pontuacao) for c in resultado.cards] == [
        ("item_faltando", 15), ("qualidade_temperatura", 12), ("atendimento", 8)]
    assert resultado.outros_temas == []
    assert resultado.invalidas == 0
    assert atual.total > 20
    assert len(_avaliacoes(tmp_path / "radar.db")) == atual.total  # nenhuma avaliação fora da semana


def test_nenhum_dado_real_nem_alerta_e_o_texto_e_curto(tmp_path):
    semana_de_teste.gerar(tmp_path / "radar.db", url_base=URL_BASE, agora=AGORA)

    with closing(banco.abrir(tmp_path / "radar.db")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM alertas").fetchone()[0] == 0  # nada de Telegram no teste
        assert conn.execute("SELECT COUNT(*) FROM acoes").fetchone()[0] == 0  # o testador começa sem ações
        textos = [l[0] for l in conn.execute("SELECT texto FROM avaliacoes")]
    assert all(0 < len(t) <= 140 for t in textos) and len(set(textos)) == len(textos)


# ---------- token ----------

def test_a_saida_traz_o_token_so_no_link_e_nada_e_gravado_no_disco(tmp_path):
    caminho = tmp_path / "radar.db"
    r = _rodar(caminho)

    assert r.returncode == 0, r.stderr
    token = _token_do_link(r.stdout.strip().splitlines()[-1])  # o link é a última linha
    assert tokens.formato_valido(token)
    assert r.stdout.count(token) == 1 and token not in r.stderr
    assert [l for l in r.stdout.splitlines() if token in l] == [r.stdout.strip().splitlines()[-1]]
    assert token.encode() not in caminho.read_bytes()  # só o hash
    assert {p.name for p in tmp_path.iterdir()} <= {"radar.db", "radar.db-journal"}  # nenhum arquivo com o token
    with closing(banco.abrir(caminho)) as conn:
        assert tokens.validar_token(conn, token, datetime.now(timezone.utc)) is True
        linhas = conn.execute("SELECT token_hash FROM tokens_painel").fetchall()
    assert [l[0] for l in linhas] == [tokens.hash_do_token(token)]


def test_cada_execucao_gera_um_token_novo_com_o_mesmo_conteudo(tmp_path):
    primeira, segunda = _rodar(tmp_path / "a.db"), _rodar(tmp_path / "b.db")

    assert primeira.returncode == segunda.returncode == 0
    assert _token_do_link(primeira.stdout.strip().splitlines()[-1]) != _token_do_link(segunda.stdout.strip().splitlines()[-1])
    assert [l[1:4] for l in _avaliacoes(tmp_path / "a.db")] == [l[1:4] for l in _avaliacoes(tmp_path / "b.db")]


def test_banco_existente_e_recusado_sem_alterar_nada(tmp_path):
    caminho = tmp_path / "radar.db"
    with closing(banco.abrir(caminho)):
        pass
    antes = caminho.read_bytes()

    r = _rodar(caminho)

    assert r.returncode != 0 and r.stdout == ""
    assert "já existe" in r.stderr
    assert caminho.read_bytes() == antes


def test_pasta_do_banco_inexistente_da_erro_claro(tmp_path):
    r = _rodar(tmp_path / "nao_existe" / "radar.db")

    assert r.returncode == 2 and r.stdout == ""
    assert "pasta do banco não existe" in r.stderr and "Traceback" not in r.stderr


@pytest.mark.parametrize("valor", [None, "", "ftp://x", "http://localhost:8000/", "localhost:8000"])
def test_url_base_ausente_ou_invalida_e_recusada_sem_criar_o_banco(tmp_path, valor):
    caminho = tmp_path / "radar.db"
    r = _rodar(caminho, url_base=valor)

    assert r.returncode != 0 and r.stdout == ""
    assert "RADAR_URL_BASE" in r.stderr
    assert not caminho.exists()


# ---------- banco utilizável pelo servidor real ----------

def test_servidor_real_abre_o_link_gerado_e_mostra_os_3_cards(tmp_path):
    caminho = tmp_path / "radar.db"
    link = semana_de_teste.gerar(caminho, url_base=URL_BASE)  # relógio real: é o que o testador vai ver
    token = _token_do_link(link)

    with servidor(tmp_path, caminho, "log.txt") as s:
        status, corpo = s.pedir("GET", f"/api/painel/{token}/cards")  # sem ?semana=: a última semana completa
        log = s.texto_do_log()

    assert status == 200
    assert [c["categoria"] for c in corpo["cards"]] == ["item_faltando", "qualidade_temperatura", "atendimento"]
    assert all(1 <= len(c["evidencias"]) <= 3 for c in corpo["cards"])
    assert token not in log
