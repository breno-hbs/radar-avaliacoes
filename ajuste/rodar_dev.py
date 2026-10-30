"""Ajuste do classificador com os 20 de desenvolvimento. NÃO é métrica oficial.

Dados simulados. Projeto independente, sem vínculo com o iFood.

Este script só lê o arquivo de desenvolvimento (ARQUIVO_DEV). Os conjuntos de avaliação não são
abertos aqui, nem por nome. Os 20 itens se dividem em:
- FEWSHOT_IDS: os 7 embutidos como exemplos no prompt (acerto neles é esperado);
- MEDICAO_IDS: os 13 restantes, usados para medir o ajuste.

Uso (faz chamadas pagas à API; só rodar quando decidido):
    python -m ajuste.rodar_dev --versao v0 [--rodadas 3]
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.classificador import PARAMETROS, carregar_prompt, classificar, criar_cliente

RAIZ = Path(__file__).resolve().parent.parent
NOME_DEV = "desenvolvimento.json"
ARQUIVO_DEV = RAIZ / "data" / NOME_DEV
RELATORIOS = Path(__file__).resolve().parent / "relatorios"

FEWSHOT_IDS = ["dev-008", "dev-009", "dev-010", "dev-015", "dev-017", "dev-013", "dev-020"]
MEDICAO_IDS = ["dev-001", "dev-002", "dev-003", "dev-004", "dev-005", "dev-006", "dev-007",
               "dev-011", "dev-012", "dev-014", "dev-016", "dev-018", "dev-019"]
CRITICOS_DEV = ["dev-014", "dev-015", "dev-016"]


def carregar_desenvolvimento(caminho=ARQUIVO_DEV):
    """Lê o conjunto de desenvolvimento. Recusa outro arquivo ANTES de abrir."""
    caminho = Path(caminho)
    if caminho.name != NOME_DEV:
        raise ValueError("só o conjunto de desenvolvimento pode ser usado no ajuste")
    avaliacoes = json.loads(caminho.read_text(encoding="utf-8"))
    if not isinstance(avaliacoes, list):
        raise ValueError("o arquivo de desenvolvimento deve conter uma lista")
    for a in avaliacoes:
        if a.get("conjunto") != "desenvolvimento":
            raise ValueError("item fora do conjunto de desenvolvimento")
    return avaliacoes


def separar(avaliacoes):
    """Devolve (fewshot, medicao) na ordem das constantes. Falha se a partição não fecha."""
    if set(FEWSHOT_IDS) & set(MEDICAO_IDS):
        raise ValueError("few-shot e medição se sobrepõem")
    ids = [a["id_externo"] for a in avaliacoes]
    if len(ids) != len(set(ids)):
        raise ValueError("id repetido no desenvolvimento")
    esperados = set(FEWSHOT_IDS) | set(MEDICAO_IDS)
    if set(ids) != esperados or len(ids) != 20:
        raise ValueError("few-shot e medição não cobrem exatamente os 20 de desenvolvimento")
    por_id = {a["id_externo"]: a for a in avaliacoes}
    return [por_id[i] for i in FEWSHOT_IDS], [por_id[i] for i in MEDICAO_IDS]


def rodar(avaliacoes, cliente, prompt, rodadas=3):
    """Classifica cada avaliação `rodadas` vezes. O classificador recebe só o texto."""
    resultado = []
    for n in range(1, rodadas + 1):
        itens = []
        for a in avaliacoes:
            r = classificar(a["texto"], cliente, prompt)
            itens.append({
                "id": a["id_externo"],
                "texto": a["texto"],
                "gabarito": {"categorias": a["categorias"], "criticidade": a["criticidade"]},
                "previsao": r["saida"],
                "tentativas": r["tentativas"],
                "erros": r["erros"],
                "usos": r["usos"],
            })
        resultado.append({"rodada": n, "itens": itens})
    return resultado


def agora_sao_paulo():
    """Data e hora ISO com offset de America/Sao_Paulo (UTC-3, sem horário de verão desde 2019).

    Offset fixo em vez de zoneinfo: no Windows o zoneinfo precisaria do pacote tzdata.
    """
    return datetime.now(timezone(timedelta(hours=-3))).isoformat(timespec="seconds")


def _salvar(caminho, conteudo):
    caminho.write_text(conteudo, encoding="utf-8")


def main(argv=None):
    from ajuste.relatorio import gerar_relatorio  # import tardio: relatorio importa este módulo

    parser = argparse.ArgumentParser(description="Ajuste com os 20 de desenvolvimento.")
    parser.add_argument("--versao", required=True, help="versão do prompt, ex.: v0")
    parser.add_argument("--rodadas", type=int, default=3)
    args = parser.parse_args(argv)
    if args.rodadas < 1:
        sys.exit("--rodadas deve ser pelo menos 1.")

    prompt = carregar_prompt(args.versao)
    avaliacoes = carregar_desenvolvimento()
    separar(avaliacoes)  # valida a partição antes de gastar qualquer chamada

    # Mesma extensão do arquivo de desenvolvimento para as saídas brutas.
    brutos = [RELATORIOS / f"{args.versao}_rodada{n}{ARQUIVO_DEV.suffix}"
              for n in range(1, args.rodadas + 1)]
    resumo = RELATORIOS / f"{args.versao}.md"
    existentes = [p.name for p in brutos + [resumo] if p.exists()]
    if existentes:
        sys.exit(f"Já existe: {', '.join(existentes)}. Apague manualmente para rodar de novo "
                 "(novas chamadas pagas).")

    cliente = criar_cliente()
    rodadas = rodar(avaliacoes, cliente, prompt, rodadas=args.rodadas)

    RELATORIOS.mkdir(exist_ok=True)
    for caminho, rodada in zip(brutos, rodadas):
        _salvar(caminho, json.dumps(rodada, ensure_ascii=False, indent=2))
    metadados = {
        "data": agora_sao_paulo(),
        "modelo": PARAMETROS["model"],
        "parametros": PARAMETROS,
    }
    _salvar(resumo, gerar_relatorio(args.versao, rodadas, metadados))
    print(f"Relatórios gravados em {RELATORIOS}")


if __name__ == "__main__":
    main()
