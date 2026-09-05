"""AnvilCode visual identity — one place for colors, glyphs and banner art.

Design rules:
- palette: light blue -> white gradients on a dark base
- the TUI uses its own custom visuals (pixel-art icon, gradient wordmark,
  ASCII glyphs) — no emoji in the chrome itself (model replies may use them)
"""
from __future__ import annotations

from rich.text import Text

# ---------------- palette (blue -> white) ----------------

BLUE_DEEP = "steel_blue1"      # borders, secondary
BLUE = "light_sky_blue1"       # primary
BLUE_SOFT = "sky_blue1"        # mid gradient step
BLUE_PALE = "light_steel_blue" # near-white blue
WHITE = "bright_white"         # highlights

BANNER_COLORS = [BLUE_DEEP, BLUE, BLUE_SOFT, BLUE_PALE, WHITE]  # fallback per letter

# gradient stops for the wordmark + icon art (light blue -> white)
GRAD_TOP = (125, 211, 252)     # #7dd3fc
GRAD_BOTTOM = (248, 250, 252)  # #f8fafc

THEME = {
    "primary": f"bold {BLUE}",
    "accent": f"bold {WHITE}",
    "ok": BLUE,
    "warn": "yellow",
    "err": "bold red",
    "dim": "grey58",
    "tool": f"bold {BLUE_DEEP}",
}

# ---------------- glyphs (deliberately not emoji) ----------------

G = {
    "brand": "»",     # tool / action prefix
    "prompt": "❯",    # input prompt
    "ok": "+",
    "warn": "!",
    "err": "x",
    "info": "·",
    "dot": "·",
    "arrow": "->",
    "cursor": "❯",
}

# ---------------- banner ----------------

# "ANVIL" in FIGlet ANSI-Shadow style, 6 rows per letter.
_LETTERS = {
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "N": ["███╗   ██╗", "████╗  ██║", "██╔██╗ ██║", "██║╚██╗██║", "██║ ╚████║", "╚═╝  ╚═══╝"],
    "V": ["██╗   ██╗", "██║   ██║", "██║   ██║", "╚██╗ ██╔╝", " ╚████╔╝ ", "  ╚═══╝  "],
    "I": ["██╗", "██║", "██║", "██║", "██║", "╚═╝"],
    "L": ["██╗     ", "██║     ", "██║     ", "██║     ", "███████╗", "╚══════╝"],
}
_WORD = "ANVIL"

WORDMARK_WIDTH = sum(len(_LETTERS[c][0]) for c in _WORD) + len(_WORD)  # 43


def _grad(t: float) -> str:
    """Interpolate GRAD_TOP -> GRAD_BOTTOM (t in 0..1) as an rgb() style."""
    r = int(GRAD_TOP[0] + (GRAD_BOTTOM[0] - GRAD_TOP[0]) * t)
    g = int(GRAD_TOP[1] + (GRAD_BOTTOM[1] - GRAD_TOP[1]) * t)
    b = int(GRAD_TOP[2] + (GRAD_BOTTOM[2] - GRAD_TOP[2]) * t)
    return f"rgb({r},{g},{b})"


def banner_rows() -> list:
    """The wordmark with a true blue->white gradient fill, one Text per row.

    Gradient runs mostly vertically (top blue -> bottom white) with a slight
    diagonal lean, per character.
    """
    n_rows = 6
    total_cols = sum(len(_LETTERS[c][0]) + 1 for c in _WORD)
    out = []
    for row in range(n_rows):
        t = Text()
        col = 0
        for ch in _WORD:
            glyphs = _LETTERS[ch]
            for gc in glyphs[row]:
                tt = (row / (n_rows - 1)) * 0.8 + (col / max(1, total_cols - 1)) * 0.2
                t.append(gc, style=_grad(tt))
                col += 1
            t.append(" ", style=_grad(row / (n_rows - 1)))
            col += 1
        out.append(t)
    return out
