#!/usr/bin/env python3
"""Render every SpareCycles brand asset from one vector-ish definition.

    pip install pillow
    python tools/generate_brand_assets.py

Outputs into web/: favicon.svg, favicon.ico, favicon-16/32/48.png,
apple-touch-icon.png (iOS home screen), icon-192/512.png (Android/PWA),
maskable-512.png, og-image.png (link previews).

The mark is a cycle arrow (compute cycles) wrapped around a coin (tokens) —
the two things this project trades. Shapes are supersampled 8x and
downsampled with LANCZOS so they stay crisp at 16px.
"""

import math
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "..", "web")

INK = (232, 234, 242)        # --ink (dark theme)
ACCENT = (123, 123, 240)     # --accent (dark theme)
ACCENT_DEEP = (91, 91, 214)  # --accent (light theme), used for depth
BG_TOP = (30, 31, 58)        # icon backdrop, top
BG_BOTTOM = (15, 16, 23)     # --bg (dark theme)
MUTED = (143, 148, 166)      # --muted

FONTS = r"C:\Windows\Fonts"
F_BOLD = os.path.join(FONTS, "segoeuib.ttf")
F_SEMI = os.path.join(FONTS, "seguisb.ttf")
F_REG = os.path.join(FONTS, "segoeui.ttf")


def font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default(size)


def vgradient(size: tuple[int, int], top: tuple, bottom: tuple) -> Image.Image:
    """Vertical two-stop gradient without needing numpy."""
    mask = Image.linear_gradient("L").resize(size, Image.BILINEAR)
    return Image.composite(
        Image.new("RGB", size, bottom), Image.new("RGB", size, top), mask)


# PIL's radial_gradient only saturates at the *corners* of its square, so a
# naive invert still leaves ~30/255 of light along the square's edges — which
# shows up as a hard seam wherever the lamp is pasted. Remapping so the light
# reaches exactly zero at the inscribed circle guarantees a seamless paste.
_EDGE = 255 * 128 / 181.0   # gradient value at the inscribed circle


def glow(size: tuple[int, int], strength: float) -> Image.Image:
    """Soft radial light (bright centre → zero at the inscribed circle)."""
    g = Image.radial_gradient("L").resize(size, Image.BILINEAR)
    return g.point(
        lambda v: int(max(0.0, 1.0 - v / _EDGE) ** 1.7 * 255 * strength))


def glow_at(canvas: tuple[int, int], centre: tuple[int, int],
            radius: int, strength: float) -> Image.Image:
    """Radial light centred anywhere on the canvas, fading to nothing before
    it reaches an edge — so the card never shows a hard cut-off line."""
    mask = Image.new("L", canvas, 0)
    lamp = glow((radius * 2, radius * 2), strength)
    mask.paste(lamp, (centre[0] - radius, centre[1] - radius))
    return mask


def draw_mark(size: int, backdrop: bool = True, pad: float = 0.0) -> Image.Image:
    """The SpareCycles mark. `pad` insets the glyph (for maskable icons)."""
    ss = 8
    s = size * ss
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    if backdrop:
        img.paste(vgradient((s, s), BG_TOP, BG_BOTTOM), (0, 0))
        halo = Image.new("RGBA", (s, s), ACCENT + (255,))
        img.paste(halo, (0, 0), glow((s, s), 0.30))
        img.putalpha(255)

    d = ImageDraw.Draw(img)
    c = s / 2
    scale = 1.0 - pad
    r = s * 0.335 * scale          # ring radius
    w = s * 0.125 * scale          # ring thickness
    coin = s * 0.150 * scale       # inner coin radius

    # Cycle arrow: an open ring with a clean gap, ending in an arrowhead.
    start, end = 128, 388
    d.arc([c - r, c - r, c + r, c + r], start, end,
          fill=ACCENT + (255,), width=int(w))

    # Arrowhead, tangent to the ring at `end`. Kept narrow and pointed so it
    # reads as motion rather than as a blob at favicon size.
    th = math.radians(end)
    tx, ty = -math.sin(th), math.cos(th)      # tangent (screen coords)
    nx, ny = math.cos(th), math.sin(th)       # radial
    px, py = c + r * nx, c + r * ny
    d.polygon([
        (px + tx * w * 2.05, py + ty * w * 2.05),   # tip
        (px + nx * w * 1.02, py + ny * w * 1.02),   # outer base
        (px - nx * w * 1.02, py - ny * w * 1.02),   # inner base
    ], fill=ACCENT + (255,))

    # Coin at the centre = the donated tokens.
    d.ellipse([c - coin, c - coin, c + coin, c + coin], fill=INK + (255,))
    slot = coin * 0.42
    d.ellipse([c - slot, c - slot, c + slot, c + slot], fill=ACCENT_DEEP + (255,))

    return img.resize((size, size), Image.LANCZOS)


FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1e1f3a"/><stop offset="1" stop-color="#0f1017"/>
    </linearGradient>
  </defs>
  <rect width="64" height="64" rx="14" fill="url(#bg)"/>
  <path d="M32 12.6a19.4 19.4 0 1 1-16.8 9.7" fill="none" stroke="#7b7bf0"
        stroke-width="8" stroke-linecap="butt"/>
  <path d="M8.4 24.2 22 20.5l-3.1 13.7z" fill="#7b7bf0"/>
  <circle cx="32" cy="32" r="9.6" fill="#e8eaf2"/>
  <circle cx="32" cy="32" r="4" fill="#5b5bd6"/>
</svg>
"""


def build_og(mark: Image.Image) -> Image.Image:
    """1200x630 link-preview card."""
    W, H = 1200, 630
    img = vgradient((W, H), (27, 28, 54), BG_BOTTOM).convert("RGB")

    # Two soft accent lights so the card reads as lit, never flat black.
    # Radii are large enough to fade out before any edge (no seam lines).
    lamp = Image.new("RGB", (W, H), ACCENT)
    img = Image.composite(lamp, img, glow_at((W, H), (250, 150), 620, 0.40))
    img = Image.composite(lamp, img, glow_at((W, H), (1060, 620), 520, 0.16))

    d = ImageDraw.Draw(img)
    d.line([(0, 0), (W, 0)], fill=ACCENT, width=9)

    # Glyph floats on the card (no backdrop) so it doesn't look like a sticker.
    img.paste(mark, (84, 92), mark)

    f_word = font(F_BOLD, 92)
    f_tag = font(F_REG, 35)
    f_chip = font(F_SEMI, 25)

    x = 84 + 216 + 34
    d.text((x, 118), "Spare", font=f_word, fill=INK)
    wspare = d.textlength("Spare", font=f_word)
    d.text((x + wspare, 118), "Cycles", font=f_word, fill=ACCENT)
    d.text((x + 4, 228), "donate your idle AI compute", font=f_tag, fill=MUTED)

    d.text((84, 362), "Some devs have tokens and no ideas.", font=f_tag, fill=INK)
    d.text((84, 408), "Some have ideas and no tokens.", font=f_tag, fill=INK)

    chips = ["claude", "gpt", "grok", "gemini", "ollama", "LM Studio"]
    cx = 84
    for label in chips:
        tw = d.textlength(label, font=f_chip)
        d.rounded_rectangle([cx, 490, cx + tw + 40, 540], radius=25,
                            outline=(62, 66, 88), width=2)
        d.text((cx + 20, 501), label, font=f_chip, fill=MUTED)
        cx += tw + 56

    d.text((84, 566), "a marketplace of vibe-coding projects  ·  "
                      "GoFundMe, but the currency is tokens",
           font=font(F_REG, 26), fill=(116, 122, 146))
    return img


def main() -> None:
    os.makedirs(WEB, exist_ok=True)
    out = lambda n: os.path.join(WEB, n)  # noqa: E731

    with open(out("favicon.svg"), "w", encoding="utf-8") as f:
        f.write(FAVICON_SVG)

    # Browser tab icons (+ .ico so bare /favicon.ico requests are covered).
    sizes = [16, 32, 48]
    icons = {n: draw_mark(n) for n in sizes}
    for n, im in icons.items():
        im.save(out(f"favicon-{n}.png"))
    icons[48].save(out("favicon.ico"), sizes=[(n, n) for n in sizes])

    # iOS home screen: 180x180, fully opaque (iOS applies its own mask and
    # composites transparency onto black, which would look broken).
    draw_mark(180).convert("RGB").save(out("apple-touch-icon.png"))

    # Android / PWA install icons.
    draw_mark(192).save(out("icon-192.png"))
    draw_mark(512).save(out("icon-512.png"))
    # Maskable: extra padding so Android's aggressive crop can't clip the glyph.
    draw_mark(512, pad=0.22).save(out("maskable-512.png"))

    build_og(draw_mark(216, backdrop=False)).save(out("og-image.png"),
                                                  optimize=True)

    print("wrote:", ", ".join(sorted(
        f for f in os.listdir(WEB)
        if f.endswith((".png", ".ico", ".svg")))))


if __name__ == "__main__":
    main()
