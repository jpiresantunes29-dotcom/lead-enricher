"""
Extrai domínios de uma lista colada ou de um CSV exportado de outro sistema.

O usuário não deveria ter que formatar nada: quem exporta do CRM tem cabeçalho
e dez colunas, quem copia da planilha tem uma coluna, quem copia do navegador
tem URL completa com https e caminho. Tudo isso vira a mesma lista de domínios.
"""
import csv
import io
import re
from typing import List

from services._utils import normalize_domain

# Cabeçalhos que costumam guardar o site da empresa, em português e inglês.
_COLUNAS_PROVAVEIS = (
    "dominio", "domínio", "domain", "site", "website", "web site", "url",
    "endereco", "endereço", "pagina", "página", "homepage", "empresa_site",
)

# Uma linha só com "empresa;site;telefone" é cabeçalho, não dado.
_LINHA_TEM_DOMINIO = re.compile(r"[a-z0-9][a-z0-9\-]*\.[a-z]{2,}", re.IGNORECASE)


def _parece_dominio(valor: str) -> bool:
    valor = (valor or "").strip()
    if not valor or " " in valor.strip():
        # Espaço no meio é razão social, não domínio ("Acme Tecnologia Ltda").
        if not valor.lower().startswith(("http://", "https://")):
            return False
    return bool(_LINHA_TEM_DOMINIO.search(valor))


def _dialeto(texto: str) -> str:
    """Descobre o separador olhando a primeira linha não vazia."""
    primeira = next((l for l in texto.splitlines() if l.strip()), "")
    for sep in (";", ",", "\t", "|"):
        if sep in primeira:
            return sep
    return ","


def parse(texto: str, limit: int = 1000) -> List[str]:
    """
    Devolve os domínios encontrados, na ordem em que aparecem e sem repetir.

    Funciona com: uma coluna por linha, CSV com cabeçalho (escolhe a coluna do
    site), CSV sem cabeçalho (escolhe a coluna que mais parece domínio) e URLs
    completas.
    """
    if not texto or not texto.strip():
        return []

    linhas = list(csv.reader(io.StringIO(texto.strip()), delimiter=_dialeto(texto)))
    linhas = [l for l in linhas if any((c or "").strip() for c in l)]
    if not linhas:
        return []

    coluna = None
    primeira = [(c or "").strip().lower() for c in linhas[0]]

    # Cabeçalho declarado: respeitamos o nome da coluna.
    if not any(_parece_dominio(c) for c in primeira):
        for indice, nome in enumerate(primeira):
            if any(chave in nome for chave in _COLUNAS_PROVAVEIS):
                coluna = indice
                break
        linhas = linhas[1:]     # a primeira linha era cabeçalho

    # Sem cabeçalho útil: vale a coluna em que mais linhas parecem domínio.
    if coluna is None and linhas:
        largura = max(len(l) for l in linhas)
        pontuacao = [
            sum(1 for l in linhas if len(l) > i and _parece_dominio(l[i]))
            for i in range(largura)
        ]
        melhor = max(range(largura), key=lambda i: pontuacao[i]) if largura else 0
        coluna = melhor if pontuacao and pontuacao[melhor] else None

    vistos = set()
    out: List[str] = []
    for linha in linhas:
        candidatos = []
        if coluna is not None and len(linha) > coluna:
            candidatos.append(linha[coluna])
        else:
            candidatos.extend(linha)     # linha torta: procura em qualquer coluna

        for bruto in candidatos:
            if not _parece_dominio(bruto):
                continue
            dominio = normalize_domain(bruto.strip())
            # E-mail colado por engano ainda carrega o domínio da empresa.
            if "@" in dominio:
                dominio = dominio.split("@", 1)[1]
            if not dominio or "." not in dominio or dominio in vistos:
                continue
            vistos.add(dominio)
            out.append(dominio)
            break

        if len(out) >= limit:
            break
    return out
