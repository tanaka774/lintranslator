# Phase 1 results — headless pipeline

Built and verified end to end. Environment: CachyOS, KDE Plasma 6.7.4 / kwin_wayland,
AMD RX 9070, Python 3.12, tesseract 5.5.3.

## Verified working

| Stage | Result | Notes |
|---|---|---|
| portal capture | **works** | 2560x1440 grab, **~0.21 s** steady state, no dialog after first call |
| permission persistence | **works** | fresh processes reuse the grant silently (0.4 s wall per grab) |
| region crop | works | fraction-based, resolution independent |
| OCR (module) | works | 2 lines, **94% confidence**, ~105-230 ms |
| translation (module) | works | NLLB-600M, **~1.7 s/line**, cache hits 0 ms |
| full `run` loop | works | 47 polls / 46 grabs, 0 errors, JSON output |
| auto-calibration | works | finds the dialogue block, 84% confidence |

Measured translation output:

```
EN: [The committee has resolved that this entry warrants retention as a standing record.
     The material below is the file concerning today's submission.]
JA: [この事件は記録として保存されるべきであると決定された.
     今日の要請に関する事件記録は以下のとおりです.]
```

## Bugs found and fixed during implementation

### 1. Region was 20px too short — line 2 silently cut in half

The first hand-tuned region (`y 693-748`) read line 1 perfectly and truncated
line 2, because line 2 actually sits at `y 762-775`, not `y 718-743`. The OCR
output still looked plausible, which is what makes this class of bug dangerous.

Row positions were established by profiling bright pixels **in the text column
range only** (`x 290-1000`); profiling the full width was fooled twice by the
orange "butterfly" background art, which is both brighter and denser than the
text.

### 2. Change detection starved the settler (pipeline never translated)

First live run: 38 polls, 1 change, 1 OCR, **0 translations**.

The settler required N identical *consecutive OCR reads*. But OCR only runs when
pixels change, so a static screen produces exactly one read and the text never
settles. Fixed by settling across a single sample delay (hold the first fresh
reading, release on confirmation) plus a time-based release (`settle_max_wait`)
for text that appears and then stops changing.

### 3. Mean-difference change detection missed the typewriter

`min_mean_delta` used the mean absolute difference over the whole region, so
appending one word to a long line barely moved it and the reveal was not
detected. Replaced with `changed_fraction`: the share of samples differing by at
least 12 gray levels, triggered at `max(1, 0.0005 * n)`.

The absolute floor matters. At stride 4 a 320x60 region yields only 1200 samples,
and changing one character moves **1** of them; a pure fraction would miss it.

### 4. Junk lines were being translated

HUD chrome leaking into the region produced low-confidence lines that were fed to
the translator, costing ~1.5 s each and corrupting the output
(`translate_avg_ms` 3196 -> 2723, and two junk fragments emitted). Fixed by
dropping OCR lines below `min_confidence` (55) and collapsing whitespace in the
joined passage.

### 5. Config silently ignored a renamed key

After renaming `min_mean_delta` -> `min_changed_fraction`, the existing
`config.json` still held the old key and the setting was silently ignored. Config
loading now reports unknown keys (`ignoring unknown config key(s): ...`).

## Calibration: what actually separates text from background art

Two signals, measured on the reference screenshot:

| region | ink fraction | edge fraction |
|---|---|---|
| real dialogue | 0.058 | **0.149-0.175** |
| orange butterfly art | 0.206 | 0.030-0.046 |

Edge fraction (share of horizontally adjacent pixel pairs differing by >25 gray
levels) is the discriminator: glyph strokes produce hard transitions, smooth
gradient artwork does not. The metric must be computed over ink-bearing rows only
- averaging across a merged two-line block including the inter-line gap drops it
to 0.090 and rejects real text.

## Alternatives measured and rejected

- `Helsinki-NLP/opus-mt-en-jap`: hallucinated output unrelated to the input.
- rapidocr / PP-OCRv4: lost word spaces, 4x slower than tesseract here.
- X11 `root.get_image` via XWayland: `BadMatch` on a 24-bit root; the portal is
  the correct path on Wayland anyway.
- Auto-calibration via brightness alone: fails, art is brighter than text.

## Not yet verified

- Capture against the **live game** (verification used the reference screenshot
  plus live desktop content; the pipeline itself is screen-agnostic).
- Japanese/Korean **source** OCR. `--langs eng+jpn` is wired up but untested.
- The PipeWire `ScreenCast` consumer: `portal.py` implements session setup, but
  no GStreamer frame reader is wired in. `portal-screenshot` is the default and
  is fast enough at 2 fps.

## Reproduce

```bash
.venv-gi/bin/python -m lintranslator check
.venv-gi/bin/python -m lintranslator grab -o /tmp/frame.png      # eyeball the region
.venv-gi/bin/python -m lintranslator read --repeat 3             # OCR only
.venv-gi/bin/python -m pytest tests/ -q                  # 21 tests
.venv-gi/bin/python -m lintranslator run --duration 20
```
