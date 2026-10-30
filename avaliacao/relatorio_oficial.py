"""Relatório da avaliação oficial (markdown e dados equivalentes). Função pura.

Dados simulados. Projeto independente, sem vínculo com o iFood.

Não lê arquivos, não usa rede e não importa o executor: recebe a `execucao` pronta (um dict) e
devolve (markdown, dados). Reporta as três rodadas e a média; nunca escolhe uma rodada.

Forma de `execucao`:
    versao, hashes (dict, com os hashes do código, da regra de retentativa e dos dois conjuntos de
    dados), git_head, iniciado_em, concluido_em (ISO com -03:00), modelo,
    parametros (dict), abortadas [{pasta, causa, motivo}] e
    rodadas [{rodada, itens: [{conjunto, id, texto, gabarito{categorias, criticidade},
    previsao dict|None, tentativas, erros [{tentativa, tipo, ...}],
    usos [{tentativa, input_tokens, output_tokens}]}]}].
Opcionais, só quando o relatório é gerado a partir do diário (--so-relatorio):
    gerado_por_so_relatorio (bool) e git_head_atual (o HEAD no momento de gerar).
Os nomes dos conjuntos vêm por parâmetro (padrão: as constantes abaixo).

Saída inválida = previsão None (em gerais E críticos). Por item inválido, o relatório lista o TIPO
de erro de cada tentativa (campo `erros[].tipo`), nunca a mensagem nem o corpo.
"""
import copy
import json

from ajuste.relatorio import FONTE_PRECO, PRECO_ENTRADA_MTOK, PRECO_SAIDA_MTOK
from app.contrato import CATEGORIAS, CRITICIDADES
from app.metricas import f1_categorias, falsos_alertas, recall_criticos

CONJUNTO_GERAL = "geral"
CONJUNTO_CRITICO = "critico"
N_RODADAS = 3
# Data em que o preço por MTok (importado de ajuste.relatorio) foi consultado na fonte.
DATA_CONSULTA_PRECO = "2026-10-29"

AVISO = "Dados simulados. Projeto independente, sem vínculo com o iFood."
MARCA_SO_RELATORIO = ("Gerado por --so-relatorio, a partir do diário da execução já concluída "
                      "(nenhuma chamada à API foi feita). Commit atual (git HEAD): {head}.")
FRASE_NENHUMA_ESCOLHIDA = "Reportadas as três rodadas e a média; nenhuma rodada foi escolhida."

RESSALVAS = [
    "Amostra pequena: 50 gerais e 15 críticos. Os números avaliam o protótipo; não garantem "
    "desempenho em produção e carregam muita incerteza.",
    "Rotulador único: as classificações de referência foram revisadas manualmente por uma só "
    "pessoa; não há medida de concordância entre rotuladores.",
    "As 85 avaliações fictícias foram criadas com apoio de IA e nenhuma foi escrita à mão. Se a "
    "mesma IA, ou uma parecida, gera os dados e classifica, há risco de acerto inflado; isso fica "
    "declarado como limitação aberta.",
    "Amostra crítica enriquecida: os 15 casos críticos foram escolhidos para medir detecção e "
    "a amostra não representa a frequência real de relatos críticos.",
    "O conjunto crítico não é totalmente independente da definição dos critérios: o caso crit-007 "
    "motivou a regra de acúmulo da versão 1.1 do guia (2026-10-28), antes de qualquer execução do "
    "classificador. Há também exposição ao crit-009: a primeira redação do prompt trazia uma "
    "cláusula derivada desse caso, removida antes de qualquer medição oficial.",
    "As 85 avaliações demonstram o processo de avaliação do classificador e não comprovam "
    "desempenho com clientes reais.",
    "Convenção de rotulação: o pedido que \"nunca chegou\" é classificado como atraso.",
    "Alergia de terceiro (pedido de outra pessoa que é alérgica) fica por interpretação do "
    "modelo: o prompt não tem cláusula sobre isso.",
    "Saídas inválidas devem ser citadas na mesma frase dos falsos alertas: uma saída inválida "
    "vale como previsão vazia e pode esconder um falso alerta.",
]


# ---------- formatação ----------

def _dec(x, casas=3):
    """Número com vírgula decimal; None vira n/d."""
    return "n/d" if x is None else f"{x:.{casas}f}".replace(".", ",")


def _lista(rotulos):
    return json.dumps(list(rotulos), ensure_ascii=False)


def _norm(previsao):
    """Forma comparável (a ordem das listas não importa); None continua None."""
    if previsao is None:
        return None
    return (tuple(sorted(previsao["categorias"])), tuple(sorted(previsao["criticidade"])))


def _fmt_previsao(previsao):
    if previsao is None:
        return "saída inválida"
    return f"categorias {_lista(previsao['categorias'])}, criticidade {_lista(previsao['criticidade'])}"


def _fmt_gabarito(gab):
    return f"categorias {_lista(gab['categorias'])}, criticidade {_lista(gab['criticidade'])}"


def _campo(item, campo):
    """Lista de rótulos da previsão (None se a saída foi inválida)."""
    return None if item["previsao"] is None else item["previsao"][campo]


def _media(valores):
    """Média dos valores definidos e quantos entraram."""
    definidos = [v for v in valores if v is not None]
    return {"media": sum(definidos) / len(definidos) if definidos else None,
            "rodadas": len(definidos)}


# ---------- métricas por rodada e médias ----------

def _metricas_da_rodada(rodada, geral, critico):
    gerais = [i for i in rodada["itens"] if i["conjunto"] == geral]
    criticos = [i for i in rodada["itens"] if i["conjunto"] == critico]
    if not gerais or not criticos:
        raise ValueError(f"a rodada {rodada['rodada']} precisa de itens dos dois conjuntos")
    f1 = f1_categorias([i["gabarito"]["categorias"] for i in gerais],
                       [_campo(i, "categorias") for i in gerais])
    alertas = falsos_alertas([i["gabarito"]["criticidade"] for i in gerais],
                             [_campo(i, "criticidade") for i in gerais])
    recall = recall_criticos([i["gabarito"]["criticidade"] for i in criticos],
                             [_campo(i, "criticidade") for i in criticos])
    return {
        "rodada": rodada["rodada"],
        "gerais": {"total": len(gerais), "f1": f1, "falsos_alertas": alertas},
        "criticos": recall,
    }


def _medias(metricas):
    gerais = [m["gerais"] for m in metricas]
    criticos = [m["criticos"] for m in metricas]
    n = len(metricas)

    def media_simples(valores):
        return sum(valores) / len(valores)

    por_tipo = {}
    for t in CRITICIDADES:
        por_tipo[t] = {
            "acertos_media": media_simples([c["por_tipo"][t]["acertos"] for c in criticos]),
            "total": criticos[0]["por_tipo"][t]["total"],
            "falsos_positivos_media": media_simples(
                [c["por_tipo"][t]["falsos_positivos"] for c in criticos]),
        }
    return {
        "rodadas": n,
        "gerais": {
            "f1_micro": _media([g["f1"]["micro"]["f1"] for g in gerais]),
            "f1_macro": _media([g["f1"]["macro_f1"] for g in gerais]),
            "por_categoria": {c: _media([g["f1"]["por_categoria"][c]["f1"] for g in gerais])
                              for c in CATEGORIAS},
            "falsos_alertas": {
                "media": media_simples([g["falsos_alertas"]["falsos_alertas"] for g in gerais]),
                "total_nao_criticas": gerais[0]["falsos_alertas"]["total_nao_criticas"],
                "saidas_invalidas_media": media_simples(
                    [g["falsos_alertas"]["saidas_invalidas"] for g in gerais]),
            },
        },
        "criticos": {
            "detectadas": _media([c["detectadas"] for c in criticos]),
            "total": criticos[0]["total"],
            "saidas_invalidas_media": media_simples([c["saidas_invalidas"] for c in criticos]),
            "por_tipo": por_tipo,
        },
    }


# ---------- variação, erros, tokens ----------

def _chaves(rodada):
    return [(i["conjunto"], i["id"]) for i in rodada["itens"]]


def _variacao(rodadas):
    por_rodada = [{(i["conjunto"], i["id"]): i for i in r["itens"]} for r in rodadas]
    base = _chaves(rodadas[0])
    for r, mapa in zip(rodadas, por_rodada):
        if set(mapa) != set(base):
            raise ValueError(f"a rodada {r['rodada']} não tem os mesmos itens da primeira")
    mudaram = []
    for chave in base:
        previsoes = [mapa[chave]["previsao"] for mapa in por_rodada]
        if len({_norm(p) for p in previsoes}) > 1:
            mudaram.append({"conjunto": chave[0], "id": chave[1],
                            "previsoes": copy.deepcopy(previsoes)})
    return mudaram


def _erros(rodadas, geral, critico):
    saida = []
    for r in rodadas:
        entrada = {"rodada": r["rodada"], "gerais": [], "criticos": []}
        for item in r["itens"]:
            if _norm(item["previsao"]) == _norm(item["gabarito"]):
                continue
            registro = {"id": item["id"], "texto": item["texto"],
                        "gabarito": copy.deepcopy(item["gabarito"]),
                        "previsao": copy.deepcopy(item["previsao"]),
                        "tentativas": item["tentativas"]}
            if item["previsao"] is None:
                # Saída inválida: o tipo de erro de cada tentativa (só tentativa e tipo).
                registro["tipos_de_erro"] = [
                    {"tentativa": e.get("tentativa"), "tipo": e.get("tipo")}
                    for e in (item.get("erros") or [])]
            if item["conjunto"] == geral:
                entrada["gerais"].append(registro)
            elif item["conjunto"] == critico:
                entrada["criticos"].append(registro)
        saida.append(entrada)
    return saida


def _tokens(rodadas):
    usos = [u for r in rodadas for i in r["itens"] for u in (i.get("usos") or [])]
    entradas = [u.get("input_tokens") for u in usos if u.get("input_tokens") is not None]
    saidas = [u.get("output_tokens") for u in usos if u.get("output_tokens") is not None]
    registrado = bool(entradas or saidas)
    entrada, saida = sum(entradas), sum(saidas)
    return {
        "usos_total": len(usos),
        "usos_com_entrada": len(entradas),
        "usos_com_saida": len(saidas),
        "entrada": entrada if registrado else None,
        "saida": saida if registrado else None,
        "custo_estimado_usd": (entrada * PRECO_ENTRADA_MTOK / 1_000_000
                               + saida * PRECO_SAIDA_MTOK / 1_000_000) if registrado else None,
        "preco_entrada_mtok": PRECO_ENTRADA_MTOK,
        "preco_saida_mtok": PRECO_SAIDA_MTOK,
        "fonte_preco": FONTE_PRECO,
        "data_consulta_preco": DATA_CONSULTA_PRECO,
    }


def _parametros_resumidos(parametros):
    """Parâmetros sem o schema (que é longo e já tem hash próprio)."""
    resumo = copy.deepcopy(parametros)
    saida = resumo.get("output_config")
    if isinstance(saida, dict):
        saida.pop("format", None)
    return resumo


# ---------- markdown ----------

def _md_metadados(e, total_chamadas, extras):
    h = e.get("hashes") or {}
    return [
        "## Metadados", "",
        f"- Versão do prompt: {e['versao']}",
        f"- SHA-256 do prompt: {h.get('prompt_sha256')}",
        f"- SHA-256 do schema de saída: {h.get('schema_sha256')}",
        f"- SHA-256 dos parâmetros da chamada: {h.get('parametros_sha256')}",
        f"- SHA-256 da regra de retentativa: {h.get('regra_retentativa_sha256')}",
        f"- SHA-256 dos dados gerais: {h.get('dados_geral_sha256')}",
        f"- SHA-256 dos dados críticos: {h.get('dados_critico_sha256')}",
        f"- Modelo: {e['modelo']}",
        f"- SDK: anthropic {h.get('anthropic')} (python-dotenv {h.get('python_dotenv')})",
        f"- Parâmetros (sem o schema): `{json.dumps(_parametros_resumidos(e['parametros']), ensure_ascii=False)}`",
        "- Sem temperature/top_p/top_k (rejeitados pelo modelo); a variação é medida repetindo as rodadas.",
        f"- Commit avaliado (git HEAD): {e['git_head']}",
        f"- Início: {e['iniciado_em']}",
        f"- Fim: {e['concluido_em']}",
        f"- Total de chamadas: {total_chamadas}",
        f"- Tentativas extras: {extras}",
        "",
    ]


def _md_abortadas(abortadas):
    linhas = [f"## Execuções abortadas anteriores: {len(abortadas)}", ""]
    if not abortadas:
        return linhas + ["Nenhuma.", ""]
    for a in abortadas:
        linhas.append(f"- pasta: {a['pasta']}; causa: {a['causa']}; motivo: {a['motivo']}")
    linhas.append("")
    return linhas


def _md_gerais(m):
    g = m["gerais"]
    f1, fa = g["f1"], g["falsos_alertas"]
    inv = m["saidas_invalidas"]  # previsão None em gerais E críticos
    micro = f1["micro"]
    linhas = [
        f"### Rodada {m['rodada']}, avaliações gerais ({g['total']})", "",
        f"- F1 micro: {_dec(micro['f1'])} "
        f"(precisão {_dec(micro['precisao'])}, recall {_dec(micro['recall'])})",
        f"- F1 macro: {_dec(f1['macro_f1'])}",
        f"- categorias_fora_do_macro: {_lista(f1['categorias_fora_do_macro'])}",
        "",
        "| Categoria | tp | fp | fn | Precisão | Recall | F1 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in CATEGORIAS:
        k = f1["por_categoria"][c]
        linhas.append(f"| {c} | {k['tp']} | {k['fp']} | {k['fn']} | "
                      f"{_dec(k['precisao'])} | {_dec(k['recall'])} | {_dec(k['f1'])} |")
    linhas += [
        "",
        "Uma saída inválida entra nas contas como previsão vazia.",
        "",
        f"Falsos alertas: {fa['falsos_alertas']} de {fa['total_nao_criticas']} avaliações não "
        f"críticas marcadas como críticas (saídas inválidas: {inv['na_rodada']} nos "
        f"{inv['itens_da_rodada']} itens desta rodada; {inv['nas_tres_rodadas']} de "
        f"{inv['itens_nas_tres_rodadas']} nas {N_RODADAS} rodadas).",
        "",
    ]
    return linhas


def _md_criticos(m):
    c = m["criticos"]
    linhas = [
        f"### Rodada {m['rodada']}, avaliações críticas ({c['total']})", "",
        f"- Recall geral: {c['detectadas']} de {c['total']} "
        f"(saídas inválidas entre os críticos: {c['saidas_invalidas']})",
        "- Recall por tipo (alergia classificada como cobrança indevida não conta como acerto "
        "em alergia):",
    ]
    for t in CRITICIDADES:
        k = c["por_tipo"][t]
        linhas.append(f"  - {t}: {k['acertos']} de {k['total']}, "
                      f"falsos positivos da marcação: {k['falsos_positivos']}")
    linhas.append("")
    return linhas


def _md_medias(med):
    n = med["rodadas"]
    g, c = med["gerais"], med["criticos"]
    fa = g["falsos_alertas"]
    linhas = [
        "## Médias das três rodadas", "",
        FRASE_NENHUMA_ESCOLHIDA, "",
        "### Avaliações gerais", "",
        f"- F1 micro: média {_dec(g['f1_micro']['media'])} ({g['f1_micro']['rodadas']} de {n} rodadas)",
        f"- F1 macro: média {_dec(g['f1_macro']['media'])} ({g['f1_macro']['rodadas']} de {n} rodadas)",
        "",
        "Média só dos valores definidos; a última coluna diz quantas rodadas entraram.",
        "",
        "| Categoria | F1 médio | Rodadas com F1 definido |",
        "| --- | --- | --- |",
    ]
    for cat in CATEGORIAS:
        k = g["por_categoria"][cat]
        linhas.append(f"| {cat} | {_dec(k['media'])} | {k['rodadas']} de {n} |")
    linhas += [
        "",
        f"Falsos alertas: média {_dec(fa['media'], 2)} de {fa['total_nao_criticas']} avaliações "
        f"não críticas marcadas como críticas (saídas inválidas na mesma execução: média "
        f"{_dec(fa['saidas_invalidas_media'], 2)}).",
        "",
        "### Avaliações críticas", "",
        f"- Recall geral: média {_dec(c['detectadas']['media'], 2)} de {c['total']} "
        f"(saídas inválidas entre os críticos: média {_dec(c['saidas_invalidas_media'], 2)})",
        "- Recall por tipo (média de contagens):",
    ]
    for t in CRITICIDADES:
        k = c["por_tipo"][t]
        linhas.append(f"  - {t}: média {_dec(k['acertos_media'], 2)} de {k['total']}, "
                      f"falsos positivos da marcação: média {_dec(k['falsos_positivos_media'], 2)}")
    linhas.append("")
    return linhas


def _md_variacao(variacao, nomes_rodadas):
    linhas = ["## Variação entre rodadas", ""]
    if not variacao:
        return linhas + ["Nenhuma previsão mudou entre as rodadas.", ""]
    linhas += ["Itens cuja previsão (categorias e criticidade, sem ordem) mudou entre rodadas.", ""]
    for v in variacao:
        linhas.append(f"- **{v['id']}** ({v['conjunto']})")
        for numero, p in zip(nomes_rodadas, v["previsoes"]):
            linhas.append(f"  - rodada {numero}: {_fmt_previsao(p)}")
    linhas.append("")
    return linhas


def _md_erros(erros):
    linhas = ["## Erros", "",
              "Itens com previsão diferente do gabarito (categorias ou criticidade) ou saída inválida.",
              ""]
    for e in erros:
        for rotulo, chave in (("gerais", "gerais"), ("críticos", "criticos")):
            linhas += [f"### Rodada {e['rodada']}, {rotulo}", ""]
            if not e[chave]:
                linhas += ["Nenhum erro.", ""]
                continue
            for i in e[chave]:
                linhas += [
                    f"- **{i['id']}** (tentativas: {i['tentativas']})",
                    f"  - texto: {json.dumps(i['texto'], ensure_ascii=False)}",
                    f"  - gabarito: {_fmt_gabarito(i['gabarito'])}",
                    f"  - previsão: {_fmt_previsao(i['previsao'])}",
                ]
                if "tipos_de_erro" in i:
                    tipos = "; ".join(f"{t['tentativa']}: {t['tipo']}" for t in i["tipos_de_erro"])
                    linhas.append(f"  - erros por tentativa: {tipos or 'nenhum registrado'}")
            linhas.append("")
    return linhas


def _md_tokens(t):
    linhas = ["## Tokens e custo estimado", ""]
    if t["entrada"] is None:
        return linhas + ["Tokens: não registrado. O custo real está no Console.", ""]
    return linhas + [
        f"- Respostas com uso registrado: {t['usos_com_entrada']} (entrada) e "
        f"{t['usos_com_saida']} (saída), de {t['usos_total']}",
        f"- Tokens de entrada: {t['entrada']}",
        f"- Tokens de saída: {t['saida']}",
        f"- Custo estimado: US$ {_dec(t['custo_estimado_usd'], 4)} "
        f"(entrada a US$ {PRECO_ENTRADA_MTOK} / MTok, saída a US$ {PRECO_SAIDA_MTOK} / MTok)",
        f"- Fonte do preço: {FONTE_PRECO} (preço consultado em {DATA_CONSULTA_PRECO})",
        "- É estimativa: tentativas que terminaram em exceção não têm uso registrado. "
        "O custo real está no Console.",
        "",
    ]


def _md_ressalvas():
    linhas = ["## Ressalvas obrigatórias", ""]
    linhas += [f"{n}. {texto}" for n, texto in enumerate(RESSALVAS, start=1)]
    linhas.append("")
    return linhas


# ---------- função pública ----------

def gerar_relatorio_oficial(execucao, conjunto_geral=CONJUNTO_GERAL,
                            conjunto_critico=CONJUNTO_CRITICO):
    """Devolve (markdown, dados) da avaliação oficial. Não lê arquivos nem altera `execucao`."""
    rodadas = sorted(execucao["rodadas"], key=lambda r: r["rodada"])
    if len(rodadas) != N_RODADAS:
        raise ValueError(f"a avaliação oficial tem {N_RODADAS} rodadas, não {len(rodadas)}")
    abortadas = copy.deepcopy(execucao.get("abortadas") or [])

    itens = [i for r in rodadas for i in r["itens"]]
    total_chamadas = sum(i["tentativas"] for i in itens)
    extras = sum(max(i["tentativas"] - 1, 0) for i in itens)

    metricas = [_metricas_da_rodada(r, conjunto_geral, conjunto_critico) for r in rodadas]
    # Saídas inválidas = previsão None em gerais E críticos, por rodada e nas três.
    invalidas = [sum(1 for i in r["itens"] if i["previsao"] is None) for r in rodadas]
    for m, r, n in zip(metricas, rodadas, invalidas):
        m["saidas_invalidas"] = {"na_rodada": n, "itens_da_rodada": len(r["itens"]),
                                 "nas_tres_rodadas": sum(invalidas),
                                 "itens_nas_tres_rodadas": len(itens)}
    medias = _medias(metricas)
    variacao = _variacao(rodadas)
    erros = _erros(rodadas, conjunto_geral, conjunto_critico)
    tokens = _tokens(rodadas)

    so_relatorio = bool(execucao.get("gerado_por_so_relatorio"))
    git_head_atual = execucao.get("git_head_atual") if so_relatorio else None

    dados = {
        "versao": execucao["versao"],
        "aviso": AVISO,
        "gerado_por_so_relatorio": so_relatorio,
        "git_head_atual": git_head_atual,
        "hashes": copy.deepcopy(execucao.get("hashes") or {}),
        "modelo": execucao["modelo"],
        "parametros": copy.deepcopy(execucao["parametros"]),
        "git_head": execucao["git_head"],
        "iniciado_em": execucao["iniciado_em"],
        "concluido_em": execucao["concluido_em"],
        "total_chamadas": total_chamadas,
        "tentativas_extras": extras,
        "n_abortadas": len(abortadas),
        "abortadas": abortadas,
        "rodadas": metricas,
        "medias": medias,
        "frase_das_medias": FRASE_NENHUMA_ESCOLHIDA,
        "variacao": variacao,
        "erros": erros,
        "tokens": tokens,
        "ressalvas": list(RESSALVAS),
    }

    linhas = [f"# Avaliação oficial da {execucao['versao']} (prompt congelado). {AVISO}", ""]
    if so_relatorio:
        linhas += [MARCA_SO_RELATORIO.format(head=git_head_atual), ""]
    linhas += _md_metadados(execucao, total_chamadas, extras)
    linhas += _md_abortadas(abortadas)
    for m in metricas:
        linhas += [f"## Rodada {m['rodada']}", ""]
        linhas += _md_gerais(m)
        linhas += _md_criticos(m)
    linhas += _md_medias(medias)
    linhas += _md_variacao(variacao, [r["rodada"] for r in rodadas])
    linhas += _md_erros(erros)
    linhas += _md_tokens(tokens)
    linhas += _md_ressalvas()
    return "\n".join(linhas), dados
