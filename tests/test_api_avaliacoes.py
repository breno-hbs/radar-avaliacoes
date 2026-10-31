"""Testes do POST /avaliacoes (bloco 2.2 da Fase 2).

Fonte da verdade: docs/ESPECIFICACAO.md (rota, regras de segurança e decisões da Fase 2).
O classificador é SEMPRE substituído por um falso: nenhum teste chama a API real nem gasta
créditos. Dados sintéticos; a chave abaixo é fictícia e existe só nestes testes.
"""

import logging
from contextlib import ExitStack, closing

import pytest
from fastapi.testclient import TestClient

from app import banco, classificador, main
from app.classificador import ErroPermanenteDaAPI, ErroTecnicoEsgotado
from avaliacao import rodar_oficial

CHAVE = "chave-ficticia-de-teste"

CORPO = {
    "id_externo": "ext-1",
    "texto": "O pedido chegou frio e faltou um item.",
    "nota": 2,
    "recebida_em": "2026-10-26T12:00:00-03:00",
}


class Falso:
    """Classificador falso e injetável: registra as chamadas e devolve o que for combinado."""

    def __init__(self, saida=None, erro=None, antes=None):
        self.saida = saida
        self.erro = erro
        self.antes = antes
        self.chamadas = []

    @classmethod
    def ok(cls, categorias=("atraso",), criticidade=(), **kw):
        return cls(saida={"categorias": list(categorias), "criticidade": list(criticidade)}, **kw)

    @classmethod
    def invalida(cls):
        return cls(saida=None)

    @classmethod
    def com_erro(cls, erro):
        return cls(erro=erro)

    def __call__(self, texto):
        self.chamadas.append(texto)
        if self.antes:
            self.antes(texto)
        if self.erro:
            raise self.erro
        return {"saida": self.saida, "tentativas": 1, "erros": [], "usos": []}


@pytest.fixture(autouse=True)
def limpar_recursos_reais():
    main._recursos.cache_clear()
    main._avisar_chave_ausente.cache_clear()
    yield
    main._recursos.cache_clear()
    main._avisar_chave_ausente.cache_clear()


@pytest.fixture
def caminho(tmp_path, monkeypatch):
    destino = tmp_path / "radar.db"
    monkeypatch.setenv("RADAR_API_KEY", CHAVE)
    monkeypatch.setenv("RADAR_DB", str(destino))
    return destino


@pytest.fixture
def novo_cliente(caminho):
    with ExitStack() as pilha:

        def criar(falso, **kw):
            app = main.criar_app()
            app.dependency_overrides[main.obter_classificador] = lambda: falso
            return pilha.enter_context(TestClient(app, **kw))

        yield criar


def _post(cliente, corpo=None, chave=CHAVE, **kw):
    headers = {} if chave is None else {"X-API-Key": chave}
    return cliente.post("/avaliacoes", json=CORPO if corpo is None else corpo, headers=headers, **kw)


def _conta(caminho, tabela):
    with closing(banco.abrir(caminho)) as c:
        return c.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]


def _nada_gravado(caminho):
    return _conta(caminho, "avaliacoes") == 0 and _conta(caminho, "alertas") == 0


def _avaliacao(caminho, id_externo="ext-1"):
    with closing(banco.abrir(caminho)) as c:
        return banco.buscar_avaliacao_por_id_externo(c, id_externo)


def _alerta(caminho, avaliacao_id):
    with closing(banco.abrir(caminho)) as c:
        return banco.buscar_alerta_por_avaliacao(c, avaliacao_id)


# a) autenticação ---------------------------------------------------------------


def test_sem_chave_devolve_401(novo_cliente, caminho):
    falso = Falso.ok()
    r = _post(novo_cliente(falso), chave=None)
    assert r.status_code == 401
    assert falso.chamadas == []
    assert _nada_gravado(caminho)


def test_chave_errada_devolve_401(novo_cliente, caminho):
    falso = Falso.ok()
    r = _post(novo_cliente(falso), chave="chave-errada")
    assert r.status_code == 401
    assert falso.chamadas == []
    assert _nada_gravado(caminho)


def test_chave_certa_passa(novo_cliente):
    r = _post(novo_cliente(Falso.ok()))
    assert r.status_code == 201


def test_chave_vazia_no_cabecalho_devolve_401(novo_cliente):
    assert _post(novo_cliente(Falso.ok()), chave="").status_code == 401


def test_autenticacao_vem_antes_da_validacao_do_corpo(novo_cliente):
    r = _post(novo_cliente(Falso.ok()), corpo={"lixo": 1}, chave=None)
    assert r.status_code == 401
    assert "id_externo" not in r.text


@pytest.mark.parametrize("enviada", [None, "", CHAVE, "qualquer-coisa"])
def test_variavel_ausente_recusa_toda_requisicao(novo_cliente, monkeypatch, caminho, enviada):
    monkeypatch.delenv("RADAR_API_KEY", raising=False)
    falso = Falso.ok()
    assert _post(novo_cliente(falso), chave=enviada).status_code == 401
    assert falso.chamadas == []
    assert _nada_gravado(caminho)


@pytest.mark.parametrize("enviada", [None, "", "qualquer-coisa"])
def test_variavel_vazia_nunca_aceita_chave_vazia(novo_cliente, monkeypatch, caminho, enviada):
    monkeypatch.setenv("RADAR_API_KEY", "")
    falso = Falso.ok()
    assert _post(novo_cliente(falso), chave=enviada).status_code == 401
    assert falso.chamadas == []
    assert _nada_gravado(caminho)


def test_chave_com_caracteres_nao_ascii_nao_derruba_o_servidor(novo_cliente):
    cliente = novo_cliente(Falso.ok(), raise_server_exceptions=False)
    for chave in ("chävé-ç-日本".encode("utf-8"), b"\xff\xfe"):
        r = cliente.post("/avaliacoes", json=CORPO, headers={"X-API-Key": chave})
        assert r.status_code == 401


def test_compara_com_compare_digest_e_em_bytes(novo_cliente, monkeypatch):
    chamadas = []
    original = main.hmac.compare_digest

    def espiao(a, b):
        chamadas.append((type(a), type(b)))
        return original(a, b)

    monkeypatch.setattr(main.hmac, "compare_digest", espiao)
    _post(novo_cliente(Falso.ok()), chave="chave-errada")
    assert chamadas == [(bytes, bytes)]


@pytest.mark.parametrize(
    "enviada", [CHAVE[:-1] + "X", CHAVE[:-1], CHAVE + "x", CHAVE.upper(), " " + CHAVE]
)
def test_chave_parecida_com_a_certa_devolve_401(novo_cliente, enviada):
    # mesmo tamanho, prefixo da certa, a certa com sobra, caixa diferente, espaço no começo
    assert _post(novo_cliente(Falso.ok()), chave=enviada).status_code == 401


@pytest.mark.parametrize("bruto", [b"{", b"isto nao e json", bytes([0xFF, 0xFE]), b'{"id_externo":'])
def test_sem_chave_e_corpo_malformado_devolve_401_e_nao_422(novo_cliente, caminho, bruto):
    falso = Falso.ok()
    r = novo_cliente(falso).post(
        "/avaliacoes", content=bruto, headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 401
    assert "id_externo" not in r.text
    assert falso.chamadas == []
    assert _nada_gravado(caminho)


@pytest.mark.parametrize("rota", ["/docs", "/redoc", "/openapi.json"])
def test_documentacao_automatica_nao_fica_exposta(novo_cliente, rota):
    cliente = novo_cliente(Falso.ok())
    assert cliente.get(rota).status_code == 404
    assert cliente.get(rota, headers={"X-API-Key": CHAVE}).status_code == 404


def test_variavel_vazia_registra_aviso_sem_valor_de_chave(novo_cliente, monkeypatch, caplog):
    monkeypatch.setenv("RADAR_API_KEY", "")
    caplog.set_level(logging.WARNING)
    assert _post(novo_cliente(Falso.ok()), chave="qualquer").status_code == 401
    assert "RADAR_API_KEY" in caplog.text
    assert "qualquer" not in caplog.text


@pytest.mark.parametrize("valor", [None, ""])
def test_aviso_de_variavel_ausente_sai_uma_so_vez(novo_cliente, monkeypatch, caplog, valor):
    if valor is None:
        monkeypatch.delenv("RADAR_API_KEY", raising=False)
    else:
        monkeypatch.setenv("RADAR_API_KEY", valor)
    caplog.set_level(logging.WARNING)
    cliente = novo_cliente(Falso.ok())
    respostas = [_post(cliente, chave="x") for _ in range(3)]
    assert [r.status_code for r in respostas] == [401, 401, 401]  # a recusa continua em toda requisição
    avisos = [r for r in caplog.records if "RADAR_API_KEY" in r.getMessage()]
    assert len(avisos) == 1


def test_401_traz_www_authenticate(novo_cliente):
    r = _post(novo_cliente(Falso.ok()), chave=None)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "X-API-Key"
    r2 = _post(novo_cliente(Falso.ok()), chave="errada")
    assert r2.headers["www-authenticate"] == "X-API-Key"


def test_status_e_401_e_nao_403_com_cabecalho_ausente(novo_cliente):
    assert _post(novo_cliente(Falso.ok()), chave=None).status_code == 401


def test_resposta_401_nao_vaza_a_chave_esperada_nem_a_enviada(novo_cliente):
    r = _post(novo_cliente(Falso.ok()), chave="chave-errada-xyz")
    assert CHAVE not in r.text
    assert "chave-errada-xyz" not in r.text
    assert CHAVE not in str(dict(r.headers))


def test_chave_nunca_aparece_em_log(novo_cliente, caplog):
    caplog.set_level(logging.DEBUG)
    cliente = novo_cliente(Falso.ok())
    _post(cliente, chave="chave-errada-xyz")
    _post(cliente)
    _post(cliente, corpo={"lixo": 1})
    assert CHAVE not in caplog.text
    assert "chave-errada-xyz" not in caplog.text


# b) validação ------------------------------------------------------------------


@pytest.mark.parametrize("campo", list(CORPO))
def test_campo_ausente_devolve_422(novo_cliente, caminho, campo):
    falso = Falso.ok()
    corpo = {k: v for k, v in CORPO.items() if k != campo}
    assert _post(novo_cliente(falso), corpo=corpo).status_code == 422
    assert falso.chamadas == []
    assert _nada_gravado(caminho)


@pytest.mark.parametrize(
    "campo, valor",
    [
        ("id_externo", 123), ("id_externo", None), ("id_externo", ""),
        ("texto", 5), ("texto", None),
        ("nota", "2"), ("nota", 2.5), ("nota", True), ("nota", None),
        ("recebida_em", 123), ("recebida_em", None),
        ("recebida_em", "2026-10-26T12:00:00"),
        ("recebida_em", "2026-10-26"),
        ("recebida_em", "ontem de manhã"),
    ],
)
def test_tipo_errado_ou_recebida_em_sem_fuso_devolve_422(novo_cliente, caminho, campo, valor):
    falso = Falso.ok()
    corpo = {**CORPO, campo: valor}
    assert _post(novo_cliente(falso), corpo=corpo).status_code == 422
    assert falso.chamadas == []
    assert _nada_gravado(caminho)


@pytest.mark.parametrize("corpo", [[], "texto solto", 7])
def test_corpo_que_nao_e_objeto_devolve_422(novo_cliente, caminho, corpo):
    falso = Falso.ok()
    assert _post(novo_cliente(falso), corpo=corpo).status_code == 422
    assert falso.chamadas == []


@pytest.mark.parametrize("bruto", [b"isto nao e json", b"null", b"", b"{"])
def test_corpo_que_nao_e_json_valido_de_objeto_devolve_422(novo_cliente, caminho, bruto):
    falso = Falso.ok()
    r = novo_cliente(falso).post(
        "/avaliacoes",
        content=bruto,
        headers={"X-API-Key": CHAVE, "Content-Type": "application/json"},
    )
    assert r.status_code == 422
    assert falso.chamadas == []
    assert _nada_gravado(caminho)


@pytest.mark.parametrize(
    "recebida_em", ["2026-10-26T12:00:00-03:00", "2026-10-26T15:00:00Z", "2026-10-26T15:00:00+00:00"]
)
def test_recebida_em_com_fuso_e_aceita(novo_cliente, recebida_em):
    assert _post(novo_cliente(Falso.ok()), corpo={**CORPO, "recebida_em": recebida_em}).status_code == 201


def test_recebida_em_e_gravada_em_utc(novo_cliente, caminho):
    _post(novo_cliente(Falso.ok()))
    assert _avaliacao(caminho)["recebida_em"] == "2026-10-26T15:00:00+00:00"


def test_campos_extras_sao_ignorados(novo_cliente):
    assert _post(novo_cliente(Falso.ok()), corpo={**CORPO, "extra": "x"}).status_code == 201


# c) duplicata ------------------------------------------------------------------


def test_mesmo_id_externo_duas_vezes_grava_uma_so_avaliacao_e_um_so_alerta(novo_cliente, caminho):
    cliente = novo_cliente(Falso.ok(criticidade=["saude"]))
    primeira = _post(cliente)
    segunda = _post(cliente)

    assert primeira.status_code == 201
    assert primeira.json()["criada"] is True
    assert segunda.status_code == 200
    assert segunda.json() == {"avaliacao_id": primeira.json()["avaliacao_id"], "criada": False}
    assert _conta(caminho, "avaliacoes") == 1
    assert _conta(caminho, "alertas") == 1


def test_duplicata_nao_chama_o_classificador_de_novo(novo_cliente):
    falso = Falso.ok()
    cliente = novo_cliente(falso)
    _post(cliente)
    _post(cliente)
    assert len(falso.chamadas) == 1


def test_duplicata_e_ignorada_mesmo_com_classificador_fora_do_ar(novo_cliente, caminho):
    primeira = _post(novo_cliente(Falso.ok()))
    segunda = _post(novo_cliente(Falso.com_erro(ErroTecnicoEsgotado("ReadTimeout", None, None))))
    assert segunda.status_code == 200
    assert segunda.json()["avaliacao_id"] == primeira.json()["avaliacao_id"]


def test_duplicata_nao_altera_a_avaliacao_original(novo_cliente, caminho):
    cliente = novo_cliente(Falso.ok())
    _post(cliente)
    _post(cliente, corpo={**CORPO, "texto": "texto diferente", "nota": 5})
    original = _avaliacao(caminho)
    assert original["texto"] == CORPO["texto"]
    assert original["nota"] == 2


def test_duplicata_com_criticidade_nao_cria_alerta_para_avaliacao_sem_alerta(novo_cliente, caminho):
    _post(novo_cliente(Falso.ok()))
    segunda = _post(novo_cliente(Falso.ok(criticidade=["saude"])))
    assert segunda.status_code == 200
    assert _conta(caminho, "alertas") == 0


def test_gravacao_concorrente_do_mesmo_id_externo_devolve_duplicata(novo_cliente, caminho):
    # Outra requisição grava o mesmo id_externo enquanto esta classifica: a consulta prévia
    # não o viu, mas a gravação transacional o encontra e a resposta é de duplicata.
    def outra_requisicao(texto):
        with closing(banco.abrir(caminho)) as c:
            banco.gravar_avaliacao(
                c, id_externo="ext-1", texto="da outra requisição", nota=1,
                recebida_em="2026-10-26T12:00:00-03:00", categorias=["atraso"],
                criticidade=["saude"], status_classificacao="ok",
            )

    r = _post(novo_cliente(Falso.ok(criticidade=["saude"], antes=outra_requisicao)))

    assert r.status_code == 200
    assert r.json()["criada"] is False
    assert _conta(caminho, "avaliacoes") == 1
    assert _conta(caminho, "alertas") == 1
    assert _avaliacao(caminho)["texto"] == "da outra requisição"


# d) classificação ok -----------------------------------------------------------


def test_ok_sem_criticidade_grava_sem_alerta(novo_cliente, caminho):
    r = _post(novo_cliente(Falso.ok(categorias=["atraso", "embalagem"])))

    assert r.status_code == 201
    assert r.json() == {"avaliacao_id": 1, "criada": True, "alerta_id": None}
    av = _avaliacao(caminho)
    assert av["status_classificacao"] == "ok"
    assert av["categorias"] == '["atraso", "embalagem"]'
    assert av["criticidade"] == "[]"
    assert _conta(caminho, "alertas") == 0


def test_ok_com_criticidade_grava_um_unico_alerta_com_todos_os_tipos(novo_cliente, caminho):
    r = _post(novo_cliente(Falso.ok(criticidade=["saude", "alergia"])))

    assert r.status_code == 201
    corpo = r.json()
    assert corpo["criada"] is True
    assert corpo["alerta_id"] is not None
    assert _conta(caminho, "alertas") == 1
    alerta = _alerta(caminho, corpo["avaliacao_id"])
    assert alerta["tipos"] == '["saude", "alergia"]'
    assert alerta["estado"] == "pendente"
    assert alerta["id"] == corpo["alerta_id"]


def test_o_classificador_recebe_so_o_texto(novo_cliente):
    falso = Falso.ok()
    _post(novo_cliente(falso))
    assert falso.chamadas == [CORPO["texto"]]


def test_texto_vazio_e_aceito_e_vai_ao_classificador(novo_cliente):
    falso = Falso.ok(categorias=[])
    r = _post(novo_cliente(falso), corpo={**CORPO, "texto": ""})
    assert r.status_code == 201
    assert falso.chamadas == [""]


# e) saída inválida -------------------------------------------------------------


def test_saida_invalida_grava_como_invalida_sem_alerta(novo_cliente, caminho):
    r = _post(novo_cliente(Falso.invalida()))

    assert r.status_code == 201
    assert r.json() == {"avaliacao_id": 1, "criada": True, "alerta_id": None}
    av = _avaliacao(caminho)
    assert av["status_classificacao"] == "invalida"
    assert av["categorias"] == "[]"
    assert av["criticidade"] == "[]"
    assert _conta(caminho, "alertas") == 0


def test_invalida_tambem_vale_como_duplicata(novo_cliente, caminho):
    falso = Falso.invalida()
    cliente = novo_cliente(falso)
    _post(cliente)
    segunda = _post(cliente)
    assert segunda.status_code == 200
    assert len(falso.chamadas) == 1
    assert _conta(caminho, "avaliacoes") == 1


# f) falhas do classificador ------------------------------------------------------


def test_falha_tecnica_esgotada_devolve_503_e_nao_grava(novo_cliente, caminho):
    r = _post(novo_cliente(Falso.com_erro(ErroTecnicoEsgotado("ReadTimeout", None, None))))
    assert r.status_code == 503
    assert _nada_gravado(caminho)


def test_erro_permanente_devolve_500_e_nao_grava(novo_cliente, caminho):
    r = _post(novo_cliente(Falso.com_erro(ErroPermanenteDaAPI(401, "req_segredo123"))))
    assert r.status_code == 500
    assert _nada_gravado(caminho)


def test_depois_do_503_o_reenvio_e_processado(novo_cliente, caminho):
    r1 = _post(novo_cliente(Falso.com_erro(ErroTecnicoEsgotado("ReadTimeout", 529, "req_x"))))
    r2 = _post(novo_cliente(Falso.ok(criticidade=["alergia"])))
    assert r1.status_code == 503
    assert r2.status_code == 201
    assert r2.json()["criada"] is True
    assert _conta(caminho, "avaliacoes") == 1
    assert _conta(caminho, "alertas") == 1


@pytest.mark.parametrize(
    "erro",
    [
        ErroPermanenteDaAPI(403, "req_segredo123"),
        ErroTecnicoEsgotado("InternalServerError", 529, "req_segredo123"),
        RuntimeError("detalhe-interno-sigiloso"),
        KeyError("detalhe-interno-sigiloso"),
    ],
)
def test_respostas_de_erro_nao_vazam_detalhes(novo_cliente, caplog, erro):
    caplog.set_level(logging.DEBUG)
    r = _post(novo_cliente(Falso.com_erro(erro), raise_server_exceptions=False))
    assert r.status_code in (500, 503)
    for proibido in ("req_segredo123", "detalhe-interno-sigiloso", CHAVE, "Traceback", "Erro", "403", "529"):
        assert proibido not in r.text
    for proibido in ("detalhe-interno-sigiloso", CHAVE):
        assert proibido not in caplog.text


def test_erro_inesperado_devolve_500_sem_gravar(novo_cliente, caminho):
    r = _post(novo_cliente(Falso.com_erro(RuntimeError("boom")), raise_server_exceptions=False))
    assert r.status_code == 500
    assert _nada_gravado(caminho)


def test_falha_do_banco_nao_deixa_gravacao_pela_metade(novo_cliente, caminho, monkeypatch):
    def quebra(*args, **kwargs):
        raise RuntimeError("falha forçada no alerta")

    monkeypatch.setattr(banco, "_inserir_alerta", quebra)
    r = _post(novo_cliente(Falso.ok(criticidade=["saude"]), raise_server_exceptions=False))
    assert r.status_code == 500
    assert "falha forçada" not in r.text
    assert _nada_gravado(caminho)


def test_banco_nao_configurado_devolve_500_sem_detalhes(novo_cliente, monkeypatch):
    monkeypatch.delenv("RADAR_DB", raising=False)
    r = _post(novo_cliente(Falso.ok(), raise_server_exceptions=False))
    assert r.status_code == 500
    assert "RADAR_DB" not in r.text


# classificador real (injeção de produção), sem chamar a API ---------------------------


def test_versao_do_prompt_e_a_congelada():
    assert main.VERSAO_PROMPT == rodar_oficial.VERSAO == "v1"


def test_classificador_de_producao_usa_o_prompt_congelado_e_o_cliente_criado(caminho, monkeypatch):
    chamadas = []

    def classificar_falso(texto, cliente, prompt, **kw):
        chamadas.append((texto, cliente, prompt))
        return {"saida": {"categorias": ["atraso"], "criticidade": []}, "tentativas": 1, "erros": [], "usos": []}

    monkeypatch.setattr(classificador, "criar_cliente", lambda: "CLIENTE-FALSO")
    monkeypatch.setattr(classificador, "classificar", classificar_falso)
    with TestClient(main.criar_app()) as cliente:
        r = _post(cliente)
        _post(cliente, corpo={**CORPO, "id_externo": "ext-2"})

    assert r.status_code == 201
    assert chamadas[0] == (CORPO["texto"], "CLIENTE-FALSO", classificador.carregar_prompt("v1"))
    assert chamadas[1][1] == "CLIENTE-FALSO"


def test_cliente_real_e_criado_uma_so_vez(caminho, monkeypatch):
    criados = []

    def criar():
        criados.append(1)
        return "CLIENTE-FALSO"

    monkeypatch.setattr(classificador, "criar_cliente", criar)
    monkeypatch.setattr(
        classificador, "classificar",
        lambda *a, **k: {"saida": {"categorias": [], "criticidade": []}, "tentativas": 1, "erros": [], "usos": []},
    )
    with TestClient(main.criar_app()) as cliente:
        for i in range(3):
            _post(cliente, corpo={**CORPO, "id_externo": f"ext-{i}"})
    assert criados == [1]


def test_sem_chave_da_anthropic_a_api_devolve_500_e_nao_encerra_o_processo(caminho, monkeypatch):
    def sem_chave():
        raise SystemExit("ANTHROPIC_API_KEY ausente.")

    monkeypatch.setattr(classificador, "criar_cliente", sem_chave)
    with TestClient(main.criar_app(), raise_server_exceptions=False) as cliente:
        r = _post(cliente)
        outra = _post(cliente, chave="chave-errada")
    assert r.status_code == 500
    assert "ANTHROPIC" not in r.text
    assert outra.status_code == 401  # o servidor continua de pé
    assert _nada_gravado(caminho)


# g) persistência -----------------------------------------------------------------


def test_nova_instancia_da_aplicacao_enxerga_os_dados_gravados(caminho):
    def instancia():
        app = main.criar_app()
        app.dependency_overrides[main.obter_classificador] = lambda: Falso.ok(criticidade=["alergia"])
        return TestClient(app)

    with instancia() as primeira:
        r1 = _post(primeira)
    with instancia() as segunda:
        r2 = _post(segunda)

    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r2.json() == {"avaliacao_id": r1.json()["avaliacao_id"], "criada": False}
    assert _avaliacao(caminho)["texto"] == CORPO["texto"]
    assert _alerta(caminho, r1.json()["avaliacao_id"])["tipos"] == '["alergia"]'
    assert _conta(caminho, "avaliacoes") == 1
    assert _conta(caminho, "alertas") == 1


def test_o_banco_e_criado_no_caminho_de_radar_db(caminho, novo_cliente):
    assert not caminho.exists()
    _post(novo_cliente(Falso.ok()))
    assert caminho.exists()
