"""Configuração fixa e função de chamada compartilhadas pelos três experimentos.

Dados fictícios. Projeto independente, sem vínculo com o iFood.

Regras de segurança deste módulo:
- a chave vem só do .env (ANTHROPIC_API_KEY) e nunca é lida, impressa nem gravada aqui;
- erros gravam só tipo, status HTTP e identificador da requisição, nunca a mensagem
  bruta da exceção nem cabeçalhos;
- nenhuma chamada é repetida automaticamente (max_retries=0 e nenhum laço de nova tentativa).
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
SAIDAS = Path(__file__).resolve().parent / "saidas"

PROVEDOR = "Anthropic"
MODELO = "claude-sonnet-5-5"

# Parâmetros fixos nas 8 chamadas. temperature, top_p e top_k NÃO são enviados:
# este modelo os rejeita, e nenhum valor padrão é presumido.
PARAMETROS = {
    "max_retries": 0,
    "max_tokens": 400,
    "thinking": {"type": "between_tools"},
    "output_config": {"effort": "medium"},
    "temperature": "omitido",
    "top_p": "omitido",
    "top_k": "omitido",
}


def criar_cliente():
    load_dotenv(RAIZ / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY ausente. Defina-a no .env (o valor nunca é exibido).")
    return anthropic.Anthropic(max_retries=0)


def chamar(cliente, rotulo, mensagens):
    """Faz UMA chamada independente e devolve um registro serializável.

    Não levanta exceção de API: o erro vira registro com valido=False.
    Cada chamada envia apenas as `mensagens` recebidas (API sem estado).
    """
    registro = {
        "rotulo": rotulo,
        "data_hora_utc": datetime.now(timezone.utc).isoformat(),
        "provedor": PROVEDOR,
        "modelo": MODELO,
        "parametros": PARAMETROS,
        "mensagens_enviadas": mensagens,
    }
    try:
        resposta = cliente.messages.create(
            model=MODELO,
            max_tokens=PARAMETROS["max_tokens"],
            thinking=PARAMETROS["thinking"],
            output_config=PARAMETROS["output_config"],
            messages=mensagens,
        )
    except anthropic.APIStatusError as e:
        registro.update(
            valido=False,
            erro={"tipo": type(e).__name__, "status": e.status_code, "request_id": e.request_id},
        )
        return registro
    except anthropic.APIError as e:
        # Sem resposta HTTP (conexão, tempo esgotado): só o tipo da exceção.
        registro.update(valido=False, erro={"tipo": type(e).__name__, "status": None, "request_id": None})
        return registro

    tipos = [bloco.type for bloco in resposta.content]
    textos = [bloco.text for bloco in resposta.content if bloco.type == "text"]
    registro.update(
        request_id=getattr(resposta, "_request_id", None),
        stop_reason=resposta.stop_reason,
        tipos_de_bloco=tipos,
        apareceu_bloco_de_pensamento=any("thinking" in t for t in tipos),
        blocos_de_texto=textos,
        texto="".join(textos),
        tokens={
            "entrada": resposta.usage.input_tokens,
            "saida": resposta.usage.output_tokens,
        },
    )
    # Válido = terminou sozinha (end_turn) e trouxe ao menos um bloco de texto.
    registro["valido"] = resposta.stop_reason == "end_turn" and bool(textos)
    return registro


def exigir_validas(registros):
    """Para o experimento se alguma chamada foi erro ou resposta incompleta."""
    for r in registros:
        if r["valido"]:
            continue
        if "erro" in r:
            e = r["erro"]
            msg = f"{r['rotulo']}: erro {e['tipo']} (status {e['status']}, request_id {e['request_id']})."
            if e["status"] == 400:
                msg += " Erro 400: nada foi repetido nem alterado; avise o responsável."
        else:
            msg = f"{r['rotulo']}: resposta inválida (stop_reason={r['stop_reason']}). Não será repetida."
        print(msg, file=sys.stderr)
        return False
    return True


def preparar_saida(nome):
    """Recusa rodar de novo se a saída já existe: evita gasto repetido sem querer."""
    SAIDAS.mkdir(exist_ok=True)
    caminho = SAIDAS / f"{nome}.json"
    if caminho.exists():
        sys.exit(f"{caminho.name} já existe. Apague-o manualmente se quiser rodar de novo (nova chamada paga).")
    return caminho


def salvar(caminho, dados):
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
