"""Executor da avaliação oficial da v1 (prompt congelado): 50 gerais e 15 críticos, 3 rodadas.

Dados simulados. Projeto independente, sem vínculo com o iFood.

ESTE É O ÚNICO MÓDULO que conhece os arquivos dos conjuntos de avaliação. Faz chamadas pagas à
API; só rodar quando decidido. Uso:
    python -m avaliacao.rodar_oficial                   # dry-run: confere travas e não abre nada
    python -m avaliacao.rodar_oficial --executar        # avaliação de verdade
    python -m avaliacao.rodar_oficial --executar --motivo-tecnico "texto"   # refaz após aborto
    python -m avaliacao.rodar_oficial --so-relatorio    # relatório a partir de um diário completo

As opções não aceitam abreviação (--exec é erro de argparse). --so-relatorio é exclusivo com
--executar e --motivo-tecnico.

O prompt é lido UMA vez: o hash conferido com o registro é o da mesma string enviada à API.

Travas, nesta ordem (cada uma encerra ANTES de qualquer chamada à API):
a) Hashes e versões atuais (prompt, schema, parâmetros, regra de retentativa, modelo, SDK) devem
   ser idênticos aos do registro de congelamento (saída 2).
b) A árvore git não pode ter alterações em prompts/, app/, avaliacao/ e ajuste/, exceto nas
   pastas de saída (saída 3). O commit avaliado (git HEAD) vai para o relatório.
c) Sem --executar e sem --so-relatorio: imprime o dry-run e sai (0), sem abrir dados, sem conferir
   a tag nem o hash dos dados e sem criar cliente.
d) Com --executar, nesta ordem:
   (i)   TAG classificador-v1-congelado: ela existe, o HEAD descende dela e app/classificador.py,
         app/contrato.py e app/metricas.py são idênticos aos da tag (saída 7). Sem abrir dados.
   (ii)  Recusa sobrescrever avaliação concluída (saída 4).
   (iii) Se há v1.lock, outra execução pode estar em andamento (saída 9, sem tocar em nada).
   (iv)  Diário anterior: um diário completo e concluído sem relatório NÃO é aborto e não se
         arquiva (saída 5: use --so-relatorio); um diário incompleto ou abortado exige
         --motivo-tecnico (saída 5 sem ele, ou com motivo e nada a arquivar). Aqui só se confere;
         nada é movido.
   (v)   Os dois conjuntos são abertos e validados (saída 6) e o hash de cada um é conferido com o
         registro (saída 8; só diz QUAL conjunto diverge, nunca conteúdo).
   (vi)  O cliente é criado (saída 10 se a chave estiver ausente; nada é mostrado além disso).
   (vii) TRAVA DE EXECUÇÃO: cria avaliacao/resultados/v1.lock com open(..., "x") (pid e hora);
         se já existir, saída 9 sem tocar em nada. O lock é removido no fim, em qualquer saída.
   (viii) SÓ AGORA o diário anterior (incompleto/abortado) é MOVIDO, nunca apagado, para a pasta
         de abortadas. O arquivamento vem depois da validação, do cliente e do lock, para que uma
         falha de dados, de chave ou uma execução em andamento nunca desloque o diário.
   (ix)  Executa as rodadas.

Durante e depois da execução NADA é impresso sobre métricas, previsões ou contagens. O progresso
fica no diário (JSONL, uma linha por item, com flush e fsync). Em aborto (erro permanente da API,
erro técnico esgotado, Ctrl+C ou qualquer exceção) o diário recebe uma linha final de aborto (só o
tipo da exceção e, para a família ErroTecnicoDaAPI, status e request_id), nenhum relatório é gerado
e a saída é 1 (130 para Ctrl+C). Só quando as 3 rodadas terminam são gravados relatorio.json e,
depois, relatorio.md (se um dos dois falhar, o que foi criado é removido).

--so-relatorio roda as travas (a) e (b) e NÃO abre dados, NÃO cria cliente e NÃO chama a API. Lê o
diário e exige: início na primeira linha; exatamente 195 itens (3 rodadas x 65, na ordem rodada,
gerais, críticos); exatamente uma linha final de fim concluído e nenhuma linha de aborto; hashes do
início (código e dados) iguais aos atuais e registrados. O relatório sai marcado "gerado por
--so-relatorio", com o HEAD atual, e o commit avaliado continua sendo o do diário.

Códigos de saída:
    0 ok ou dry-run;
    1 aborto durante a execução (inclui erro técnico esgotado e erro permanente da API);
    2 congelamento diverge (ou erro de argparse: opção desconhecida, abreviada ou incompatível);
    3 git sujo ou git indisponível;
    4 avaliação já concluída (relatório presente);
    5 diário anterior sem --motivo-tecnico, motivo sem diário, ou diário concluído (use
      --so-relatorio);
    6 conjunto de dados inválido;
    7 tag de congelamento não confere;
    8 hash dos dados diverge do registro;
    9 v1.lock presente (outra execução em andamento ou lock órfão);
    10 cliente não pôde ser criado;
    11 --so-relatorio com diário inválido;
    130 Ctrl+C.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import contrato
from app.classificador import (
    MAX_TENTATIVAS,
    PARAMETROS,
    ErroTecnicoDaAPI,
    carregar_prompt,
    classificar,
    criar_cliente,
)
from avaliacao.congelamento import (
    CHAVES_DE_DADOS,
    comparar,
    hash_canonico,
    hashes_atuais,
    ler_congelamento,
)
from avaliacao.relatorio_oficial import (
    CONJUNTO_CRITICO,
    CONJUNTO_GERAL,
    gerar_relatorio_oficial,
)

RAIZ = Path(__file__).resolve().parent.parent
VERSAO = "v1"
ARQUIVO_GERAL = RAIZ / "data" / "geral.json"
ARQUIVO_CRITICO = RAIZ / "data" / "critico.json"
ARQUIVO_CONGELAMENTO = RAIZ / "prompts" / "classificador" / "v1.congelamento.md"
PASTA_RESULTADOS = RAIZ / "avaliacao" / "resultados" / "v1"
PASTA_ABORTADAS = RAIZ / "avaliacao" / "abortadas"
ARQUIVO_LOCK = PASTA_RESULTADOS.parent / "v1.lock"
RODADAS = 3
N_GERAL = 50
N_CRITICO = 15
N_ITENS_DO_DIARIO = RODADAS * (N_GERAL + N_CRITICO)
# Extrapolado da execução nos 20 de desenvolvimento (US$ 0,4486 em 60 chamadas). Só para o dry-run.
CUSTO_ESTIMADO_POR_CHAMADA = 0.4486 / 60

# Tag que marca o commit do congelamento e os arquivos que não podem mudar desde então.
TAG_CONGELAMENTO = "classificador-v1-congelado"
ARQUIVOS_CONGELADOS = ["app/classificador.py", "app/contrato.py", "app/metricas.py"]

MSG_ABORTO = "Execução abortada; nenhum resultado parcial foi exibido. Diário preservado."
MSG_CLIENTE = "cliente não pôde ser criado (chave ausente?)"
SEM_FIM = "sem marcação de fim"
SEM_MOTIVO = "sem motivo registrado"

# Pastas cujo conteúdo precisa estar commitado; as de saída ficam de fora para que um aborto
# (que deixa arquivos em resultados/) não bloqueie o refazer.
_PASTAS_VIGIADAS = ["prompts", "app", "avaliacao", "ajuste"]
_PASTAS_EXCLUIDAS = [":(exclude)avaliacao/resultados", ":(exclude)avaliacao/abortadas"]


# ---------- tempo e mensagens ----------

def _agora():
    """Agora em America/Sao_Paulo (UTC-3 fixo, sem horário de verão; sem depender do tzdata)."""
    return datetime.now(timezone(timedelta(hours=-3)))


def _iso(momento=None):
    return (momento or _agora()).isoformat(timespec="seconds")


def _dormir(segundos):
    """Espera entre tentativas do classificador. Procurada na hora da chamada (testes a trocam)."""
    time.sleep(segundos)


def _erro(mensagem):
    print(mensagem, file=sys.stderr)


# ---------- git ----------

def _git(argumentos, raiz):
    """Roda git sem shell (lista de argumentos). RuntimeError, sem detalhes, se falhar."""
    try:
        r = subprocess.run(["git", *argumentos], cwd=raiz, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", check=False)
    except OSError:
        raise RuntimeError("não foi possível executar o git") from None
    if r.returncode != 0:
        raise RuntimeError(f"git {argumentos[0]} falhou (código {r.returncode})")
    return r.stdout


def _git_codigo(argumentos, raiz):
    """Roda git sem shell e devolve só o código de saída (a saída em si é descartada)."""
    try:
        r = subprocess.run(["git", *argumentos], cwd=raiz, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", check=False)
    except OSError:
        raise RuntimeError("não foi possível executar o git") from None
    return r.returncode


def git_head(raiz=RAIZ):
    """Hash do commit atual (git rev-parse HEAD)."""
    saida = _git(["rev-parse", "HEAD"], raiz).strip()
    if not saida:
        raise RuntimeError("git rev-parse HEAD não devolveu nada")
    return saida


def git_alteracoes(raiz=RAIZ):
    """Linhas de `git status --porcelain` em prompts, app, avaliacao e ajuste.

    Inclui arquivos não rastreados (um a um) e deixa de fora as pastas de saída
    avaliacao/resultados e avaliacao/abortadas. Lista vazia = árvore limpa.
    """
    saida = _git(["status", "--porcelain", "--untracked-files=all", "--",
                  *_PASTAS_VIGIADAS, *_PASTAS_EXCLUIDAS], raiz)
    return [linha for linha in saida.splitlines() if linha.strip()]


def conferir_tag(raiz=RAIZ):
    """Confere a tag de congelamento. Levanta RuntimeError (mensagem sem conteúdo de arquivo) se:
    a tag não existe; o HEAD não descende dela; ou algum de ARQUIVOS_CONGELADOS difere da tag.
    """
    ref = f"refs/tags/{TAG_CONGELAMENTO}"
    if _git_codigo(["rev-parse", "--verify", "--quiet", ref], raiz) != 0:
        raise RuntimeError(f"a tag {TAG_CONGELAMENTO} não existe")
    if _git_codigo(["merge-base", "--is-ancestor", ref, "HEAD"], raiz) != 0:
        raise RuntimeError(f"o HEAD atual não descende da tag {TAG_CONGELAMENTO}")
    if _git_codigo(["diff", "--quiet", ref, "HEAD", "--", *ARQUIVOS_CONGELADOS], raiz) != 0:
        raise RuntimeError(f"{', '.join(ARQUIVOS_CONGELADOS)} diferem da tag {TAG_CONGELAMENTO}")


# ---------- conjuntos ----------

def carregar_conjunto(caminho, nome, esperado):
    """Lê e valida um conjunto de avaliação. Levanta ValueError sem citar texto de avaliação.

    Exige: lista de objetos; campo `conjunto` igual a `nome` em todos; `esperado` itens;
    ids únicos; rótulos dentro do contrato; geral sem nenhuma criticidade; crítico com ao menos
    uma. O nome é conferido ANTES de abrir o arquivo.
    """
    if nome not in (CONJUNTO_GERAL, CONJUNTO_CRITICO):
        raise ValueError(f"conjunto desconhecido: {nome!r}")
    itens = json.loads(Path(caminho).read_text(encoding="utf-8"))
    if not isinstance(itens, list):
        raise ValueError(f"o arquivo do conjunto {nome} deve conter uma lista")
    ids = set()
    for posicao, a in enumerate(itens):
        if not isinstance(a, dict):
            raise ValueError(f"item {posicao} do conjunto {nome} não é um objeto")
        if a.get("conjunto") != nome:
            raise ValueError(f"item {posicao}: o campo conjunto deve ser {nome!r}")
        id_ = a.get("id_externo")
        if not isinstance(id_, str) or not id_:
            raise ValueError(f"item {posicao} do conjunto {nome} sem id_externo")
        if not isinstance(a.get("texto"), str):
            raise ValueError(f"item {id_}: texto ausente ou não é texto")
        try:
            contrato.validar_saida({"categorias": a.get("categorias"),
                                    "criticidade": a.get("criticidade")})
        except contrato.SaidaInvalida as e:
            raise ValueError(f"item {id_}: rótulos fora do contrato ({e})") from None
        if id_ in ids:
            raise ValueError(f"id repetido no conjunto {nome}: {id_}")
        ids.add(id_)
        if nome == CONJUNTO_GERAL and a["criticidade"]:
            raise ValueError(f"item {id_}: o conjunto {nome} não pode ter criticidade")
        if nome == CONJUNTO_CRITICO and not a["criticidade"]:
            raise ValueError(f"item {id_}: o conjunto {nome} exige criticidade")
    if len(itens) != esperado:
        raise ValueError(f"o conjunto {nome} deve ter {esperado} itens (tem {len(itens)})")
    return itens


# ---------- diário ----------

def _gravar(arquivo, obj):
    """Uma linha JSON no diário, já no disco (flush e fsync) antes de seguir."""
    arquivo.write(json.dumps(obj, ensure_ascii=False) + "\n")
    arquivo.flush()
    os.fsync(arquivo.fileno())


def _causa_do_aborto(excecao):
    """Só o tipo e, para a API, status e request_id. Nunca a mensagem nem o corpo do erro."""
    if isinstance(excecao, ErroTecnicoDaAPI):
        return {"tipo": type(excecao).__name__, "status": excecao.status,
                "request_id": excecao.request_id}
    return {"tipo": type(excecao).__name__}


def _executar(gerais, criticos, cliente, prompt, head, hashes):
    """Roda as rodadas gravando cada item no diário assim que termina.

    `hashes` (código, regra de retentativa e dados) vai inteiro para a linha de início. Devolve
    (iniciado_em, concluido_em, rodadas). Em qualquer interrupção grava a linha de aborto (melhor
    esforço) e relança a exceção.
    """
    PASTA_RESULTADOS.mkdir(parents=True, exist_ok=True)
    iniciado_em = _iso()
    rodadas = []
    with open(PASTA_RESULTADOS / "diario.jsonl", "a", encoding="utf-8", newline="\n") as diario:
        _gravar(diario, {"tipo": "inicio", "versao": VERSAO, "git_head": head,
                         "iniciado_em": iniciado_em, **hashes})
        try:
            for n in range(1, RODADAS + 1):
                itens = []
                for conjunto, avaliacoes in ((CONJUNTO_GERAL, gerais), (CONJUNTO_CRITICO, criticos)):
                    for a in avaliacoes:
                        r = classificar(a["texto"], cliente, prompt, dormir=_dormir)
                        item = {
                            "id": a["id_externo"],
                            "texto": a["texto"],
                            "gabarito": {"categorias": a["categorias"],
                                         "criticidade": a["criticidade"]},
                            "previsao": r["saida"],
                            "tentativas": r["tentativas"],
                            "erros": r["erros"],
                            "usos": r["usos"],
                        }
                        _gravar(diario, {"tipo": "item", "rodada": n, "conjunto": conjunto, **item})
                        itens.append({"conjunto": conjunto, **item})
                rodadas.append({"rodada": n, "itens": itens})
        except BaseException as e:  # inclui KeyboardInterrupt: marcar e relançar
            try:
                _gravar(diario, {"tipo": "fim", "status": "abortado", "causa": _causa_do_aborto(e)})
            except Exception:
                pass  # melhor esforço: o aborto original é o que importa
            raise
        concluido_em = _iso()
        _gravar(diario, {"tipo": "fim", "status": "concluido", "concluido_em": concluido_em})
    return iniciado_em, concluido_em, rodadas


def _arquivar(motivo):
    """MOVE a pasta de resultados para abortadas/v1_AAAAMMDDTHHMMSS e grava motivo.json."""
    PASTA_ABORTADAS.mkdir(parents=True, exist_ok=True)
    agora = _agora()
    base = f"{VERSAO}_{agora.strftime('%Y%m%dT%H%M%S')}"
    destino = PASTA_ABORTADAS / base
    sufixo = 1
    while destino.exists():
        sufixo += 1
        destino = PASTA_ABORTADAS / f"{base}_{sufixo}"
    shutil.move(str(PASTA_RESULTADOS), str(destino))
    (destino / "motivo.json").write_text(
        json.dumps({"motivo": motivo, "arquivada_em": _iso(agora)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return destino


def _causa_arquivada(pasta):
    """Texto da causa, a partir da última linha `fim` do diário arquivado."""
    fim = None
    try:
        linhas = (pasta / "diario.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return SEM_FIM
    for linha in linhas:
        try:
            obj = json.loads(linha)
        except ValueError:
            continue  # linha truncada por uma queda no meio da gravação
        if isinstance(obj, dict) and obj.get("tipo") == "fim":
            fim = obj
    if fim is None:
        return SEM_FIM
    status = str(fim.get("status"))
    causa = fim.get("causa")
    if not isinstance(causa, dict):
        return status
    detalhes = [str(causa["tipo"])] if "tipo" in causa else []
    if causa.get("status") is not None:
        detalhes.append(f"status {causa['status']}")
    if causa.get("request_id") is not None:
        detalhes.append(f"request_id {causa['request_id']}")
    return f"{status} ({', '.join(detalhes)})" if detalhes else status


def _motivo_arquivado(pasta):
    try:
        obj = json.loads((pasta / "motivo.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return SEM_MOTIVO
    motivo = obj.get("motivo") if isinstance(obj, dict) else None
    return motivo if isinstance(motivo, str) and motivo.strip() else SEM_MOTIVO


def _ler_abortadas():
    """Uma entrada por subpasta de abortadas: pasta, causa e motivo gravado."""
    if not PASTA_ABORTADAS.is_dir():
        return []
    pastas = sorted(p for p in PASTA_ABORTADAS.iterdir() if p.is_dir())
    return [{"pasta": p.name, "causa": _causa_arquivada(p), "motivo": _motivo_arquivado(p)}
            for p in pastas]


# ---------- leitura e validação do diário (para --so-relatorio e para a regra do refazer) ----------
# Toda mensagem de ValueError abaixo é fixa: nunca traz texto, id, hash nem previsão do diário.

def _linhas_do_diario(caminho):
    """Linhas do diário como dicts. ValueError (mensagem fixa) se não existir ou não for JSONL."""
    try:
        bruto = Path(caminho).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise ValueError("o diário não existe ou não pôde ser lido") from None
    linhas = []
    for numero, texto in enumerate(bruto.splitlines(), start=1):
        if not texto.strip():
            continue
        try:
            obj = json.loads(texto)
        except ValueError:
            raise ValueError(f"a linha {numero} do diário não é JSON") from None
        if not isinstance(obj, dict):
            raise ValueError(f"a linha {numero} do diário não é um objeto") from None
        linhas.append(obj)
    return linhas


def _rotulos_validos(obj):
    if not isinstance(obj, dict):
        return False
    try:
        contrato.validar_saida({"categorias": obj.get("categorias"),
                                "criticidade": obj.get("criticidade")})
    except contrato.SaidaInvalida:
        return False
    return set(obj) == {"categorias", "criticidade"}


def _item_valido(item, rodada, conjunto):
    """Forma de uma linha de item do diário, na posição (rodada, conjunto) esperada."""
    inteiro = lambda v: isinstance(v, int) and not isinstance(v, bool)  # noqa: E731
    return (inteiro(item.get("rodada")) and item["rodada"] == rodada
            and item.get("conjunto") == conjunto
            and isinstance(item.get("id"), str) and item["id"] != ""
            and isinstance(item.get("texto"), str)
            and _rotulos_validos(item.get("gabarito"))
            and "previsao" in item
            and (item["previsao"] is None or _rotulos_validos(item["previsao"]))
            and inteiro(item.get("tentativas")) and 0 <= item["tentativas"] <= MAX_TENTATIVAS
            and isinstance(item.get("erros"), list) and isinstance(item.get("usos"), list))


def _validar_diario(linhas):
    """Estrutura de um diário completo e concluído. Devolve (inicio, itens, fim).

    Exige: primeira linha tipo=inicio; exatamente 195 itens (3 rodadas x 65, ordem rodada, gerais,
    críticos); exatamente uma linha final tipo=fim status=concluido; nenhuma linha de aborto; os
    mesmos ids, na mesma ordem, em todas as rodadas. Não confere hashes.
    """
    if not linhas or linhas[0].get("tipo") != "inicio":
        raise ValueError("o diário não começa com a linha de início")
    fim = linhas[-1]
    if fim.get("tipo") != "fim":
        raise ValueError("o diário não termina com uma linha de fim (execução incompleta)")
    if fim.get("status") != "concluido":
        raise ValueError("o diário termina em aborto ou sem fim concluído")
    meio = linhas[1:-1]
    for linha in meio:
        tipo = linha.get("tipo")
        if tipo == "fim":
            raise ValueError("o diário tem linha de fim ou de aborto antes do fim")
        if tipo != "item":
            raise ValueError("o diário tem linha de tipo inesperado")
    if len(meio) != N_ITENS_DO_DIARIO:
        raise ValueError(f"o diário tem {len(meio)} itens; o esperado são {N_ITENS_DO_DIARIO}")
    por_rodada = N_GERAL + N_CRITICO
    for indice, item in enumerate(meio):
        posicao = indice % por_rodada
        conjunto = CONJUNTO_GERAL if posicao < N_GERAL else CONJUNTO_CRITICO
        if not _item_valido(item, indice // por_rodada + 1, conjunto):
            raise ValueError("itens fora de ordem ou malformados (ordem: rodada, gerais, críticos)")
    ids = [[i["id"] for i in meio[n * por_rodada:(n + 1) * por_rodada]] for n in range(RODADAS)]
    if any(i != ids[0] for i in ids[1:]) or len(set(ids[0])) != por_rodada:
        raise ValueError("os ids dos itens não são os mesmos, sem repetição, nas três rodadas")
    return linhas[0], meio, fim


def _diario_completo_e_concluido(caminho):
    """True se o diário tem a estrutura completa de uma avaliação concluída (sem conferir hashes)."""
    try:
        _validar_diario(_linhas_do_diario(caminho))
    except ValueError:
        return False
    return True


def _execucao_do_diario(registrado, atuais, head_atual):
    """Monta a `execucao` do relatório a partir do diário completo. ValueError (mensagem fixa)."""
    inicio, itens, fim = _validar_diario(_linhas_do_diario(PASTA_RESULTADOS / "diario.jsonl"))
    esperados = {**atuais, **{k: registrado[k] for k in CHAVES_DE_DADOS}}
    divergentes = sorted(k for k, v in esperados.items() if inicio.get(k) != v)
    if inicio.get("versao") != VERSAO or registrado["versao"] != VERSAO:
        divergentes.append("versao")
    if divergentes:
        raise ValueError("o início do diário diverge do código atual e do registro: "
                         + ", ".join(divergentes))
    for valor in (inicio.get("git_head"), inicio.get("iniciado_em"), fim.get("concluido_em")):
        if not isinstance(valor, str) or not valor:
            raise ValueError("faltam git_head, iniciado_em ou concluido_em no diário")
    por_rodada = N_GERAL + N_CRITICO
    chaves = ("id", "texto", "gabarito", "previsao", "tentativas", "erros", "usos")
    rodadas = [{"rodada": n + 1,
                "itens": [{"conjunto": i["conjunto"], **{c: i[c] for c in chaves}}
                          for i in itens[n * por_rodada:(n + 1) * por_rodada]]}
               for n in range(RODADAS)]
    return {
        "versao": VERSAO,
        "hashes": esperados,
        "git_head": inicio["git_head"],
        "iniciado_em": inicio["iniciado_em"],
        "concluido_em": fim["concluido_em"],
        "modelo": PARAMETROS["model"],
        "parametros": PARAMETROS,
        "rodadas": rodadas,
        "abortadas": _ler_abortadas(),
        "gerado_por_so_relatorio": True,
        "git_head_atual": head_atual,
    }


# ---------- relatórios ----------

def _gravar_atomico(caminho, texto):
    """Escreve em arquivo temporário e renomeia, para nunca deixar relatório pela metade."""
    temporario = caminho.with_name(caminho.name + ".tmp")
    with open(temporario, "w", encoding="utf-8", newline="\n") as arquivo:
        arquivo.write(texto)
        arquivo.flush()
        os.fsync(arquivo.fileno())
    os.replace(temporario, caminho)


def _gravar_relatorios(markdown, dados):
    """Grava relatorio.json e, DEPOIS, relatorio.md. Se um dos dois falhar, remove o que criou.

    Devolve (caminho_md, caminho_json).
    """
    caminho_json = PASTA_RESULTADOS / "relatorio.json"
    caminho_md = PASTA_RESULTADOS / "relatorio.md"
    try:
        _gravar_atomico(caminho_json, json.dumps(dados, ensure_ascii=False, indent=2))
        _gravar_atomico(caminho_md, markdown)
    except BaseException:
        for caminho in (caminho_json, caminho_md):
            for alvo in (caminho, caminho.with_name(caminho.name + ".tmp")):
                try:
                    alvo.unlink(missing_ok=True)
                except OSError:
                    pass  # melhor esforço: o erro original é o que importa
        raise
    return caminho_md, caminho_json


def _gerar_e_gravar(execucao):
    """Gera e grava os relatórios. Imprime só os caminhos. Devolve a saída (0 ou 1)."""
    try:
        markdown, dados = gerar_relatorio_oficial(execucao)
        caminho_md, caminho_json = _gravar_relatorios(markdown, dados)
    except Exception as e:
        _erro(f"Falha ao gerar o relatório ({type(e).__name__}). Diário preservado; "
              "nenhum resultado foi exibido.")
        return 1
    print(f"Relatórios gravados: {caminho_md} {caminho_json}")
    return 0


# ---------- dry-run ----------

def _dry_run(hashes, head):
    total = RODADAS * (N_GERAL + N_CRITICO)
    custo = f"{total * CUSTO_ESTIMADO_POR_CHAMADA:.2f}".replace(".", ",")
    print("\n".join([
        "Dry-run da avaliação oficial. Dados simulados. Projeto independente, sem vínculo com o iFood.",
        f"Versão: {VERSAO}",
        f"Commit (git HEAD): {head}",
        f"SHA-256 do prompt: {hashes['prompt_sha256']}",
        f"SHA-256 do schema: {hashes['schema_sha256']}",
        f"SHA-256 dos parâmetros: {hashes['parametros_sha256']}",
        f"SHA-256 da regra de retentativa: {hashes['regra_retentativa_sha256']}",
        f"Modelo: {hashes['modelo']}",
        f"Chamadas previstas: {total} chamadas = ({N_GERAL}+{N_CRITICO})*{RODADAS}",
        f"Custo estimado: ~US$ {custo} (extrapolado, não garantido; base: execução nos 20 de "
        "desenvolvimento)",
        "Congelamento e git conferidos. A tag de congelamento e o hash dos dados só são "
        "conferidos com --executar. Nada foi aberto nem chamado: dados, API e diário "
        "intocados. Para executar de verdade, use --executar.",
    ]))


# ---------- --so-relatorio ----------

def _so_relatorio(registrado, atuais, head):
    """Relatório a partir de um diário completo. Não abre dados, não cria cliente, não chama a API."""
    if (PASTA_RESULTADOS / "relatorio.json").exists() or (PASTA_RESULTADOS / "relatorio.md").exists():
        _erro("Já existe uma avaliação concluída (relatório presente). Recusado: não sobrescrevo.")
        return 4
    try:
        execucao = _execucao_do_diario(registrado, atuais, head)
    except ValueError as e:  # mensagens fixas, sem conteúdo do diário
        _erro(f"Diário inválido para --so-relatorio: {e}. Nenhum relatório foi gerado.")
        return 11
    except Exception as e:
        _erro(f"Diário inválido para --so-relatorio ({type(e).__name__}). Nenhum relatório foi gerado.")
        return 11
    return _gerar_e_gravar(execucao)


# ---------- --executar ----------

def _criar_lock():
    """Cria o lock com open(..., "x"). Devolve None se criou, ou o texto do problema."""
    try:
        ARQUIVO_LOCK.parent.mkdir(parents=True, exist_ok=True)
        with open(ARQUIVO_LOCK, "x", encoding="utf-8") as lock:
            lock.write(f"pid {os.getpid()}\n{_iso()}\n")
    except FileExistsError:
        return "existe"
    except OSError:
        return "falhou"
    return None


def _remover_lock():
    try:
        ARQUIVO_LOCK.unlink(missing_ok=True)
    except OSError:
        _erro("Aviso: não foi possível remover o lock; remova manualmente "
              "avaliacao/resultados/v1.lock antes de uma nova execução.")


MSG_LOCK = ("v1.lock presente: outra execução em andamento ou lock órfão; confira e remova "
            "manualmente. Nada foi tocado.")


def _executar_e_relatar(gerais, criticos, cliente, prompt, head, hashes):
    """Executa as rodadas e, só com as 3 completas, grava os relatórios. Devolve a saída."""
    try:
        iniciado_em, concluido_em, rodadas = _executar(gerais, criticos, cliente, prompt,
                                                      head, hashes)
    except KeyboardInterrupt:
        _erro(MSG_ABORTO)
        return 130
    except Exception:
        _erro(MSG_ABORTO)
        return 1
    return _gerar_e_gravar({
        "versao": VERSAO,
        "hashes": hashes,
        "git_head": head,
        "iniciado_em": iniciado_em,
        "concluido_em": concluido_em,
        "modelo": PARAMETROS["model"],
        "parametros": PARAMETROS,
        "rodadas": rodadas,
        "abortadas": _ler_abortadas(),
    })


def _avaliar(args, registrado, atuais, prompt, head):
    """Fluxo --executar, travas (d)(i) a (d)(ix) do docstring do módulo."""
    # (i) tag: antes de abrir qualquer dado.
    try:
        conferir_tag()
    except RuntimeError as e:
        _erro(f"A trava da tag {TAG_CONGELAMENTO} falhou: {e}. Nada foi aberto nem chamado.")
        return 7

    # (ii) avaliação já concluída.
    if (PASTA_RESULTADOS / "relatorio.json").exists() or (PASTA_RESULTADOS / "relatorio.md").exists():
        _erro("Já existe uma avaliação concluída (relatório presente). Recusado: não sobrescrevo.")
        return 4

    # (iii) lock: olhada sem criar (a criação atômica vem depois). Antes de qualquer outra coisa
    # que dependa do diário, para uma segunda execução não receber a orientação errada.
    if ARQUIVO_LOCK.exists():
        _erro(MSG_LOCK)
        return 9

    # (iv) diário anterior e --motivo-tecnico: só confere, nada é movido.
    motivo = (args.motivo_tecnico or "").strip()
    tem_diario = (PASTA_RESULTADOS / "diario.jsonl").exists()
    if tem_diario:
        if _diario_completo_e_concluido(PASTA_RESULTADOS / "diario.jsonl"):
            _erro("Existe um diário anterior COMPLETO e CONCLUÍDO, sem relatório. Isso não é um "
                  "aborto e não pode ser arquivado como tal: use --so-relatorio para gerar o "
                  "relatório a partir dele.")
            return 5
        if not motivo:
            _erro("Existe um diário anterior sem relatório (execução incompleta ou abortada). "
                  "Para arquivá-lo e refazer a avaliação, informe --motivo-tecnico TEXTO.")
            return 5
    elif motivo:
        _erro("--motivo-tecnico informado, mas não há diário anterior a arquivar.")
        return 5

    # (v) dados: abrir, validar e conferir o hash de cada conjunto com o registro.
    try:
        gerais = carregar_conjunto(ARQUIVO_GERAL, CONJUNTO_GERAL, N_GERAL)
        criticos = carregar_conjunto(ARQUIVO_CRITICO, CONJUNTO_CRITICO, N_CRITICO)
    except (OSError, ValueError) as e:
        _erro(f"Conjunto de avaliação ausente ou inválido: {e}")
        return 6
    # O hash é do MESMO conteúdo lido e validado acima (uma só leitura de cada arquivo); é o
    # mesmo valor de congelamento.hash_dados(arquivo).
    dados = {"dados_geral_sha256": hash_canonico(gerais),
             "dados_critico_sha256": hash_canonico(criticos)}
    divergentes = [nome for nome, chave in ((CONJUNTO_GERAL, "dados_geral_sha256"),
                                            (CONJUNTO_CRITICO, "dados_critico_sha256"))
                   if dados[chave] != registrado[chave]]
    if divergentes:
        _erro("Os dados de avaliação divergem do hash registrado no congelamento. Divergem: "
              f"{', '.join(repr(nome) for nome in divergentes)}. Nada foi executado.")
        return 8

    # (vi) cliente: ausência de chave não pode vir depois de arquivar o diário.
    try:
        cliente = criar_cliente()
    except (SystemExit, Exception):  # criar_cliente encerra com SystemExit se faltar a chave
        _erro(MSG_CLIENTE)
        return 10

    # (vii) trava de execução, atômica.
    problema = _criar_lock()
    if problema == "existe":
        _erro(MSG_LOCK)
        return 9
    if problema is not None:
        _erro("Não foi possível criar o lock avaliacao/resultados/v1.lock. Nada foi executado.")
        return 9
    try:
        # (viii) só agora o diário anterior é movido.
        if tem_diario:
            try:
                _arquivar(motivo)
            except OSError as e:
                _erro(f"Falha ao arquivar o diário anterior ({type(e).__name__}). "
                      "Nada foi executado.")
                return 5
        # (ix) execução. Nada de métricas na saída, nem durante nem depois.
        return _executar_e_relatar(gerais, criticos, cliente, prompt, head, {**atuais, **dados})
    finally:
        _remover_lock()


# ---------- main ----------

def _parser():
    p = argparse.ArgumentParser(description="Avaliação oficial da v1 (50 gerais e 15 críticos).",
                                allow_abbrev=False)
    p.add_argument("--executar", action="store_true",
                   help="roda de verdade (chamadas pagas); sem isto, só o dry-run")
    p.add_argument("--motivo-tecnico", default=None, metavar="TEXTO",
                   help="arquiva o diário de uma execução anterior incompleta, com este motivo")
    p.add_argument("--so-relatorio", action="store_true",
                   help="gera o relatório a partir de um diário completo e concluído, sem chamar "
                        "a API nem abrir os dados (exclusivo com --executar e --motivo-tecnico)")
    return p


def main(argv=None):
    """Devolve o código de saída (lista completa no docstring do módulo)."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.so_relatorio and (args.executar or args.motivo_tecnico is not None):
        parser.error("--so-relatorio não pode ser usado junto com --executar nem com "
                     "--motivo-tecnico")

    # Trava (a): congelamento. O prompt é lido UMA vez; o hash é o da mesma string enviada.
    try:
        prompt = carregar_prompt(VERSAO)
    except (OSError, ValueError) as e:
        _erro(f"Prompt {VERSAO} ausente ou ilegível ({type(e).__name__}). "
              "Nada foi aberto nem chamado.")
        return 2
    try:
        registrado = ler_congelamento(ARQUIVO_CONGELAMENTO)
        atuais = hashes_atuais(VERSAO, prompt_texto=prompt)
    except (OSError, ValueError) as e:
        _erro(f"Registro de congelamento ausente ou inválido: {e}")
        return 2
    divergentes = set(comparar(registrado, atuais))
    if registrado["versao"] != VERSAO:
        divergentes.add("versao")
    if divergentes:
        _erro("O código atual diverge do registro de congelamento. Divergem: "
              f"{', '.join(sorted(divergentes))}. Nada foi aberto nem chamado.")
        return 2

    # Trava (b): árvore git limpa (vale também no dry-run e em --so-relatorio).
    try:
        alteracoes = git_alteracoes()
        if not alteracoes:
            head = git_head()
    except RuntimeError as e:
        _erro(f"Não foi possível conferir o git: {e}")
        return 3
    if alteracoes:
        _erro("Há alterações não commitadas em prompts/, app/, avaliacao/ ou ajuste/. "
              "Commite antes de avaliar. Caminhos:")
        for linha in alteracoes:
            _erro(f"  {linha[3:] if len(linha) > 3 else linha}")
        return 3

    if args.so_relatorio:
        return _so_relatorio(registrado, atuais, head)

    # Trava (c): sem --executar, só o dry-run.
    if not args.executar:
        if args.motivo_tecnico:
            print("(--motivo-tecnico só vale junto com --executar; foi ignorado.)")
        _dry_run(atuais, head)
        return 0

    return _avaliar(args, registrado, atuais, prompt, head)


if __name__ == "__main__":
    sys.exit(main())
