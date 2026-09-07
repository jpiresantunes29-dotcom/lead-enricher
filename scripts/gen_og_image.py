"""Gera static/og-cover.png (1200x630), a imagem de compartilhamento social.

Uso (o PNG é versionado no repo; rode só quando a identidade mudar):
    python scripts/gen_og_image.py

Requer Pillow — dependência apenas de desenvolvimento, não vai para o runtime.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
VOID = (7, 12, 24)
SURFACE = (17, 26, 46)
BRAND = (59, 130, 246)
BRAND_BRIGHT = (96, 165, 250)
TEXT = (241, 245, 249)
TEXT_2 = (148, 163, 184)
OK = (74, 222, 128)

OUT = Path(__file__).resolve().parent.parent / "static" / "og-cover.png"

# Fontes do Windows; troque os caminhos se rodar em outro SO.
_FONTS = {
    "bold": "C:/Windows/Fonts/segoeuib.ttf",
    "semibold": "C:/Windows/Fonts/seguisb.ttf",
    "regular": "C:/Windows/Fonts/segoeui.ttf",
    "mono": "C:/Windows/Fonts/consola.ttf",
}


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(_FONTS[kind], size)
    except OSError:
        return ImageFont.load_default(size)


def gradient_backdrop() -> Image.Image:
    """Navy com um halo azul radial no canto superior direito."""
    img = Image.new("RGB", (W, H), VOID)
    halo = Image.new("L", (W, H), 0)
    hd = ImageDraw.Draw(halo)
    cx, cy = int(W * 0.78), int(H * 0.12)
    for r in range(560, 0, -8):
        hd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=int(46 * (1 - r / 560)))
    return Image.composite(Image.new("RGB", (W, H), BRAND), img, halo)


def rounded(draw, box, radius, **kw):
    draw.rounded_rectangle(box, radius=radius, **kw)


def main() -> None:
    img = gradient_backdrop()
    d = ImageDraw.Draw(img)

    # Marca
    rounded(d, (72, 62, 130, 120), 14, fill=BRAND)
    d.text((88, 72), "L", font=font("bold", 42), fill=(255, 255, 255))
    d.text((148, 78), "LeadEnricher", font=font("semibold", 34), fill=TEXT)

    # Headline
    d.text((72, 178), "Digite um domínio.", font=font("bold", 68), fill=TEXT)
    d.text((72, 258), "Conheça a empresa inteira.", font=font("bold", 68), fill=BRAND_BRIGHT)

    # Subtítulo
    d.text(
        (72, 356),
        "Infraestrutura, porte, decisores com e-mail verificado\n"
        "— em segundos, direto do domínio.",
        font=font("regular", 29),
        fill=TEXT_2,
        spacing=12,
    )

    # Cartão de resultado (o "produto" numa olhada)
    card = (72, 462, 1128, 566)
    rounded(d, card, 16, fill=SURFACE, outline=(38, 58, 96), width=1)
    d.text((100, 486), "acme.com.br", font=font("mono", 26), fill=TEXT)
    d.text((100, 522), "Acme Tecnologia · 51–200 · SaaS", font=font("regular", 20), fill=TEXT_2)

    for i, (label, value, color) in enumerate(
        [("MX", "Google Workspace", OK), ("DECISORES", "4 · e-mail válido", OK)]
    ):
        x = 560 + i * 250
        d.text((x, 484), label, font=font("mono", 16), fill=TEXT_2)
        d.text((x, 512), value, font=font("semibold", 22), fill=color)

    rounded(d, (960, 486, 1104, 542), 10, fill=(30, 64, 175))
    d.text((984, 500), "SCORE 87", font=font("mono", 24), fill=(219, 234, 254))

    # Faixa da marca no rodapé
    d.rectangle((0, H - 6, W, H), fill=BRAND)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG", optimize=True)
    print(f"gerado: {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
