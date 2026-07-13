# Fix: Arabic text renders as black boxes (▯) in the exported PDF on Linux / Railway

> **Audience:** another LLM (or engineer) who must reproduce this fix from scratch,
> either in this repo or in a sibling project that has the same bug.
> **Golden rule of this project:** every fix must target the *root-cause class*, not the
> single failing case. This document follows that rule.

---

## 1. The symptom

- The user deploys the app (here: on **Railway**, a Linux container) and downloads the
  **session-report PDF** from the UI.
- In that PDF, **every Arabic character is a small dark rectangle** (`▯` — the
  "missing glyph" / `.notdef` box).
- **Latin letters and digits render fine** — e.g. `2025`, `78.1`, `25.1%`, the English
  headings ("NAMAA Analytics Agent - Session Report", "Query History", "Summary").
- The **exact same code produces a correct PDF on the developer's Windows machine.**
  It only breaks on the deployed Linux host.

## 2. The WRONG diagnosis (do not go here)

This looks like a text-**encoding** problem, but it is **not**.

- The Arabic text in the PDF is correct Unicode. If it were an encoding problem you'd see
  mojibake (`Ø§Ù„Ø¥...`), question marks, or empty space — **not** uniform, evenly-sized
  boxes.
- Uniform boxes of the *same size as the surrounding text* are the universal signature of
  **"the font has no glyph for this codepoint."** The renderer knows there's a character
  there; it just has no shape to draw, so it draws `.notdef` (the box).

Do **not** waste time on: `encode('utf-8')`, `arabic_reshaper`, `python-bidi`, changing the
DB collation, or re-saving files. Those are all upstream of the real problem and are
already working (proof: the boxes are correctly *positioned* and *counted*).

## 3. The ROOT CAUSE

The PDF is generated with **reportlab**. reportlab does **not** fall back across fonts —
it draws every glyph from the *one* font you set on the paragraph/canvas. If that font
lacks Arabic glyphs, you get boxes.

The font-registration code searched for an Arabic-capable TTF in **Windows-only paths**:

```python
# utils/arabic.py — BEFORE (buggy)
_WIN_FONTS = [ r"C:\Windows\Fonts", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts") ]

def _try_register(name, filename):
    for d in _WIN_FONTS:
        fp = os.path.join(d, filename)
        if os.path.exists(fp):
            pdfmetrics.registerFont(TTFont(name, fp)); return True
    return False

for _reg_file in ("arial.ttf", "tahoma.ttf", "verdana.ttf"):
    if _try_register("ArabicFont", _reg_file):
        _AR_FONT = "ArabicFont"; break
```

with a fallback default of:

```python
_AR_FONT  = "Helvetica"        # a PDF built-in font — HAS NO ARABIC GLYPHS
_AR_FONT_B = "Helvetica-Bold"
```

**What happens on each host:**

| Host | `C:\Windows\Fonts\arial.ttf` exists? | Registered font | Arabic result |
|------|--------------------------------------|-----------------|---------------|
| Developer's Windows | ✅ yes | `ArabicFont` (Arial) | ✅ renders |
| Railway / Docker / Linux | ❌ no | falls back to `Helvetica` | ▯ boxes |

On Linux the registration silently fails (the file simply isn't there), `_AR_FONT` stays
`"Helvetica"`, and Helvetica has zero Arabic glyphs → every Arabic char becomes a box.
Latin/digits survive because Helvetica *does* have those.

**This is a portability bug: the code assumed the developer's OS fonts exist everywhere.**

## 4. The FIX (host-independent — the important part)

The only robust, general fix is: **stop depending on whatever fonts the host OS happens to
have. Ship your own Arabic font inside the repo and register it first.** Then the PDF
renders identically on Windows, Linux, Docker, Railway, a teammate's laptop — anywhere.

Three layers, in priority order:

1. **Bundle an Arabic font in the repo** and register it **first**. (the real fix)
2. **Also search Linux system font dirs** (recursively) as a fallback.
3. **Also `apt-get install` a Noto font in the Dockerfile** as a system-level backup.

### Step 4.1 — Choose and vendor a license-safe Arabic font

Use an **SIL OFL** font (free to redistribute inside the repo). Good choices:
**Noto Naskh Arabic**, **Noto Sans Arabic**, or **Amiri**.

> ⚠️ **Do NOT use DejaVu Sans** for this — it has *no* Arabic glyphs. It's fine only as a
> last-resort Latin fallback.
>
> ⚠️ **Avoid variable fonts** (a single `NotoNaskhArabic[wght].ttf`). reportlab's
> `TTFont` handles static instances far more reliably. Fetch the **static** Regular + Bold
> `.ttf` files.

Download the **static** TTFs into `assets/fonts/`:

```bash
mkdir -p assets/fonts
BASE="https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoNaskhArabic/unhinted/ttf"
curl -sL -o assets/fonts/NotoNaskhArabic-Regular.ttf "$BASE/NotoNaskhArabic-Regular.ttf"
curl -sL -o assets/fonts/NotoNaskhArabic-Bold.ttf    "$BASE/NotoNaskhArabic-Bold.ttf"
# license (OFL requires shipping it):
curl -sL -o assets/fonts/OFL.txt "https://raw.githubusercontent.com/notofonts/arabic/main/OFL.txt"
```

**ALWAYS validate the download** — GitHub returns a 200-but-HTML page for wrong paths, and
you'll silently commit a broken "font." A real TrueType file starts with magic bytes
`00 01 00 00`:

```bash
for f in assets/fonts/*.ttf; do
  printf "%-45s " "$f"; head -c4 "$f" | xxd -p     # MUST print: 00010000
done
```

If you see `0a0a0a0a` or `3c21...` (`<!`), it's an HTML error page — the URL is wrong.
Try the alternate path `notofonts/notofonts.github.io .../unhinted/ttf/...`, or the
Google Fonts mirror, until the magic bytes are `00010000`.

### Step 4.2 — Confirm the font actually contains Arabic glyphs

Before wiring it in, verify reportlab can register it AND that it has an Arabic glyph
(U+0628 ARABIC LETTER BEH `ب`):

```python
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
pdfmetrics.registerFont(TTFont("t", "assets/fonts/NotoNaskhArabic-Regular.ttf"))
cmap = pdfmetrics.getFont("t").face.charToGlyph
assert cmap.get(0x0628, 0) != 0, "font has NO Arabic glyphs — wrong font!"
print("OK: Arabic glyph present ->", cmap[0x0628])
```

### Step 4.3 — Rewrite the font-registration block

Replace the Windows-only search with a **prioritized, cross-platform** search that tries
the bundled font first, then Windows, then Linux dirs (recursively — Linux nests fonts in
sub-folders like `dejavu/DejaVuSans.ttf`).

```python
# utils/arabic.py — AFTER (fixed)
_AR_FONT  = "Helvetica"          # only reached if literally nothing else is found
_AR_FONT_B = "Helvetica-Bold"

try:
    import os
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    _PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _BUNDLED_FONTS = os.path.join(_PKG_ROOT, "assets", "fonts")

    # Priority order: bundled (repo)  →  Windows  →  common Linux locations.
    _FONT_DIRS = [
        _BUNDLED_FONTS,
        r"C:\Windows\Fonts",
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
        "/usr/share/fonts", "/usr/share/fonts/truetype",
        "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
    ]

    def _find_font(filename):
        for d in _FONT_DIRS:
            if not d or not os.path.isdir(d):
                continue
            direct = os.path.join(d, filename)
            if os.path.exists(direct):
                return direct
            if d.startswith("/"):                 # Linux trees nest fonts — walk them
                for root, _dirs, files in os.walk(d):
                    if filename in files:
                        return os.path.join(root, filename)
        return None

    def _try_register(name, *candidates):
        for filename in candidates:
            fp = _find_font(filename)
            if fp:
                try:
                    pdfmetrics.registerFont(TTFont(name, fp)); return True
                except Exception:
                    pass
        return False

    if _try_register("ArabicFont",
                     "NotoNaskhArabic-Regular.ttf",           # bundled (repo) — tried FIRST
                     "arial.ttf", "tahoma.ttf", "verdana.ttf", # Windows
                     "NotoSansArabic-Regular.ttf", "Amiri-Regular.ttf",  # Linux (apt)
                     "DejaVuSans.ttf"):                        # last resort (Latin only)
        _AR_FONT = "ArabicFont"

    if _try_register("ArabicFontB",
                     "NotoNaskhArabic-Bold.ttf",
                     "arialbd.ttf",
                     "NotoSansArabic-Bold.ttf", "Amiri-Bold.ttf",
                     "DejaVuSans-Bold.ttf"):
        _AR_FONT_B = "ArabicFontB"
    elif _AR_FONT != "Helvetica":
        # No dedicated bold — reuse the regular under the bold name so bold text
        # still shows Arabic (just not visually heavier).
        if _try_register("ArabicFontB",
                         "NotoNaskhArabic-Regular.ttf",
                         "arial.ttf", "tahoma.ttf", "verdana.ttf",
                         "NotoSansArabic-Regular.ttf", "Amiri-Regular.ttf"):
            _AR_FONT_B = "ArabicFontB"

except ImportError:
    pass  # reportlab not installed
```

The rest of the file (`_ar_str`, `_ar_pdf`, `fix_arabic`, `_sanitize_text`) is unchanged.
The PDF generator already reads `_AR_FONT` / `_AR_FONT_B` from this module, so no changes
are needed there.

### Step 4.4 — Make sure the font actually SHIPS (the trap)

**This is where the fix commonly fails to work.** Check `.gitignore` — many repos ignore
binary assets. This project ignored `*.png`, `*.jpg`, etc. Confirm `.ttf` is **not**
ignored, or the fonts never reach the deploy:

```bash
git check-ignore assets/fonts/NotoNaskhArabic-Regular.ttf   # empty output = trackable (good)
```

If it IS ignored, add a negation rule (e.g. `!assets/fonts/` and `!assets/fonts/*.ttf`)
right after the offending pattern.

Then confirm the blobs are really staged (not skipped):

```bash
git add assets/fonts/ utils/arabic.py
git ls-files -s assets/fonts/     # each .ttf must show a real 40-char blob hash
```

### Step 4.5 — (Optional but recommended) system-font backup in Docker

If the deploy uses a Dockerfile, add a Noto font package as a second safety net:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*
```

## 5. How to VERIFY the fix (simulate Linux/Railway on your Windows box)

Do **not** trust "it renders on my Windows machine" — that path was never broken. You must
prove the **bundled** font wins even when Windows fonts are unavailable. Disable the
Windows fallback and generate a real PDF, then assert the Arabic font is *embedded in the
PDF bytes*:

```python
import os
os.environ["WINDIR"] = "/nonexistent"          # kill the Windows font fallback

import importlib, utils.arabic as A
importlib.reload(A)
assert A._AR_FONT == "ArabicFont",  "bundled regular not picked!"
assert A._AR_FONT_B == "ArabicFontB","bundled bold not picked!"

from reportlab.pdfgen import canvas
out = os.path.join(os.environ.get("TEMP", "/tmp"), "ar_test.pdf")
c = canvas.Canvas(out); c.setFont(A._AR_FONT, 18)
c.drawString(72, 700, A._ar_str("إجمالي الإيرادات لكل فئة رئيسية 2025")); c.save()

raw = open(out, "rb").read()
assert b"NotoNaskh" in raw or b"ArabicFont" in raw, "Arabic font NOT embedded — still boxes!"
print("PASS: bundled Arabic font is embedded — renders on any host.")
```

If that prints `PASS`, the deployed PDF will render Arabic correctly.

## 6. Ship it

```bash
git add assets/fonts/ utils/arabic.py Dockerfile      # + .gitignore if you edited it
git commit -m "fix: bundle Arabic font so PDF renders Arabic on Linux/Railway (not boxes)"
git push
```

Then the user must `git pull` / let the platform **redeploy** so the new fonts + code go
live. The bug is a build-time asset problem — an already-running container won't pick it up
until it's rebuilt.

## 7. Generalization checklist (apply this reasoning to similar bugs)

- **Boxes/`.notdef` in any renderer (PDF, matplotlib, Plotly-to-image) = missing glyphs,
  not encoding.** Fix = supply a font that has the glyphs.
- **Never depend on OS-installed fonts for a deployed app.** Bundle the font in the repo
  and reference it by path. This applies to reportlab, matplotlib (`FontProperties(fname=…)`
  / `fontManager.addfont`), Pillow (`ImageFont.truetype(path)`), WeasyPrint, etc.
- **"Works on my machine, boxes on the server" ⇒ suspect an OS-provided resource** (font,
  locale, system binary like `kaleido`/`wkhtmltopdf`) that exists locally but not in the
  minimal container.
- **Always validate downloaded binary assets** by magic bytes before committing.
- **Always verify the asset is not `.gitignore`d** and is actually staged.
- **Verify by simulating the broken environment**, not the working one.

---

### Files changed by this fix (reference)

| File | Change |
|------|--------|
| `assets/fonts/NotoNaskhArabic-Regular.ttf` | **new** — bundled Arabic font (OFL) |
| `assets/fonts/NotoNaskhArabic-Bold.ttf` | **new** — bundled Arabic bold (OFL) |
| `assets/fonts/OFL.txt` | **new** — font license |
| `utils/arabic.py` | cross-platform, bundled-first font registration |
| `Dockerfile` | `apt-get install fonts-noto-core` as a backup |
| `.gitignore` | (only if binary assets were ignored) un-ignore `assets/fonts/` |
