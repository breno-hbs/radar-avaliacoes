"""Testes do medidor de alertas (bloco 2.4).

Fonte da verdade: docs/ESPECIFICACAO.md (tabela alertas, critério 5). O banco é temporário e o
medidor só lê: nenhum teste toca em banco real, token ou texto de avaliação além do que ele mesmo grava.
"""

import hashlib
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import medir_alertas  # noqa: E402

from app import banco  # noqa: E402

TEXTO_SECRETO = "texto de avaliacao que nunca deve sair no relatorio"


@pytest.fixture
def caminho(tmp_path):
    return tmp_path / "radar.db"


def _grava(conn, n, criado_em, estado="pendente", enviado_em=None, tentativas=0):
    """Grava uma avaliação crítica com alerta e ajusta criado_em/enviado_em/estado direto no banco."""
    r = banco.gravar_avaliacao(
        conn, id_externo=f"ext-{n}", texto=TEXTO_SECRETO, nota=1,
        recebida_em="2026-10-26T12:00:00-03:00", categorias=["qualidade_temperatura"],
        criticidade=["saude"], status_classificacao="ok", alerta_criado_em=criado_em,
    )
    conn.execute(
        "UPDATE alertas SET estado = ?, enviado_em = ?, tentativas = ? WHERE id = ?",
        (estado, enviado_em, tentativas, r.alerta_id),
    )


@pytest.fixture
def banco_com_alertas(caminho):
    with closing(banco.abrir(caminho)) as conn:
        _grava(conn, 1, "2026-10-26T15:00:00+00:00", "enviado", "2026-10-26T15:00:04+00:00", 1)
        _grava(conn, 2, "2026-10-26T15:10:00+00:00", "enviado", "2026-10-26T15:10:25+00:00", 1)
        _grava(conn, 3, "2026-10-26T15:20:00+00:00", "enviado", "2026-10-26T15:20:07+00:00", 2)
        _grava(conn, 4, "2026-10-26T15:30:00+00:00", "falhou", "2026-10-26T15:31:00+00:00", 2)  # só "enviado" entra nos tempos
        _grava(conn, 5, "2026-10-26T15:40:00+00:00", "pendente", None, 0)
        _grava(conn, 6, "2026-10-26T15:50:00+00:00", "pendente", None, 0)
    return caminho


def _sha(caminho):
    return hashlib.sha256(Path(caminho).read_bytes()).hexdigest()


def test_conta_alertas_por_estado(banco_com_alertas):
    r = medir_alertas.medir(banco_com_alertas)
    assert r["estados"] == {"pendente": 2, "enviado": 3, "falhou": 1}


def test_estado_sem_alertas_aparece_com_zero(caminho):
    with closing(banco.abrir(caminho)) as conn:
        _grava(conn, 1, "2026-10-26T15:00:00+00:00", "enviado", "2026-10-26T15:00:02+00:00", 1)
    assert medir_alertas.medir(caminho)["estados"] == {"pendente": 0, "enviado": 1, "falhou": 0}


def test_minimo_mediana_e_maximo_em_segundos_so_dos_enviados(banco_com_alertas):
    t = medir_alertas.medir(banco_com_alertas)["tempos"]
    assert t["n"] == 3
    assert t["minimo"] == pytest.approx(4.0)
    assert t["mediana"] == pytest.approx(7.0)
    assert t["maximo"] == pytest.approx(25.0)  # mediana 7 != média 12; o falhou de 60 s fica de fora


def test_mediana_com_quantidade_par_e_media_dos_dois_do_meio(caminho):
    with closing(banco.abrir(caminho)) as conn:
        _grava(conn, 1, "2026-10-26T15:00:00+00:00", "enviado", "2026-10-26T15:00:02+00:00", 1)
        _grava(conn, 2, "2026-10-26T15:10:00+00:00", "enviado", "2026-10-26T15:10:08+00:00", 1)
    assert medir_alertas.medir(caminho)["tempos"]["mediana"] == pytest.approx(5.0)


def test_tempo_com_fusos_diferentes_usa_o_instante_real(caminho):
    with closing(banco.abrir(caminho)) as conn:
        _grava(conn, 1, "2026-10-26T12:00:00-03:00", "enviado", "2026-10-26T15:00:05+00:00", 1)
    assert medir_alertas.medir(caminho)["tempos"]["minimo"] == pytest.approx(5.0)


def test_sem_enviados_nao_ha_tempos(caminho):
    with closing(banco.abrir(caminho)) as conn:
        _grava(conn, 1, "2026-10-26T15:00:00+00:00", "falhou", None, 2)
    r = medir_alertas.medir(caminho)
    assert r["tempos"] is None


def test_banco_vazio(caminho):
    with closing(banco.abrir(caminho)):
        pass
    r = medir_alertas.medir(caminho)
    assert r["estados"] == {"pendente": 0, "enviado": 0, "falhou": 0}
    assert r["tempos"] is None


def test_o_banco_nao_e_alterado(banco_com_alertas):
    antes = _sha(banco_com_alertas)
    medir_alertas.medir(banco_com_alertas)
    medir_alertas.main(["--banco", str(banco_com_alertas)])
    assert _sha(banco_com_alertas) == antes
    assert not Path(str(banco_com_alertas) + "-journal").exists()


def test_a_conexao_e_somente_leitura(banco_com_alertas):
    with closing(medir_alertas.abrir_somente_leitura(banco_com_alertas)) as conn:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM alertas")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE alertas SET estado = 'falhou'")


def test_nao_cria_banco_que_nao_existe(tmp_path, capsys):
    ausente = tmp_path / "nao_existe.db"
    codigo = medir_alertas.main(["--banco", str(ausente)])
    assert codigo != 0
    assert not ausente.exists()
    assert "banco" in capsys.readouterr().err.lower()


def test_relatorio_tem_cabecalho_sobre_classificacao_e_nao_vaza_texto(banco_com_alertas, capsys):
    codigo = medir_alertas.main(["--banco", str(banco_com_alertas)])
    saida = capsys.readouterr().out
    assert codigo == 0
    assert "classificação" in saida.lower()
    assert "criado_em" in saida
    for rotulo in ("pendente", "enviado", "falhou", "mínimo", "mediana", "máximo"):
        assert rotulo in saida.lower()
    assert TEXTO_SECRETO not in saida
    assert "token" not in saida.lower()


def test_banco_vem_de_radar_db_quando_nao_ha_argumento(banco_com_alertas, monkeypatch, capsys):
    monkeypatch.setenv("RADAR_DB", str(banco_com_alertas))
    assert medir_alertas.main([]) == 0
    assert "enviado" in capsys.readouterr().out


def test_sem_banco_informado_pede_radar_db_ou_banco(monkeypatch, capsys):
    monkeypatch.delenv("RADAR_DB", raising=False)
    assert medir_alertas.main([]) != 0
    assert "RADAR_DB" in capsys.readouterr().err


def test_caminho_com_espaco_funciona(tmp_path):
    pasta = tmp_path / "pasta com espaço"
    pasta.mkdir()
    destino = pasta / "radar.db"
    with closing(banco.abrir(destino)) as conn:
        _grava(conn, 1, "2026-10-26T15:00:00+00:00", "enviado", "2026-10-26T15:00:03+00:00", 1)
    assert medir_alertas.medir(destino)["tempos"]["minimo"] == pytest.approx(3.0)


def test_data_malformada_da_erro_curto(caminho, capsys):
    with closing(banco.abrir(caminho)) as conn:
        _grava(conn, 1, "2026-10-26T15:00:00+00:00", "enviado", "isto nao e data", 1)
    assert medir_alertas.main(["--banco", str(caminho)]) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_cabecalho_avisa_que_nova_tentativa_entra_no_tempo(banco_com_alertas, capsys):
    medir_alertas.main(["--banco", str(banco_com_alertas)])
    assert "nova tentativa" in capsys.readouterr().out.lower()
