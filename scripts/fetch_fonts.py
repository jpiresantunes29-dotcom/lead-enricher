"""Baixa as fontes do Google e gera static/fonts/fonts.css para self-host.

Motivo: o <link> para fonts.googleapis.com bloqueia a renderização (DNS +
TLS + CSS + woff2 num domínio de terceiros), o que penaliza LCP — métrica de
ranqueamento. Servindo do mesmo domínio, some um round-trip inteiro.

Uso (os .woff2 são versionados no repo; rode só ao mudar a tipografia):
    python scripts/fetch_fonts.py

Inter e Plus Jakarta Sans são licenciadas sob a SIL Open Font License 1.1,
que permite hospedagem própria.
"""

import re
from pathlib import Path

import requests

# Pesos realmente usados em static/css/styles.css, static/landing/landing.css
# e templates/legal_base.css.
FAMILIES = {
    "Inter": [400, 500, 600, 700],
    "Plus Jakarta Sans": [400, 500, 600, 700, 800],
}
SUBSETS = ("latin", "latin-ext")  # latin-ext cobre nomes de decisores estrangeiros

# UA de Chrome moderno — sem isso o Google devolve @font-face com TTF.
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

OUT_DIR = Path(__file__).resolve().parent.parent / "static" / "fonts"


def google_css(family: str, weights: list[int]) -> str:
    spec = f"{family.replace(' ', '+')}:wght@{';'.join(str(w) for w in weights)}"
    # URL montada à mão: `params` faria escape do '+' da família e o Google devolve 400.
    resp = requests.get(
        f"https://fonts.googleapis.com/css2?family={spec}&display=swap",
        headers={"User-Agent": CHROME_UA},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text


def local_name(family: str, weight: str, subset: str) -> str:
    return f"{family.lower().replace(' ', '-')}-{subset}-{weight}.woff2"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    blocos: list[str] = []
    baixados = 0

    for family, weights in FAMILIES.items():
        css = google_css(family, weights)
        # Cada @font-face do Google vem precedido de um comentário com o subset.
        for subset, corpo in re.findall(r"/\*\s*([\w-]+)\s*\*/\s*(@font-face\s*{[^}]*})", css):
            if subset not in SUBSETS:
                continue
            weight = re.search(r"font-weight:\s*(\d+)", corpo).group(1)
            url = re.search(r"url\((https://[^)]+\.woff2)\)", corpo).group(1)
            unicode_range = re.search(r"unicode-range:\s*([^;]+);", corpo).group(1)

            arquivo = local_name(family, weight, subset)
            binario = requests.get(url, headers={"User-Agent": CHROME_UA}, timeout=30)
            binario.raise_for_status()
            (OUT_DIR / arquivo).write_bytes(binario.content)
            baixados += 1

            blocos.append(
                "@font-face{"
                f"font-family:'{family}';"
                "font-style:normal;"
                f"font-weight:{weight};"
                "font-display:swap;"
                f"src:url('/static/fonts/{arquivo}') format('woff2');"
                f"unicode-range:{unicode_range};"
                "}"
            )

    cabecalho = (
        "/* Gerado por scripts/fetch_fonts.py — não editar à mão.\n"
        "   Inter e Plus Jakarta Sans · SIL Open Font License 1.1 */\n"
    )
    (OUT_DIR / "fonts.css").write_text(cabecalho + "\n".join(blocos) + "\n", encoding="utf-8")
    print(f"{baixados} arquivos .woff2 em {OUT_DIR}")
    print(f"CSS: {OUT_DIR / 'fonts.css'}")


if __name__ == "__main__":
    main()
