"""Relatório markdown do ajuste com os 20 de desenvolvimento. Função pura.

Dados simulados. Projeto independente, sem vínculo com o iFood.
Ajuste, não é métrica oficial: os números servem só para orientar o prompt.
"""
import json

from ajuste.rodar_dev import CRITICOS_DEV, FEWSHOT_IDS, MEDICAO_IDS
from app.contrato import CATEGORIAS, CRITICIDADES
from app.metricas import f1_categorias, falsos_alertas, recall_criticos

AVISO_RECALL = "Recall com 2 itens de medição NÃO é evidência de desempenho."

# Claude Sonnet 5.5, preço base por milhão de tokens (consultado em 2026-10-29).
FONTE_PRECO = "https://platform.claude.com/docs/en/about-claude/pricing"
PRECO_ENTRADA_MTOK = 2
PRECO_SAIDA_MTOK = 10


def _num(x):
    return "n/d" if x is None else f"{x:.3f}"


def _lista(rotulos):
    return json.dumps(list(rotulos), ensure_ascii=False)


def _norm(previsao):
    """Forma comparável (ordem não importa); None continua None."""
    if previsao is None:
        return None
    return (tuple(sorted(previsao["categorias"])), tuple(sorted(previsao["criticidade"])))


def _fmt_previsao(previsao):
    if previsao is None:
        return "saída inválida"
    return f"categorias {_lista(previsao['categorias'])}, criticidade {_lista(previsao['criticidade'])}"


def _fmt_gabarito(gab):
    return f"categorias {_lista(gab['categorias'])}, criticidade {_lista(gab['criticidade'])}"


def _subconjuntos(rodada):
    medicao = [i for i in rodada["itens"] if i["id"] in MEDICAO_IDS]
    fewshot = [i for i in rodada["itens"] if i["id"] in FEWSHOT_IDS]
    return [(f"Medição ({len(medicao)})", medicao, True), (f"Few-shot ({len(fewshot)})", fewshot, False)]


def _bloco_metricas(titulo, itens, e_medicao):
    linhas = [f"### {titulo}", ""]
    if not e_medicao:
        linhas += ["Estes itens estão no prompt como exemplos; acerto neles é esperado.", ""]

    gab_cat = [i["gabarito"]["categorias"] for i in itens]
    prev_cat = [None if i["previsao"] is None else i["previsao"]["categorias"] for i in itens]
    f1 = f1_categorias(gab_cat, prev_cat)
    linhas += [
        f"- F1 micro: {_num(f1['micro']['f1'])} "
        f"(precisão {_num(f1['micro']['precisao'])}, recall {_num(f1['micro']['recall'])})",
        f"- F1 macro: {_num(f1['macro_f1'])}",
        f"- categorias_fora_do_macro: {_lista(f1['categorias_fora_do_macro'])}",
        f"- saidas_invalidas: {f1['saidas_invalidas']}",
        "",
        "| Categoria | tp | fp | fn | Precisão | Recall | F1 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in CATEGORIAS:
        m = f1["por_categoria"][c]
        linhas.append(f"| {c} | {m['tp']} | {m['fp']} | {m['fn']} | "
                      f"{_num(m['precisao'])} | {_num(m['recall'])} | {_num(m['f1'])} |")
    linhas.append("")

    com_marcacao = [i for i in itens if i["gabarito"]["criticidade"]]
    sem_marcacao = [i for i in itens if not i["gabarito"]["criticidade"]]
    if com_marcacao:
        r = recall_criticos(
            [i["gabarito"]["criticidade"] for i in com_marcacao],
            [None if i["previsao"] is None else i["previsao"]["criticidade"] for i in com_marcacao],
        )
        linhas.append(f"- Recall dos críticos: detectados {r['detectadas']} de {r['total']} "
                      f"(saídas inválidas entre eles: {r['saidas_invalidas']})")
        for t in CRITICIDADES:
            m = r["por_tipo"][t]
            if m["total"] or m["falsos_positivos"]:
                linhas.append(f"  - {t}: {m['acertos']} de {m['total']}, "
                              f"falsos positivos do tipo: {m['falsos_positivos']}")
        if e_medicao:
            linhas.append(f"- {AVISO_RECALL}")
    if sem_marcacao:
        fa = falsos_alertas(
            [i["gabarito"]["criticidade"] for i in sem_marcacao],
            [None if i["previsao"] is None else i["previsao"]["criticidade"] for i in sem_marcacao],
        )
        linhas.append(f"- Falsos alertas: {fa['falsos_alertas']} de {fa['total_nao_criticas']} "
                      "não críticos")
    linhas.append("")
    return linhas


def _secao_criticos(rodadas):
    linhas = ["## Críticos de desenvolvimento", "",
              "| id | rodada | gabarito | previsão | detectado | tentativas |",
              "| --- | --- | --- | --- | --- | --- |"]
    for id_ in CRITICOS_DEV:
        for rodada in rodadas:
            item = next((i for i in rodada["itens"] if i["id"] == id_), None)
            if item is None:
                continue
            prev = item["previsao"]
            fmt_prev = "saída inválida" if prev is None else _lista(prev["criticidade"])
            detectado = "sim" if prev is not None and prev["criticidade"] else "não"
            linhas.append(f"| {id_} | {rodada['rodada']} | {_lista(item['gabarito']['criticidade'])} | "
                          f"{fmt_prev} | {detectado} | {item['tentativas']} |")
    linhas.append("")
    return linhas


def _secao_variacao(rodadas):
    linhas = ["## Variação entre rodadas", ""]
    ids = [i["id"] for i in rodadas[0]["itens"]] if rodadas else []
    mudaram = []
    for id_ in ids:
        previsoes = []
        for rodada in rodadas:
            item = next((i for i in rodada["itens"] if i["id"] == id_), None)
            previsoes.append(None if item is None else item["previsao"])
        if len({_norm(p) for p in previsoes}) > 1:
            mudaram.append((id_, previsoes))
    if not mudaram:
        linhas += ["Nenhuma previsão mudou entre as rodadas.", ""]
        return linhas
    for id_, previsoes in mudaram:
        linhas.append(f"- **{id_}**")
        for n, p in enumerate(previsoes, start=1):
            linhas.append(f"  - rodada {n}: {_fmt_previsao(p)}")
    linhas.append("")
    return linhas


def _secao_erros(rodadas):
    linhas = ["## Erros", "",
              "Itens com previsão diferente do gabarito (categorias ou criticidade) ou inválida.", ""]
    for rodada in rodadas:
        for titulo, itens, _ in _subconjuntos(rodada):
            nome = titulo.split(" (")[0]
            linhas += [f"### Rodada {rodada['rodada']}, {nome}", ""]
            errados = [i for i in itens if _norm(i["previsao"]) != _norm(i["gabarito"])]
            if not errados:
                linhas += ["Nenhum erro.", ""]
                continue
            for i in errados:
                linhas += [
                    f"- **{i['id']}** (tentativas: {i['tentativas']})",
                    f"  - texto: {json.dumps(i['texto'], ensure_ascii=False)}",
                    f"  - gabarito: {_fmt_gabarito(i['gabarito'])}",
                    f"  - previsão: {_fmt_previsao(i['previsao'])}",
                ]
            linhas.append("")
    return linhas


def _secao_tokens(rodadas):
    linhas = ["## Tokens e custo estimado", ""]
    usos = [u for r in rodadas for i in r["itens"] for u in i.get("usos", [])]
    completos = [u for u in usos
                 if u.get("input_tokens") is not None and u.get("output_tokens") is not None]
    if not completos:
        linhas += ["Tokens: não registrado; custo no Console.", ""]
        return linhas
    entrada = sum(u["input_tokens"] for u in completos)
    saida = sum(u["output_tokens"] for u in completos)
    custo = entrada * PRECO_ENTRADA_MTOK / 1_000_000 + saida * PRECO_SAIDA_MTOK / 1_000_000
    linhas += [
        f"- Respostas com uso registrado: {len(completos)}",
        f"- Tokens de entrada: {entrada}",
        f"- Tokens de saída: {saida}",
        f"- Custo estimado: US$ {custo:.4f} (entrada a US$ {PRECO_ENTRADA_MTOK} / MTok, "
        f"saída a US$ {PRECO_SAIDA_MTOK} / MTok)",
        f"- Fonte do preço: {FONTE_PRECO}",
        "- Estimado: tentativas que terminaram em exceção não têm uso registrado; "
        "o custo real está no Console.",
        "",
    ]
    return linhas


def gerar_relatorio(versao, rodadas, metadados):
    """Monta o markdown do ajuste. Não lê nem grava arquivos."""
    total_chamadas = sum(i["tentativas"] for r in rodadas for i in r["itens"])
    linhas = [
        f"# Relatório de ajuste do classificador: {versao}",
        "",
        "**Ajuste, não é métrica oficial.** Resultados sobre os 20 de desenvolvimento "
        "(13 de medição e 7 few-shot). Dados simulados.",
        "",
        "## Metadados",
        "",
        f"- Versão do prompt: {versao}",
        f"- Data: {metadados.get('data')}",
        f"- Modelo: {metadados.get('modelo')}",
        f"- Parâmetros: `{json.dumps(metadados.get('parametros'), ensure_ascii=False)}`",
        f"- Rodadas: {len(rodadas)}",
        "- sem temperature/top_p/top_k (rejeitados pelo modelo); variação medida por repetição",
        f"- Total de chamadas feitas: {total_chamadas}",
        "",
    ]
    linhas += _secao_tokens(rodadas)
    for rodada in rodadas:
        linhas += [f"## Rodada {rodada['rodada']}", ""]
        for titulo, itens, e_medicao in _subconjuntos(rodada):
            linhas += _bloco_metricas(titulo, itens, e_medicao)
    linhas += _secao_criticos(rodadas)
    linhas += _secao_variacao(rodadas)
    linhas += _secao_erros(rodadas)
    return "\n".join(linhas)
