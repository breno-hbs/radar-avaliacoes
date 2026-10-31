"""Simulador de avaliações (bloco 2.4). Dados SIMULADOS. Projeto independente, sem vínculo com o iFood.

Lê as 85 avaliações de data/ (20 de desenvolvimento, 50 gerais, 15 críticas) e as envia ao
POST /avaliacoes. Envia SOMENTE id_externo, texto, nota e recebida_em: o gabarito nunca vai no corpo.
Os arquivos não trazem recebida_em; ele é gerado de forma determinística (semente fixa) e distribui
as avaliações em semanas consecutivas, de segunda 00:00 a segunda seguinte 00:00 (America/Sao_Paulo).
O volume semanal (21/21/21/22) é uma hipótese do simulador, não um dado real.

Uso:
  python scripts/simulador.py --dry-run
  python scripts/simulador.py --semana-inicio 2026-10-05 --url http://127.0.0.1:8000

A chave vem de RADAR_API_KEY (ambiente ou .env) e vai só no cabeçalho X-API-Key; nunca é impressa.
Atenção: contra a API real, isto classifica os 50 gerais e os 15 críticos com o modelo e dispara
alertas de verdade no Telegram. Rode só com o prompt congelado e registre versão e modelo; ajustar
algo depois de ver o resultado torna a métrica exploratória. Não use dentro de testes.
"""

import argparse
import json
import logging
import os
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time as hora, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
PASTA_DADOS = RAIZ / "data"
ARQUIVOS = ("desenvolvimento.json", "geral.json", "critico.json")
FUSO = ZoneInfo("America/Sao_Paulo")

TIMEOUT_SEGUNDOS = 60
ESPERAS_503 = (5, 15)  # entre a 1ª e a 2ª tentativa, e entre a 2ª e a 3ª
HORA_INICIAL, HORA_FINAL = 11, 22  # 11:00 a 22:59
URL_PADRAO = "http://127.0.0.1:8000"
MAX_FALHAS_SEGUIDAS = 3  # falhas finais seguidas (de qualquer tipo) que interrompem o envio

for _nome in ("httpx", "httpcore"):
    logging.getLogger(_nome).setLevel(logging.WARNING)


# Erros de transporte que valem um reenvio. Os demais (URL com esquema errado, cabeçalho inválido)
# são de configuração: reenviar 85 vezes só adiaria o erro.
_ERROS_DE_REDE = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)


def carregar_avaliacoes(pasta=None):
    """As avaliações dos três arquivos, só com id_externo, texto e nota (sem gabarito)."""
    pasta = PASTA_DADOS if pasta is None else pasta
    avaliacoes = []
    for nome in ARQUIVOS:
        with open(Path(pasta) / nome, encoding="utf-8") as f:
            for item in json.load(f):
                avaliacoes.append(
                    {"id_externo": item["id_externo"], "texto": item["texto"], "nota": item["nota"]}
                )
    ids = [a["id_externo"] for a in avaliacoes]
    if len(set(ids)) != len(ids):
        raise ValueError("id_externo repetido entre os arquivos de avaliações")
    return avaliacoes


def validar_segunda(texto: str) -> date:
    """Converte AAAA-MM-DD em data e exige que seja segunda-feira."""
    try:
        dia = date.fromisoformat(texto)
    except ValueError:
        raise ValueError(f"data inválida {texto!r}: use AAAA-MM-DD") from None
    if dia.weekday() != 0:
        raise ValueError(
            f"{texto} não é segunda-feira: a semana começa na segunda (informe, por exemplo, "
            f"{dia - timedelta(days=dia.weekday())})"
        )
    return dia


def semana_inicio_padrao(hoje: date, semanas: int = 4) -> date:
    """A segunda que abre as `semanas` semanas completas anteriores à semana de `hoje`."""
    segunda_atual = hoje - timedelta(days=hoje.weekday())
    return segunda_atual - timedelta(weeks=semanas)


def montar_cronograma(avaliacoes, *, semente=42, semanas=4, semana_inicio: date):
    """Corpos prontos para envio (4 chaves), em ordem de recebida_em. Determinístico."""
    if semanas < 1 or semanas > len(avaliacoes):
        raise ValueError("semanas deve estar entre 1 e a quantidade de avaliações")
    if semana_inicio.weekday() != 0:
        raise ValueError("semana_inicio deve ser uma segunda-feira")

    rng = random.Random(semente)
    embaralhadas = sorted(avaliacoes, key=lambda a: a["id_externo"])  # independe da ordem de entrada
    rng.shuffle(embaralhadas)

    base, resto = divmod(len(embaralhadas), semanas)
    tamanhos = [base + (1 if i >= semanas - resto else 0) for i in range(semanas)]  # 21/21/21/22

    corpos = []
    posicao = 0
    for indice, tamanho in enumerate(tamanhos):
        segunda = semana_inicio + timedelta(weeks=indice)
        for aval in embaralhadas[posicao:posicao + tamanho]:
            dia = segunda + timedelta(days=rng.randrange(7))
            instante = datetime.combine(
                dia, hora(rng.randint(HORA_INICIAL, HORA_FINAL), rng.randrange(60), rng.randrange(60)), FUSO
            )
            corpos.append({**aval, "recebida_em": instante.isoformat()})
        posicao += tamanho
    corpos.sort(key=lambda c: (datetime.fromisoformat(c["recebida_em"]), c["id_externo"]))
    return corpos


@dataclass
class Resumo:
    total: int = 0
    criadas: int = 0
    duplicatas: int = 0
    com_alerta: int = 0
    reenvios: int = 0
    falhas_finais: int = 0
    erros_422: int = 0
    erros_500: int = 0
    outros: int = 0
    falhas: list = field(default_factory=list)
    interrompido_por_401: bool = False
    parou_por_falhas_seguidas: bool = False
    id_do_401: str = ""


def _enviar_uma(cliente, corpo, resumo, dormir):
    """Envia uma avaliação; 503 e erro de rede reenviam a MESMA até 3 tentativas. Devolve o status final
    ou None se as tentativas se esgotaram."""
    tentativas = len(ESPERAS_503) + 1
    for n in range(tentativas):
        try:
            resposta = cliente.post("/avaliacoes", json=corpo)
        except _ERROS_DE_REDE:  # a mensagem da exceção nunca é impressa
            resposta = None
        except httpx.HTTPError:  # configuração: sem reenvio
            return None
        if resposta is not None and resposta.status_code != 503:
            return resposta
        if n < len(ESPERAS_503):
            resumo.reenvios += 1
            dormir(ESPERAS_503[n])
    return None


def enviar(corpos, *, url=URL_PADRAO, chave, intervalo=2, transporte=None, dormir=time.sleep):
    """Envia os corpos em ordem. Para na hora num 401. Nunca imprime a chave nem o texto."""
    resumo = Resumo()
    seguidas = 0
    with httpx.Client(
        base_url=url, timeout=TIMEOUT_SEGUNDOS, headers={"X-API-Key": chave}, transport=transporte
    ) as cliente:
        for i, corpo in enumerate(corpos):
            if i > 0 and intervalo:
                dormir(intervalo)
            resumo.total += 1
            resposta = _enviar_uma(cliente, corpo, resumo, dormir)
            id_externo = corpo["id_externo"]
            falhou = True
            if resposta is None:
                resumo.falhas_finais += 1
                resumo.falhas.append(id_externo)
            elif resposta.status_code == 401:
                resumo.interrompido_por_401 = True
                resumo.id_do_401 = id_externo
                resumo.falhas.append(id_externo)
                break
            elif resposta.status_code in (200, 201):
                falhou = False
                if resposta.status_code == 201:
                    resumo.criadas += 1
                    if _alerta_id(resposta) is not None:
                        resumo.com_alerta += 1
                else:
                    resumo.duplicatas += 1
            else:
                if resposta.status_code == 422:
                    resumo.erros_422 += 1
                elif resposta.status_code == 500:
                    resumo.erros_500 += 1
                else:
                    resumo.outros += 1
                resumo.falhas.append(id_externo)
            seguidas = seguidas + 1 if falhou else 0
            if seguidas >= MAX_FALHAS_SEGUIDAS:
                resumo.parou_por_falhas_seguidas = True
                break  # sai antes do intervalo do próximo envio
    return resumo


def _alerta_id(resposta):
    try:
        return resposta.json().get("alerta_id")
    except (ValueError, AttributeError):
        return None


def imprimir_resumo(resumo: Resumo):
    if resumo.interrompido_por_401:
        print(
            f"PARADO no 401 (avaliação {resumo.id_do_401}): a chave foi recusada. Confira RADAR_API_KEY "
            "no .env do simulador e a mesma variável no servidor."
        )
    if resumo.parou_por_falhas_seguidas:
        print(
            f"PARADO: {MAX_FALHAS_SEGUIDAS} avaliações seguidas falharam; confira se o servidor está no "
            "ar, a URL e a chave."
        )
    print("Resumo do simulador (dados simulados)")
    print(f"  total enviado:            {resumo.total}")
    print(f"  criadas:                  {resumo.criadas}")
    print(f"  duplicatas:               {resumo.duplicatas}")
    print(f"  criadas com alerta:       {resumo.com_alerta}")
    print(f"  reenvios (503 ou rede):   {resumo.reenvios}")
    print(f"  falhas finais (503/rede): {resumo.falhas_finais}")
    print(f"  422:                      {resumo.erros_422}")
    print(f"  500:                      {resumo.erros_500}")
    if resumo.outros:
        print(f"  outros códigos:           {resumo.outros}")
    print(f"  parou por falhas seguidas: {'sim' if resumo.parou_por_falhas_seguidas else 'não'}")
    print(f"  id_externo que falharam:  {', '.join(resumo.falhas) if resumo.falhas else '(nenhum)'}")


def imprimir_dry_run(corpos, semanas, semana_inicio):
    print("Dry-run: nada será enviado. Dados simulados.")
    print(f"Semana inicial: {semana_inicio} (segunda-feira), {semanas} semanas, fuso America/Sao_Paulo")
    por_semana = Counter((datetime.fromisoformat(c["recebida_em"]).date() - semana_inicio).days // 7 for c in corpos)
    for indice in range(semanas):
        daquela = [c["recebida_em"] for c in corpos
                   if (datetime.fromisoformat(c["recebida_em"]).date() - semana_inicio).days // 7 == indice]
        inicio = semana_inicio + timedelta(weeks=indice)
        print(f"  semana {indice + 1} ({inicio}): {por_semana[indice]} avaliações; "
              f"primeira {daquela[0]}; última {daquela[-1]}")
    exemplo = dict(corpos[0])
    exemplo["texto"] = f"<texto omitido, {len(exemplo['texto'])} caracteres>"
    print("Exemplo de corpo (texto omitido; a chave vai só no cabeçalho X-API-Key e não é mostrada):")
    print(json.dumps(exemplo, ensure_ascii=False, indent=2))


def ler_argumentos(argv=None):
    p = argparse.ArgumentParser(description="Envia as 85 avaliações simuladas ao POST /avaliacoes.")
    p.add_argument("--url", default=URL_PADRAO, help="URL base da API (padrão %(default)s)")
    p.add_argument("--semente", type=int, default=42, help="semente fixa (padrão %(default)s)")
    p.add_argument("--semanas", type=int, default=4, help="quantidade de semanas (padrão %(default)s)")
    p.add_argument("--semana-inicio", help="segunda-feira AAAA-MM-DD que abre a 1ª semana")
    p.add_argument("--intervalo", type=float, default=2, help="segundos entre envios (padrão %(default)s)")
    p.add_argument("--dry-run", action="store_true", help="mostra o plano e não envia nada")
    return p.parse_args(argv)


def main(argv=None, *, transporte=None) -> int:
    args = ler_argumentos(argv)
    if args.intervalo < 0:
        print("erro: --intervalo não pode ser negativo.", file=sys.stderr)
        return 2
    try:
        if args.semana_inicio:
            inicio = validar_segunda(args.semana_inicio)
        else:
            inicio = semana_inicio_padrao(datetime.now(FUSO).date(), args.semanas)
        corpos = montar_cronograma(
            carregar_avaliacoes(), semente=args.semente, semanas=args.semanas, semana_inicio=inicio
        )
    except ValueError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    except (OSError, KeyError) as e:  # arquivo de dados ausente ou sem campo; sem traceback
        print(f"erro ao ler os dados de avaliação ({type(e).__name__}).", file=sys.stderr)
        return 2

    if args.dry_run:
        imprimir_dry_run(corpos, args.semanas, inicio)
        return 0

    load_dotenv(RAIZ / ".env")
    chave = os.environ.get("RADAR_API_KEY", "").strip()
    if not chave:
        print("erro: RADAR_API_KEY ausente. Defina-a no .env (o valor nunca é exibido).", file=sys.stderr)
        return 2

    resumo = enviar(
        corpos, url=args.url, chave=chave, intervalo=args.intervalo, transporte=transporte, dormir=time.sleep
    )
    imprimir_resumo(resumo)
    if resumo.interrompido_por_401:
        return 3
    if resumo.parou_por_falhas_seguidas:
        return 4
    return 1 if resumo.falhas else 0


if __name__ == "__main__":
    sys.exit(main())
