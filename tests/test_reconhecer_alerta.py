"""Testes de scripts/reconhecer_alerta.py (bloco 2.8). Dados SIMULADOS.

Fonte da verdade: docs/ESPECIFICACAO.md (decisão de 31/10/2026 sobre a recuperação de alertas,
"Reconhecimento"). O banco é temporário.
"""

import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import reconhecer_alerta  # noqa: E402

from app import banco  # noqa: E402

AGORA = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
DEPOIS = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def caminho(tmp_path, monkeypatch):
    caminho = tmp_path / "radar.db"
    monkeypatch.setenv("RADAR_DB", str(caminho))
    with closing(banco.abrir(caminho)):
        pass
    return caminho


_n = [0]


def _alerta(caminho, estado="falhou", id_externo=None):
    _n[0] += 1
    externo = id_externo or f"ext-{_n[0]}"
    with closing(banco.abrir(caminho)) as conn:
        r = banco.gravar_avaliacao(
            conn, id_externo=externo, texto="t", nota=1, recebida_em="2026-11-01T12:00:00+00:00",
            categorias=[], criticidade=["saude"], status_classificacao="ok",
            alerta_criado_em="2026-11-01T12:00:00+00:00",
        )
        conn.execute("UPDATE alertas SET estado = ? WHERE id = ?", (estado, r.alerta_id))
    return r.alerta_id, externo


def _reconhecido_em(caminho, alerta_id):
    with closing(banco.abrir(caminho)) as conn:
        return conn.execute("SELECT reconhecido_em FROM alertas WHERE id = ?", (alerta_id,)).fetchone()[0]


def test_reconhece_por_id(caminho, capsys):
    a, _ = _alerta(caminho)
    assert reconhecer_alerta.main(["--id", str(a)], agora=AGORA) == 0
    assert _reconhecido_em(caminho, a) == "2026-11-02T15:00:00+00:00"
    assert "reconhecido" in capsys.readouterr().out


def test_reconhece_por_id_externo(caminho):
    a, externo = _alerta(caminho, id_externo="crit-099")
    assert reconhecer_alerta.main(["--id-externo", externo], agora=AGORA) == 0
    assert _reconhecido_em(caminho, a) == "2026-11-02T15:00:00+00:00"


def test_idempotente_nao_sobrescreve_o_primeiro_instante(caminho, capsys):
    a, _ = _alerta(caminho)
    reconhecer_alerta.main(["--id", str(a)], agora=AGORA)
    assert reconhecer_alerta.main(["--id", str(a)], agora=DEPOIS) == 0
    assert _reconhecido_em(caminho, a) == "2026-11-02T15:00:00+00:00"
    assert "já" in capsys.readouterr().out


def test_nao_muda_o_estado(caminho):
    a, _ = _alerta(caminho)
    reconhecer_alerta.main(["--id", str(a)], agora=AGORA)
    with closing(banco.abrir(caminho)) as conn:
        estado, tentativas = conn.execute("SELECT estado, tentativas FROM alertas WHERE id = ?", (a,)).fetchone()
    assert estado == "falhou"


@pytest.mark.parametrize("estado", ["pendente", "enviado"])
def test_recusa_alerta_que_nao_esta_em_falhou(caminho, capsys, estado):
    a, _ = _alerta(caminho, estado=estado)
    codigo = reconhecer_alerta.main(["--id", str(a)], agora=AGORA)
    assert codigo != 0
    assert _reconhecido_em(caminho, a) is None
    saida = capsys.readouterr()
    assert "falhou" in (saida.out + saida.err) and estado in (saida.out + saida.err)


def test_alerta_nao_encontrado(caminho, capsys):
    assert reconhecer_alerta.main(["--id", "999"], agora=AGORA) != 0
    assert "não encontrado" in capsys.readouterr().err
    assert reconhecer_alerta.main(["--id-externo", "nao-existe"], agora=AGORA) != 0


@pytest.mark.parametrize("argv", [[], ["--id", "1", "--id-externo", "x"]])
def test_exige_exatamente_um_identificador(caminho, argv):
    with pytest.raises(SystemExit) as erro:
        reconhecer_alerta.main(argv, agora=AGORA)
    assert erro.value.code != 0


def test_sem_radar_db(monkeypatch, capsys):
    monkeypatch.delenv("RADAR_DB", raising=False)
    assert reconhecer_alerta.main(["--id", "1"], agora=AGORA) != 0
    assert "RADAR_DB" in capsys.readouterr().err


def test_banco_inexistente_nao_e_criado(tmp_path, monkeypatch):
    ausente = tmp_path / "nao_existe.db"
    monkeypatch.setenv("RADAR_DB", str(ausente))
    assert reconhecer_alerta.main(["--id", "1"], agora=AGORA) != 0
    assert not ausente.exists()


def test_instante_padrao_e_utc(caminho):
    a, _ = _alerta(caminho)
    assert reconhecer_alerta.main(["--id", str(a)]) == 0
    assert _reconhecido_em(caminho, a).endswith("+00:00")


def test_corrida_entre_leitura_e_gravacao_nao_finge_que_gravou(caminho, capsys, monkeypatch):
    a, _ = _alerta(caminho)
    abrir = banco.abrir

    def abrir_com_corrida(caminho_banco):
        conn = abrir(caminho_banco)
        execute = conn.execute

        class Conexao:
            def __getattr__(self, nome):
                return getattr(conn, nome)

            def execute(self, sql, *args):
                if sql.startswith("UPDATE alertas SET reconhecido_em"):
                    execute("UPDATE alertas SET reconhecido_em = '2026-11-01T08:00:00+00:00' WHERE id = ?", (a,))
                return execute(sql, *args)

            def close(self):
                conn.close()

        return Conexao()

    monkeypatch.setattr(banco, "abrir", abrir_com_corrida)
    assert reconhecer_alerta.main(["--id", str(a)], agora=AGORA) == 0
    assert "já reconhecido" in capsys.readouterr().out
