# LinTranslator

Screen-region OCR + auto-translation for game dialogue. Point it at the dialogue
box once, and it watches that rectangle, reads the text, and translates it.

Built and verified against **Limbus Company** on **KDE Plasma 6 / Wayland**, but
nothing is game-specific: the region is a rectangle, and the OCR and translation
layers are generic. Linux only - Wayland preferred, X11 works.

Current state: **[0.1.0](CHANGELOG.md)**, Phase 3 complete (pipeline + GUI + int8
backend + glossary).

![The region picker: a selection box with drag handles sits over a two-line dialogue box, and the sidebar shows the OCR preview, the recognized text, and the translation controls](docs/picker.png)

*The picker, with the box on the dialogue text and both previews on. The scene
behind it is drawn by `probe/make_render_frame.py`, not captured - the GUI renders
over whatever frame it is given, and a real screenshot would carry a game's
artwork and the desktop it was taken on.*

## Documentation

| | |
|---|---|
| **[Install](docs/install.md)** | system packages, the one-time model conversion and what it costs in disk, where state is kept, model licences |
| **[Using it](docs/guide.md)** | the picker and the panel, hotkeys, the command line, backends, language pairs, glossary, custom endpoints |
| **[Why it works this way](docs/design.md)** | the measurements behind the capture, OCR and translation choices, and the pipeline's behaviour |
| **[Development](docs/development.md)** | code layout, the test suite, the roadmap, the full list of limitations |

The rest of this page is the short version: what it is, how to get it running,
and what it does not do yet.

## How this differs from LunaTranslator

The name is a nod to [LunaTranslator](https://github.com/HIllya51/LunaTranslator),
which solves the same problem on Windows and is the reason this exists: on Linux
it runs under Wine/Proton, and its main mechanism — hooking the game's text calls
— is exactly what is awkward to make work there.

LinTranslator is built the other way round:

|  | LunaTranslator | LinTranslator |
|---|---|---|
| how it gets the text | hooks the process (OCR is the fallback) | **only** reads a screen region with OCR - the game is never touched |
| how it runs on Linux | Wine/Proton, with community workarounds | native: xdg-desktop-portal over D-Bus, GTK4 |
| what it needs from the game | that it be hookable | nothing but pixels in a rectangle |

The trade is real, and worth stating plainly: no hooks means no menu text, no
inventory, nothing off-screen or behind a fade — only what is drawn in the region
you selected. What it buys is that it works on any game, including ones that
cannot be hooked at all, and that it needs no injection, no Wine prefix and no
per-game setup.

## Quick start

```bash
.venv/bin/python -m lintranslator gui     # picks a region, then starts translating
```

Everything after that is inside the GUI: **Capture** the screen, drag a box over
the dialogue text, **Settings** to choose a backend, paste an API key, pick a model
and set the prompt, then **Start translating**. No shell commands, no environment
variables required.

The picker also shows the translation of the current selection as you adjust the
region, which is the quickest way to judge a model or prompt without launching the
panel.

For the global Re-read hotkey — the one that works while the game has focus — run
`lintranslator install-desktop` once and start the app from the application menu
rather than a terminal. That launch is what gives it the application id the
compositor's shortcut portal demands; [the hotkey
section](docs/guide.md#re-read-when-a-line-comes-out-wrong) covers what to do when
that is not available.

## Install

There is no package yet - no AUR, Flatpak or PyPI entry - so this starts from the
source. It is five commands and one long download.

```bash
# 1. the code
git clone https://github.com/tanaka774/lintranslator
cd lintranslator

# 2. tesseract and the GTK stack. PyGObject and pycairo come from the distro
#    rather than from pip: they bind system libraries.
sudo pacman -S tesseract python-gobject python-cairo          # Arch / CachyOS
# sudo apt install tesseract-ocr python3-gi python3-gi-cairo python3-cairo \
#                  gir1.2-gtk-4.0                              # Debian / Ubuntu

# 3. environment. The interpreter must be the distro's own Python: PyGObject is a
#    compiled binding to it, and --system-site-packages is what exposes it.
uv venv --python /usr/bin/python3 --system-site-packages .venv
uv pip install --python .venv/bin/python -e '.[ct2]'

# 4. the translation model, converted to int8 once. ~3 GB on disk while it runs.
.venv/bin/python -m lintranslator convert

# 5. what is missing, in plain language, with a non-zero exit if anything is.
.venv/bin/python -m lintranslator check
```

No `uv`? It only creates the venv and installs into it; `python3 -m venv
--system-site-packages .venv` and `.venv/bin/pip install -e '.[ct2]'` do the same.
There is also no need to clone: `uv pip install --python .venv/bin/python
"lintranslator[ct2] @ git+https://github.com/tanaka774/lintranslator"` installs
straight from the repository, which is what
[the install notes](docs/install.md#install) suggest to anyone who would rather
not keep a checkout around.

`.[ct2]` is the fast path: int8 NLLB through CTranslate2, no torch. `.[local]` is
the older transformers route for models that cannot be converted, `.[x11]` adds
keep-above support under XWayland, and `.[calibrate]` adds the region
auto-detection helper. tesseract language data is fetched on first use, pinned and
checksum-verified, so no root is needed for it.

Nothing deletes the 2.5 GB fp32 checkpoint after the conversion. `lintranslator
remove` reports what is on disk and takes any of it back —
[the install notes](docs/install.md#what-the-conversion-actually-costs) give the
sizes and the reasoning.

## Compatibility

| target | status |
|---|---|
| **Linux + KDE Plasma 6 / Wayland** | **verified** - the environment every measurement here was taken on |
| **Linux + X11 / XWayland** | works, and window placement and keep-above genuinely work here |
| **Linux + Wayland, other compositors** | depends on the portal backend; the hotkey and the keep-above workaround are the KDE-specific parts |
| **Linux + GNOME** | core expected to work, untested here; `xdg-desktop-portal-gnome` does not implement the shortcut portal at all, so bind the hotkey by hand |
| **macOS / Windows** | **not supported** - not merely untested: the capture and hotkey layers have nothing to talk to |

Linux is a hard requirement, not a preference. Capture, the global hotkey and the
control socket all assume it, and none of them has a fallback. The detail - which
portal interface each layer needs and what happens when it is absent - is in
[Compatibility](docs/install.md#compatibility).

The GUI itself is plain GTK4, so it is not a KDE application: it runs on any
Wayland or X11 desktop with PyGObject.

## Known limitations

- Verified against a reference frame and live static screen content, not a long
  play session. Expect to tune the region per game.
- English source text is what has been exercised. Japanese or Korean *source* OCR
  is untested, though `--langs eng+jpn` is wired up.
- Only KDE Plasma 6 / Wayland has actually been run. X11, GNOME and other
  compositors are analysed, not exercised.
- GPU inference is untested: the `ct2` backend runs on CPU threads here.
- The PipeWire `ScreenCast` backend is implemented but not wired to the pipeline;
  the `portal-screenshot` path is the default and is fast enough at 2 fps.
- The panel cannot position itself under the dialogue box on native Wayland, where
  stacking belongs to the compositor. Drag it into place, or use XWayland.
- Screen reading only: no memory reading, no injection, no game file patching.

[The full list](docs/development.md#known-limitations) also covers what is
verified offscreen and how, and what happens while the picker is open.

## Licence

LinTranslator is MIT — see [LICENSE](LICENSE).

The models are not all the same, and one of them matters:

**The default local model, `facebook/nllb-200-distilled-600M`, is CC-BY-NC-4.0 —
non-commercial use only.** Nothing is bundled or redistributed: `lintranslator
convert` downloads the weights onto your machine, so the licence binds whoever
runs the local backend rather than whoever distributes this source. For commercial
use, pick a hosted backend (DeepL, OpenRouter, OpenAI, or your own endpoint) or a
permissive model such as `facebook/m2m100_418M` (MIT). [NOTICE](NOTICE) has the
third-party terms in full, and
[the install notes](docs/install.md#model-licences) explain the trade-offs.
