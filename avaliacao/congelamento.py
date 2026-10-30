"""Congelamento do classificador: hashes do que vai à API e conferência com o registro.

Dados simulados. Projeto independente, sem vínculo com o iFood.

O prompt, o schema de saída, os parâmetros da chamada, a regra de retentativa, o modelo e as
versões das bibliotecas definem o que a avaliação oficial mede. Antes de gastar qualquer chamada,
o executor confere se tudo isso é idêntico ao que foi registrado no congelamento.

O registro (prompts/classificador/v1.congelamento.md) traz um bloco legível por máquina, entre as
linhas `<!-- congelamento:inicio -->` e `<!-- congelamento:fim -->`, com uma linha `chave: valor`
por item (o valor pode vir entre crases).

Além dos hashes do código (hashes_atuais), o bloco registra o hash canônico dos dois conjuntos de
avaliação (CHAVES_DE_DADOS). Esses dois NÃO fazem parte de hashes_atuais: dependem dos arquivos de
dados, que só o executor abre (e só com --executar). ler_congelamento exige as duas chaves;
quem as confere é o executor, com hash_dados.
"""
import hashlib
import importlib.metadata
import json
import re
from pathlib import Path

from app import classificador

MARCA_INICIO = "<!-- congelamento:inicio -->"
MARCA_FIM = "<!-- congelamento:fim -->"
STATUS_CONGELADO = "CONGELADO"

# Chaves que hashes_atuais devolve e que o registro precisa ter.
CHAVES_DE_HASHES = (
    "prompt_sha256",
    "schema_sha256",
    "parametros_sha256",
    "regra_retentativa_sha256",
    "modelo",
    "anthropic",
    "python_dotenv",
)

# Chaves com o hash canônico dos conjuntos de avaliação. Exigidas no registro, mas fora de
# hashes_atuais (dependem dos dados).
CHAVES_DE_DADOS = ("dados_geral_sha256", "dados_critico_sha256")

_HEX_64 = re.compile(r"[0-9a-f]{64}")


def hash_canonico(obj):
    """SHA-256 (hex) do JSON canônico: chaves ordenadas, sem espaços, UTF-8 sem escapes."""
    texto = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def hash_dados(caminho):
    """Hash canônico (hash_canonico) do JSON de um arquivo de dados, lido como UTF-8.

    Independe de formatação, quebras de linha e ordem das chaves, mas não da ordem da lista nem
    dos valores. Levanta OSError se o arquivo não abrir e ValueError se não for JSON.
    """
    return hash_canonico(json.loads(Path(caminho).read_text(encoding="utf-8")))


def hashes_atuais(versao="v1", prompt_texto=None):
    """Hashes e versões do código tal como está agora (lidos do módulo a cada chamada).

    `prompt_texto` é opcional: quem já leu o prompt (o executor lê UMA vez) passa a MESMA string
    que vai à API, e o hash é calculado sobre ela, sem reler o arquivo. Sem ele, o prompt é lido.
    NÃO inclui os hashes dos dados (CHAVES_DE_DADOS).
    """
    if prompt_texto is None:
        hash_prompt = classificador.sha256_do_prompt(versao)
    else:
        hash_prompt = classificador.sha256_do_texto(prompt_texto)
    return {
        "prompt_sha256": hash_prompt,
        "schema_sha256": hash_canonico(classificador.SCHEMA),
        "parametros_sha256": hash_canonico(classificador.PARAMETROS),
        "regra_retentativa_sha256": hash_canonico(classificador.REGRA_RETENTATIVA),
        "modelo": classificador.PARAMETROS["model"],
        "anthropic": importlib.metadata.version("anthropic"),
        "python_dotenv": importlib.metadata.version("python-dotenv"),
    }


def _bloco(texto):
    """Linhas entre as duas marcas. Levanta ValueError se faltar uma delas."""
    linhas = [linha.strip() for linha in texto.splitlines()]
    if MARCA_INICIO not in linhas:
        raise ValueError(f"registro de congelamento sem a marca {MARCA_INICIO}")
    inicio = linhas.index(MARCA_INICIO)
    if MARCA_FIM not in linhas[inicio + 1:]:
        raise ValueError(f"registro de congelamento sem a marca {MARCA_FIM}")
    fim = linhas.index(MARCA_FIM, inicio + 1)
    return linhas[inicio + 1:fim]


def ler_congelamento(caminho):
    """Lê o bloco do registro e devolve {chave: valor} (textos, sem as crases).

    Levanta ValueError se faltar o bloco, o status CONGELADO, a versão, qualquer chave de
    hashes_atuais ou de CHAVES_DE_DADOS (estas com 64 caracteres hexadecimais minúsculos), ou se
    uma chave aparecer repetida.
    """
    with open(caminho, encoding="utf-8") as arquivo:
        texto = arquivo.read()
    registrado = {}
    for linha in _bloco(texto):
        if not linha:
            continue
        if ":" not in linha:
            raise ValueError(f"linha do bloco de congelamento sem 'chave: valor': {linha!r}")
        chave, valor = linha.split(":", 1)
        chave = chave.strip()
        valor = valor.replace("`", "").strip()
        if chave in registrado:
            raise ValueError(f"chave repetida no registro de congelamento: {chave}")
        registrado[chave] = valor
    if registrado.get("status") != STATUS_CONGELADO:
        raise ValueError(f"o status do registro deve ser {STATUS_CONGELADO} "
                         f"(encontrado: {registrado.get('status')!r})")
    for chave in ("versao", *CHAVES_DE_HASHES, *CHAVES_DE_DADOS):
        if chave not in registrado:
            raise ValueError(f"chave ausente no registro de congelamento: {chave}")
    for chave in CHAVES_DE_DADOS:
        if not _HEX_64.fullmatch(registrado[chave]):
            raise ValueError(f"{chave} deve ter 64 caracteres hexadecimais minúsculos")
    return registrado


def comparar(registrado, atuais):
    """Chaves de `atuais` cujo valor difere do registrado (ordenadas; vazia se tudo bate)."""
    return sorted(chave for chave, valor in atuais.items() if registrado.get(chave) != valor)
