"""Testes dos workflows do n8n (Fase 3, critério 6).

Fonte da verdade: docs/ESPECIFICACAO.md (rotas GET /resumo-semanal e POST /alertas/reenviar, decisão do
resumo semanal e do reenvio de alertas). Os testes só leem os JSON de n8n/ e docs/N8N.md: não há n8n nem rede.
A importação real no n8n não é coberta aqui.
"""

import json
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
PASTA = RAIZ / "n8n"
DOC = RAIZ / "docs" / "N8N.md"

URL_BASE = "http://localhost:8000"
URL_TELEGRAM = "https://api.telegram.org/bot<TOKEN_DO_BOT>/sendMessage"

# Formato de token de bot do Telegram (pedido do Breno) e de chaves comuns de API.
PADROES_DE_SEGREDO = [
    re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    # Link do painel com token (secrets.token_urlsafe(32) tem 43 caracteres): vem de um export com dados fixados.
    re.compile(r"/painel/[A-Za-z0-9_-]{40,}"),
]


def _carrega(nome):
    return json.loads((PASTA / nome).read_text(encoding="utf-8"))


def _no(fluxo, tipo):
    return [n for n in fluxo["nodes"] if n["type"] == tipo]


def _destinos(fluxo, origem, saida=0):
    """Nomes dos nós ligados à saída `saida` de `origem` ([] se a saída não existe ou está vazia)."""
    principal = fluxo["connections"].get(origem, {}).get("main", [])
    if saida >= len(principal):
        return []
    return [c["node"] for c in principal[saida]]


@pytest.fixture(scope="module")
def semanal():
    return _carrega("semanal.json")


@pytest.fixture(scope="module")
def reenvio():
    return _carrega("reenvio.json")


# --- Comum aos dois -------------------------------------------------------------------------------------


@pytest.mark.parametrize("nome", ["semanal.json", "reenvio.json"])
def test_estrutura_do_json_consistente(nome):
    # Só estrutura (nomes, ids, ligações): a importação real no n8n não é coberta aqui.
    fluxo = _carrega(nome)
    assert fluxo["name"]
    assert fluxo["nodes"] and isinstance(fluxo["connections"], dict)
    nomes = [n["name"] for n in fluxo["nodes"]]
    assert len(nomes) == len(set(nomes)), "nomes de nó repetidos"
    ids = [n["id"] for n in fluxo["nodes"]]
    assert len(ids) == len(set(ids)), "ids de nó repetidos"
    for origem, saidas in fluxo["connections"].items():
        assert origem in nomes
        for grupo in saidas["main"]:
            for ligacao in grupo:
                assert ligacao["node"] in nomes
    assert fluxo.get("active") is False, "o workflow deve entrar desativado; o Breno ativa depois de configurar"
    # Um export feito depois de executar traria dados fixados (e o link do painel, válido por 7 dias).
    assert fluxo.get("pinData") == {}


@pytest.mark.parametrize("nome", ["semanal.json", "reenvio.json"])
def test_fuso_do_workflow_e_sao_paulo(nome):
    assert _carrega(nome)["settings"]["timezone"] == "America/Sao_Paulo"


@pytest.mark.parametrize("nome", ["semanal.json", "reenvio.json"])
def test_sem_bloco_credentials_e_sem_valor_secreto(nome):
    texto = (PASTA / nome).read_text(encoding="utf-8")
    fluxo = json.loads(texto)
    for no in fluxo["nodes"]:
        assert "credentials" not in no, f"nó {no['name']} traz credentials; elas se escolhem dentro do n8n"
    for padrao in PADROES_DE_SEGREDO:
        assert not padrao.search(texto), f"{nome} contém algo no formato de segredo ({padrao.pattern})"


def test_detector_de_token_funciona():
    # Garante que a checagem acima não é vacuamente verdadeira. O token falso é montado aqui, não é real.
    falso = "123456789:" + "A" * 35
    assert PADROES_DE_SEGREDO[0].search(f"https://api.telegram.org/bot{falso}/sendMessage")
    assert not PADROES_DE_SEGREDO[0].search(URL_TELEGRAM)


def test_nenhum_json_de_n8n_fora_dos_dois_esperados():
    # Um export colado por engano (com token) não pode entrar na pasta sem ser notado.
    assert sorted(p.name for p in PASTA.glob("*.json")) == ["reenvio.json", "semanal.json"]
    for p in PASTA.glob("*.json"):
        texto = p.read_text(encoding="utf-8")
        for padrao in PADROES_DE_SEGREDO:
            assert not padrao.search(texto)


def test_documentacao_sem_segredo():
    texto = DOC.read_text(encoding="utf-8")
    for padrao in PADROES_DE_SEGREDO:
        assert not padrao.search(texto), f"docs/N8N.md contém algo no formato de segredo ({padrao.pattern})"


@pytest.mark.parametrize("nome", ["semanal.json", "reenvio.json"])
def test_chamadas_ao_radar_usam_api_key_do_n8n(nome):
    # O segredo da API é uma credencial "Header Auth" escolhida dentro do n8n; nada no JSON.
    fluxo = _carrega(nome)
    chamadas = [n for n in _no(fluxo, "n8n-nodes-base.httpRequest") if n["parameters"]["url"].startswith(URL_BASE)]
    assert chamadas
    for no in chamadas:
        p = no["parameters"]
        assert p["authentication"] == "genericCredentialType"
        assert p["genericAuthType"] == "httpHeaderAuth"
        assert "X-API-Key" not in json.dumps(p), "o nome do cabeçalho vai na credencial, não no nó"


# --- semanal.json ---------------------------------------------------------------------------------------


def test_semanal_agenda_segunda_as_8h(semanal):
    (gatilho,) = _no(semanal, "n8n-nodes-base.scheduleTrigger")
    intervalos = gatilho["parameters"]["rule"]["interval"]
    assert intervalos == [{"field": "cronExpression", "expression": "0 8 * * 1"}]


def test_semanal_busca_o_resumo_com_get(semanal):
    (gatilho,) = _no(semanal, "n8n-nodes-base.scheduleTrigger")
    busca = [n for n in _no(semanal, "n8n-nodes-base.httpRequest") if n["parameters"]["url"].endswith("/resumo-semanal")]
    assert len(busca) == 1
    p = busca[0]["parameters"]
    assert p["url"] == f"{URL_BASE}/resumo-semanal"
    assert p.get("method", "GET") == "GET"
    assert _destinos(semanal, gatilho["name"]) == [busca[0]["name"]]


def test_semanal_if_so_envia_quando_enviar_for_true(semanal):
    (se,) = _no(semanal, "n8n-nodes-base.if")
    (condicao,) = se["parameters"]["conditions"]["conditions"]
    assert condicao["leftValue"] == "={{ $json.enviar }}"
    assert condicao["operator"] == {"type": "boolean", "operation": "true", "singleValue": True}
    assert se["parameters"]["conditions"]["options"]["typeValidation"] == "strict"
    busca = next(n for n in _no(semanal, "n8n-nodes-base.httpRequest") if n["parameters"]["url"].endswith("/resumo-semanal"))
    assert _destinos(semanal, busca["name"]) == [se["name"]]
    # Saída "true" vai para o envio; a saída "false" não tem nada ligado: o fluxo termina sem enviar.
    envio = [n for n in semanal["nodes"] if n["parameters"].get("url") == URL_TELEGRAM]
    assert len(envio) == 1
    assert _destinos(semanal, se["name"], 0) == [envio[0]["name"]]
    assert _destinos(semanal, se["name"], 1) == []


def test_semanal_telegram_sem_parse_mode_e_sem_preview(semanal):
    # O nó "Telegram" nativo do n8n sempre envia parse_mode=Markdown quando vazio (GenericFunctions.ts);
    # por isso o envio é um HTTP Request à API, com o corpo montado aqui.
    assert not _no(semanal, "n8n-nodes-base.telegram")
    (envio,) = [n for n in semanal["nodes"] if n["parameters"].get("url") == URL_TELEGRAM]
    assert envio["type"] == "n8n-nodes-base.httpRequest"
    p = envio["parameters"]
    assert p["method"] == "POST"
    assert p["authentication"] == "none", "a chave da API do Radar não pode ir para o Telegram"
    assert p["specifyBody"] == "json"
    corpo = p["jsonBody"]
    assert re.search(r"text:\s*\$json\.mensagem", corpo)
    assert re.search(r'chat_id:\s*"<CHAT_ID>"', corpo)
    assert re.search(r"disable_web_page_preview:\s*true", corpo)
    assert not re.search(r"parse_mode", json.dumps(semanal), re.IGNORECASE)


def test_semanal_so_usa_os_dois_hosts_esperados(semanal):
    urls = [n["parameters"]["url"] for n in _no(semanal, "n8n-nodes-base.httpRequest")]
    assert sorted(urls) == sorted([f"{URL_BASE}/resumo-semanal", URL_TELEGRAM])


# --- reenvio.json ---------------------------------------------------------------------------------------


def test_reenvio_agenda_a_cada_hora(reenvio):
    (gatilho,) = _no(reenvio, "n8n-nodes-base.scheduleTrigger")
    assert gatilho["parameters"]["rule"]["interval"] == [{"field": "cronExpression", "expression": "0 * * * *"}]


def test_reenvio_so_chama_a_rota_de_reenvio_com_post(reenvio):
    # Sem nenhum outro efeito: só o gatilho e uma chamada, sem nada depois dela.
    assert sorted(n["type"] for n in reenvio["nodes"]) == [
        "n8n-nodes-base.httpRequest",
        "n8n-nodes-base.scheduleTrigger",
    ]
    (chamada,) = _no(reenvio, "n8n-nodes-base.httpRequest")
    (gatilho,) = _no(reenvio, "n8n-nodes-base.scheduleTrigger")
    assert chamada["parameters"]["url"] == f"{URL_BASE}/alertas/reenviar"
    assert chamada["parameters"]["method"] == "POST"
    assert _destinos(reenvio, gatilho["name"]) == [chamada["name"]]
    assert _destinos(reenvio, chamada["name"]) == []
    assert not re.search(r"telegram|parse_mode", json.dumps(reenvio), re.IGNORECASE)


# --- Contrato com o app ---------------------------------------------------------------------------------


def test_rotas_chamadas_existem_no_app():
    # Liga os JSON ao FastAPI: se uma rota mudar de caminho ou método, este teste falha.
    # Os campos `enviar` e `mensagem` da resposta são cobertos por tests/test_resumo_semanal.py.
    from app import main

    # Pelo OpenAPI: as rotas do roteador com chave ficam dentro de um roteador incluído, fora de app.routes.
    caminhos = main.criar_app().openapi()["paths"]
    assert "get" in caminhos["/resumo-semanal"]
    assert "post" in caminhos["/alertas/reenviar"]
