"""Render the raw PlayStation prompt PNGs from the CC0 Kenney SVGs.

Dev tool only (players never run this). Needs: pip install cairosvg pillow numpy scipy

Writes raw/ps_prompts/<style>/<glyph>.png at 512x512, straight alpha:
  hud         white glyph, symbol cut out (in-game HUD / menus prompts)
  help        light gray disc, black symbol (small help bar icons, like the game's own)
  help_dark   black disc, white symbol
  help_light  white disc, black symbol
"""
import os, io
import numpy as np
from PIL import Image
from scipy import ndimage
import cairosvg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG = os.path.join(ROOT, "third_party", "kenney_input_prompts")
OUT = os.path.join(ROOT, "raw", "ps_prompts")
N = 512

GLYPHS = {
    "cross": "playstation_button_cross", "circle": "playstation_button_circle",
    "square": "playstation_button_square", "triangle": "playstation_button_triangle",
    "l1": "playstation_trigger_l1", "l2": "playstation_trigger_l2",
    "r1": "playstation_trigger_r1", "r2": "playstation_trigger_r2",
    "create": "playstation5_button_create", "options": "playstation5_button_options",
    "touchpad": "playstation5_touchpad",
    "dpad_up": "playstation_dpad_up", "dpad_down": "playstation_dpad_down",
    "dpad_left": "playstation_dpad_left", "dpad_right": "playstation_dpad_right",
    "dpad_leftright": "playstation_dpad_horizontal", "dpad_updown": "playstation_dpad_vertical",
    "l3": "playstation_button_l3", "r3": "playstation_button_r3",
}
for s in "lr":
    GLYPHS[f"{s}stick"] = f"playstation_stick_{s}"
    for d, k in [("up", "up"), ("down", "down"), ("left", "left"), ("right", "right"),
                 ("leftright", "horizontal"), ("updown", "vertical")]:
        GLYPHS[f"{s}stick_{d}"] = f"playstation_stick_{s}_{k}"

HUD_ONLY = {k for k in GLYPHS if "stick" in k or k in ("l3", "r3")}
HIGHLIGHT = "#E73246"  # Kenney's d-pad highlight colour


def render(svg_text):
    png = cairosvg.svg2png(bytestring=svg_text.encode(), output_width=N, output_height=N)
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA")).astype(np.float32)


def two_layers(name):
    """Return (body_alpha, highlight_alpha) in 0..1 for an SVG."""
    txt = open(os.path.join(SVG, name + ".svg"), encoding="utf-8").read()
    img = render(txt)
    a = img[..., 3] / 255
    if HIGHLIGHT in txt:
        red = img[..., 1] < 150          # highlight is red, body is white
        return a * ~red, a * red
    return a, np.zeros_like(a)


def holes(alpha):
    """Pixels enclosed by the glyph (the cut-out symbol), as soft alpha."""
    solid = alpha > 0.5
    outside = ndimage.binary_fill_holes(solid)
    inside = outside & ~solid
    inside = ndimage.binary_dilation(inside, iterations=2) & (alpha < 0.999)
    return np.clip(inside.astype(np.float32) * (1 - alpha), 0, 1) if inside.any() else np.zeros_like(alpha)


def compose(layers):
    """layers: list of (alpha, rgb) painted bottom to top. Returns straight-alpha RGBA uint8."""
    acc = np.zeros((N, N, 3), np.float32); A = np.zeros((N, N), np.float32)
    for a, rgb in layers:
        acc = acc * (1 - a[..., None]) + np.array(rgb, np.float32) * a[..., None]
        A = A * (1 - a) + a
    rgb = np.where(A[..., None] > 0, acc / np.maximum(A[..., None], 1e-6), 0)
    return np.dstack([np.clip(rgb, 0, 255), np.clip(A * 255, 0, 255)]).round().astype(np.uint8)


def main():
    for style in ("hud", "help", "help_dark", "help_light"):
        os.makedirs(os.path.join(OUT, style), exist_ok=True)
    for glyph, name in GLYPHS.items():
        body, hi = two_layers(name)
        dpad = hi.any()
        # HUD: white glyph, symbol cut out; d-pad = dim arms + white pressed arm
        hud = compose([(body, (120, 124, 130) if dpad else (255, 255, 255)), (hi, (255, 255, 255))])
        Image.fromarray(hud, "RGBA").save(os.path.join(OUT, "hud", glyph + ".png"))
        if glyph in HUD_ONLY:
            continue
        sym = holes(np.maximum(body, hi))
        for style, disc, ink in (("help", (178, 178, 178), (12, 12, 12)),
                                 ("help_dark", (12, 12, 12), (255, 255, 255)),
                                 ("help_light", (255, 255, 255), (12, 12, 12))):
            if dpad:
                img = compose([(body, disc), (hi, ink)])
            else:
                img = compose([(body, disc), (sym, ink)])
            Image.fromarray(img, "RGBA").save(os.path.join(OUT, style, glyph + ".png"))
    print("rendered", len(GLYPHS), "glyphs into", OUT)


if __name__ == "__main__":
    main()
