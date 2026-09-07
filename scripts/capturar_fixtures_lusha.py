"""
Captura as fixtures reais da Lusha Prospecting e aponta onde o parser diverge.

Fecha o único item aberto da §11 de `docs/LUSHA_PROSPECTING_IMPLEMENTACAO.md`:
hoje `tests/fixtures/lusha_search.json` e `lusha_enrich.json` foram montadas a
partir da documentação oficial, não de uma resposta observada. O parser aceita
mais de um nome por campo para não quebrar na primeira divergência — o que
também significa que ele pode estar lendo do lugar errado sem ninguém perceber.
Foi assim que a primeira tentativa chamou `/v2/company`, o endpoint errado.

Este script existe porque a receita da §11 são quatro passos manuais com dois
`curl` diferentes, e passo manual repetido é passo que sai errado. Aqui é um
comando só, e ele ainda compara o que voltou com o que `parse_contact()` espera.

**Custo: 1 crédito** (search de 10 contatos) **+ 1 crédito por e-mail revelado**
se `--enrich` for usado. A chave nunca é lida de arquivo nem passada por
argumento — só por variável de ambiente, para não parar no histórico do shell:

    LUSHA_API_KEY=... python -m scripts.capturar_fixtures_lusha --enrich

Sem `--enrich` só o search é capturado (1 crédito), o que já fecha metade da §11.
"""
import argparse
import json
import os
import pathlib
import sys
from typing import Any, Dict, List

import requests

_RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ))

from services.providers.lusha_prospecting import (  # noqa: E402
    _ENRICH_URL, _SEARCH_URL, _lista_de_contatos, parse_contact,
)

_FIXTURES = _RAIZ / "tests" / "fixtures"
_TIMEOUT = 30

#: Campos que `parse_contact()` produz e que, se vierem vazios da resposta real,
#: quase certamente significam nome de campo diferente — não ausência do dado.
#: `emails`/`phones` ficam de fora: no search eles vêm vazios por projeto (o
#: search lista, o enrich revela), então vazio ali é o comportamento correto.
_CAMPOS_SUSPEITOS = (
    "name", "title", "linkedin_url", "location", "department",
    "seniority", "company_name", "company_domain", "can_reveal",
)


def _chave() -> str:
    chave = (os.getenv("LUSHA_API_KEY") or "").strip()
    if not chave:
        sys.exit(
            "LUSHA_API_KEY não está definida.\n"
            "A chave é sua e os créditos são seus (modelo BYOA). Exporte-a só\n"
            "para esta execução:\n\n"
            "    LUSHA_API_KEY=... python -m scripts.capturar_fixtures_lusha\n"
        )
    return chave


def _post(url: str, chave: str, corpo: dict) -> Any:
    resp = requests.post(
        url,
        headers={"api_key": chave, "Content-Type": "application/json"},
        json=corpo,
        timeout=_TIMEOUT,
    )
    if resp.status_code == 401:
        sys.exit("401: chave inválida ou sem permissão para a Prospecting API.")
    if resp.status_code == 402:
        sys.exit("402: sem créditos na conta. Nenhuma fixture foi gravada.")
    if resp.status_code == 429:
        sys.exit("429: limite de requisições atingido. Tente de novo mais tarde.")
    resp.raise_for_status()
    return resp.json()


def _gravar(nome: str, payload: Any) -> pathlib.Path:
    destino = _FIXTURES / nome
    destino.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destino


def _relatar_divergencias(contatos: List[Dict[str, Any]]) -> bool:
    """
    Compara o que a API devolveu com o que o parser consegue ler.

    Um campo que fica vazio em TODOS os contatos é o sinal de nome divergente:
    um contato sem cargo acontece, dez sem cargo é o parser lendo a chave
    errada. Devolve True se estiver tudo certo.
    """
    lidos = [p for p in (parse_contact(c) for c in contatos) if p]
    print(f"\nContatos na resposta: {len(contatos)} · lidos pelo parser: {len(lidos)}")

    if not lidos:
        print(
            "\n  ✗ O parser não conseguiu ler NENHUM contato.\n"
            "    `id`/`contactId` ou o nome estão em campos com outro nome.\n"
            "    Compare a fixture recém-gravada com `parse_contact()`."
        )
        return False

    ok = True
    if len(lidos) < len(contatos):
        print(f"  ⚠ {len(contatos) - len(lidos)} contato(s) sem id ou sem nome legível.")
        ok = False

    print("\nCampo                  preenchidos")
    for campo in _CAMPOS_SUSPEITOS:
        n = sum(1 for p in lidos if p.get(campo))
        marca = "✓" if n else "✗"
        print(f"  {marca} {campo:<20} {n}/{len(lidos)}")
        if not n:
            ok = False

    if not ok:
        print(
            "\n  Campo com 0/N é nome divergente, não dado ausente.\n"
            "  Abra a fixture, ache a chave real e acrescente o nome em\n"
            "  `parse_contact()` (ou no helper `_nome`/`_cargo`/... daquele campo)."
        )
    return ok


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--domain", default="nubank.com.br",
                   help="Domínio da empresa a pesquisar (padrão: nubank.com.br)")
    p.add_argument("--size", type=int, default=10,
                   help="Contatos no search. Até 25 custa 1 crédito (padrão: 10)")
    p.add_argument("--enrich", action="store_true",
                   help="Também revela UM contato para capturar a fixture do enrich "
                        "(custa mais 1 crédito pelo e-mail)")
    args = p.parse_args()

    chave = _chave()

    print(f"→ search: {args.domain} ({args.size} contatos, 1 crédito)")
    # Confirmado em 2026-09-07 contra o OpenAPI oficial: domínio fica em
    # filters.companies.include.domains, e a paginação é `pagination`, não
    # `pages`. A primeira tentativa (sem os níveis "include" e com "pages")
    # devolvia 400 "property X should not exist" — é exatamente o tipo de
    # divergência que este script existe para pegar.
    busca = _post(_SEARCH_URL, chave, {
        "filters": {"companies": {"include": {"domains": [args.domain]}}},
        "pagination": {"page": 0, "size": args.size},
    })
    destino = _gravar("lusha_search.json", busca)
    print(f"  gravado em {destino.relative_to(_RAIZ)}")

    contatos = _lista_de_contatos(busca)
    if not contatos:
        print(
            "\n  ✗ Nenhum contato na resposta. Ou a empresa não tem contatos na\n"
            "    base, ou o array está numa chave que `_lista_de_contatos()` não\n"
            "    conhece. Confira a fixture antes de concluir qualquer coisa."
        )
        return 1

    ok = _relatar_divergencias(contatos)

    if args.enrich:
        primeiro = parse_contact(contatos[0])
        if not primeiro:
            print("\n  ✗ Sem contato legível para revelar. Enrich não executado.")
            return 1
        cid = primeiro["lusha_contact_id"]
        print(f"\n→ enrich: 1 contato ({primeiro['name']}, +1 crédito por e-mail)")
        # Campo confirmado é `ids`, não `contactIds`.
        revelado = _post(_ENRICH_URL, chave, {"ids": [cid]})
        destino = _gravar("lusha_enrich.json", revelado)
        print(f"  gravado em {destino.relative_to(_RAIZ)}")

        revelados = _lista_de_contatos(revelado)
        lido = parse_contact(revelados[0]) if revelados else None
        if not lido:
            print("  ✗ O parser não leu o contato revelado.")
            ok = False
        else:
            print(f"  e-mails: {len(lido['emails'])} · telefones: {len(lido['phones'])}")
            if not lido["emails"] and not lido["phones"]:
                print("  ⚠ Nada revelado: ou o contato não tinha dado, ou os campos "
                      "de e-mail/telefone têm outro nome na resposta real.")
                ok = False

    print("\n" + ("─" * 60))
    if ok:
        print(
            "Tudo lido corretamente. Para fechar a §11:\n"
            "  1. python -m pytest tests/test_lusha_prospecting.py -q\n"
            "  2. apague o aviso no topo de tests/test_lusha_prospecting.py\n"
            "  3. apague a seção correspondente de tests/fixtures/LEIA-ME.md\n"
            "  4. atualize a §0 e a §11 de docs/LUSHA_PROSPECTING_IMPLEMENTACAO.md"
        )
        return 0
    print("Há divergências acima. Ajuste o parser ANTES de apagar os avisos —\n"
          "é o silêncio deles que a §11 existe para evitar.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
