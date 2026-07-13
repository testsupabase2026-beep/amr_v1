"""
arabic.py
=========
Utilities for Arabic text reshaping and BiDi display.
Also handles optional reportlab Arabic font registration.
"""
import arabic_reshaper
from bidi.algorithm import get_display

# ── reportlab font setup (optional) ───────────────────────────
_AR_FONT  = "Helvetica"
_AR_FONT_B = "Helvetica-Bold"

try:
    import os
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # Bundled Arabic font shipped in the repo (assets/fonts/). Tried FIRST so the
    # PDF renders identically on ANY host — Windows, Linux, Docker, Railway —
    # regardless of which fonts the OS happens to have installed. Without this the
    # code silently fell back to Helvetica (no Arabic glyphs) on Linux, and every
    # Arabic character came out as a "missing glyph" box (▯) in the PDF.
    _PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _BUNDLED_FONTS = os.path.join(_PKG_ROOT, "assets", "fonts")

    # Search dirs, in priority order: bundled → Windows → common Linux locations.
    _FONT_DIRS = [
        _BUNDLED_FONTS,
        r"C:\Windows\Fonts",
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
        "/usr/share/fonts",
        "/usr/share/fonts/truetype",
        "/usr/local/share/fonts",
        os.path.expanduser("~/.fonts"),
    ]

    def _find_font(filename: str) -> str | None:
        """Return the first existing path for `filename` across all font dirs
        (recursively for the Linux trees, which nest fonts in sub-folders)."""
        for d in _FONT_DIRS:
            if not d or not os.path.isdir(d):
                continue
            direct = os.path.join(d, filename)
            if os.path.exists(direct):
                return direct
            # Linux font trees nest (e.g. dejavu/DejaVuSans.ttf) — walk them.
            if d.startswith("/"):
                for root, _dirs, files in os.walk(d):
                    if filename in files:
                        return os.path.join(root, filename)
        return None

    def _try_register(name: str, *candidates: str) -> bool:
        """Register the first candidate font file that exists, under `name`."""
        for filename in candidates:
            fp = _find_font(filename)
            if fp:
                try:
                    pdfmetrics.registerFont(TTFont(name, fp))
                    return True
                except Exception:
                    pass
        return False

    # Regular: prefer bundled Noto Naskh Arabic, then Windows/Linux Arabic-capable fonts.
    if _try_register(
        "ArabicFont",
        "NotoNaskhArabic-Regular.ttf",  # bundled (repo)
        "arial.ttf", "tahoma.ttf", "verdana.ttf",              # Windows
        "NotoSansArabic-Regular.ttf", "Amiri-Regular.ttf",     # Linux (if apt-installed)
        "DejaVuSans.ttf",                                      # last resort (no Arabic, but Latin)
    ):
        _AR_FONT = "ArabicFont"

    # Bold: dedicated bold file, else reuse the regular under the bold name.
    if _try_register(
        "ArabicFontB",
        "NotoNaskhArabic-Bold.ttf",  # bundled (repo)
        "arialbd.ttf",                                          # Windows
        "NotoSansArabic-Bold.ttf", "Amiri-Bold.ttf",           # Linux
        "DejaVuSans-Bold.ttf",
    ):
        _AR_FONT_B = "ArabicFontB"
    elif _AR_FONT != "Helvetica":
        # No dedicated bold found — register the regular font under the bold name too
        # so bold styles at least still show Arabic (just not visually bolder).
        if _try_register(
            "ArabicFontB",
            "NotoNaskhArabic-Regular.ttf",
            "arial.ttf", "tahoma.ttf", "verdana.ttf",
            "NotoSansArabic-Regular.ttf", "Amiri-Regular.ttf",
        ):
            _AR_FONT_B = "ArabicFontB"

except ImportError:
    pass  # reportlab not available


# ── Public helpers ─────────────────────────────────────────────

def _sanitize_text(text: str) -> str:
    """Replace non-breaking hyphens and em/en-dashes with standard hyphens."""
    text = str(text)
    return text.replace("‑", "-").replace("—", "-").replace("–", "-")


def fix_arabic(text: str) -> str:
    """Reshape Arabic text for proper glyph joining (use in plotly/matplotlib labels)."""
    return arabic_reshaper.reshape(_sanitize_text(text))


def _ar_str(text: str) -> str:
    """Reshape + apply BiDi algorithm (use for single-line reportlab or console output)."""
    return get_display(arabic_reshaper.reshape(_sanitize_text(text)))


def _ar_pdf(text: str) -> str:
    """
    Reshape Arabic for multi-line reportlab Paragraphs.
    Uses reshape-only (NO BiDi reversal) so reportlab wrapping stays correct.
    Right-align the paragraph style separately.
    """
    return arabic_reshaper.reshape(_sanitize_text(text))

