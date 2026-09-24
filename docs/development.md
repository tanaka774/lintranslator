# Development

Where the code lives, how to run the suite, and what is deliberately not
finished yet.

## Layout

```
lintranslator/
  paths.py       XDG directories, private/0600 write helpers
  config.py      config model, JSON load/save, unknown-key warnings
  portal.py      xdg-desktop-portal D-Bus clients (Screenshot, ScreenCast)
  capture.py     full-screen grab -> crop to region
  detect.py      change detector, text settler, empty guard
  geometry.py    prompt templates (pure, tested)
  languages.py   the 202 FLORES-200 codes + each backend's code for them (data)
  settings.py    GTK settings dialog (backend, model, language pair, prompt, card)
  ocr.py         tesseract wrapper + tessdata bootstrap + preprocessing
  translate.py   NLLB (ct2, local) / DeepL / OpenRouter / OpenAI / custom endpoint + cache
  glossary.py    term overrides, pre- and post-translation
  convert.py     HuggingFace -> CTranslate2 int8 conversion
  calibrate.py   automatic dialogue-box detection
  selection.py   picker coordinate mapping and drag geometry (pure, tested)
  occlusion.py   which LinTranslator windows are on screen (the capture gate)
  selftext.py    text that is not the game's: our own UI, and confident nonsense
  control.py     the `lintranslator reread` control socket (one line in, one line out)
  hotkey.py      global shortcut via the compositor's GlobalShortcuts portal
  picker.py      GTK4 region picker window
  panel.py       GTK4 always-on-top translation panel + worker thread
  gui.py         GTK application entry point
  pipeline.py    the capture -> OCR -> translate loop
  cli.py         check / grab / read / run / region / gui / convert / models / reread / status /
                 shortcut / remove / languages / install-desktop
  data/          the `.desktop` entry and its launcher, installed by `install-desktop`
tests/           430 tests: core, geometry, pipeline, glossary, backends, languages,
                 OCR languages, paths/permissions, the desktop install, the CLI default
probe/           spike scripts, raw measurements, per-phase results
docs/            this documentation, and the picker render in the README
PLAN.md          feasibility study with the full benchmark tables
CHANGELOG.md     what changed, per release
MANIFEST.in      what the source distribution has to carry (see the sdist CI job)
.github/         the test matrix and the sdist build
```

## Roadmap

- **Phase 4 - Packaging.** AUR package, autostart, multi-monitor and HDR handling,
  RapidOCR fallback for non-Latin source languages.
- **GPU acceleration.** The RX 9070 and `hip-runtime-amd` are present; CTranslate2
  on ROCm should cut the 0.2-0.35 s/line further. Untested here because the
  sandbox has no GPU device access.
- **GUI follow-ups.** System tray icon, per-game region profiles, a glossary
  editor, and true overlay placement if `gtk4-layer-shell` becomes available.

## Known limitations

- Verified against the reference screenshot and live static screen content, not
  yet against a long live play session. Expect to tune the region per game.
- Tested with English source text. Japanese/Korean source OCR is untested, though
  `--langs eng+jpn` is wired up.
- The PipeWire `ScreenCast` backend is implemented in `portal.py` but the frame
  consumer is not wired to the pipeline yet; `portal-screenshot` is the default
  and is fast enough at 2 fps.
- GPU inference is untested: this environment has no GPU device access, so the
  `ct2` backend runs on CPU threads. ROCm acceleration is expected to work but is
  unverified.
- The panel cannot position itself under the dialogue box on native Wayland (see
  the positioning caveat above); drag it into place or use XWayland.
- Only KDE Plasma 6 / Wayland has been run. X11/XWayland, GNOME and other
  compositors are analysed in "Compatibility" but not exercised: the risks are
  the hotkey (portal missing outside KDE) and stacking.
- The GUI was rendered and its layout verified offscreen, and both windows were
  smoke-tested for clean startup. The picker's drag interactions are driven
  through their real handlers with synthetic gesture events -
  `probe/region_resize_check.py` resizes from all eight edges and corners in both
  directions - but no automated check moves a real mouse.
- While the picker is on screen, reading is paused on purpose. Self-capture is
  prevented by *not capturing*, because Wayland gives no way to test whether one
  of our windows overlaps the box; a picker parked permanently over the game
  therefore costs translation time until it is minimised.
- Screen reading only: no memory reading, no injection, no game file patching.

## Tests

```bash
uv pip install --python .venv/bin/python -e '.[test]'   # pytest and numpy
.venv/bin/python -m pytest tests/ -v
```

The suite passes on 3.12 through 3.14. On an interpreter with no PyGObject the
three GTK modules skip rather than fail (`pytest.importorskip`), so a headless or
pip-only environment still runs everything that does not draw a window.

* `tests/test_core.py` - change detection, settling, config, cache, CJK spacing
* `tests/test_selection.py` - picker coordinate mapping and drag geometry, with
  every edge and corner resizing in both directions
* `tests/test_pipeline.py` - the full loop with capture and OCR stubbed out,
  including the capture-timing rules (pause while our own window is up, re-point
  the area live, never translate our own UI)
* `tests/test_occlusion.py` - the two self-capture guards, and that real dialogue
  is never mistaken for LinTranslator's own text
* `tests/test_picker_wiring.py` - what the picker tells the pipeline, and when,
  plus the drag handlers' half of a resize and the OCR engine Settings must
  replace
* `tests/test_control.py` - the control socket: round trip, no GUI, two GUIs
* `tests/test_hotkey.py` - what the user is told when a hotkey cannot be bound
* `tests/test_glossary.py` - term matching, cache interaction, line handling
* `tests/test_local_backends.py` - the `ct2` and `local` translator construction,
  and tokenizer naming
* `tests/test_remote_backends.py` - request shaping, key resolution, failures
* `tests/test_geometry.py` - prompt templating: placeholders, stray braces, presets
* `tests/test_cli.py` - what a bare `lintranslator` means, and that a flag with no
  subcommand still reaches the GUI rather than a usage error
* `tests/test_calibrate.py` - dialogue detection, including the `near` anchoring
* `tests/test_languages.py` - the language table, per-backend codes, and the
  check that every code in it is one the real NLLB tokenizer can score
* `tests/test_paths.py` - the XDG locations and environment handling, the
  0600-at-creation write, and that no part of a failed save survives
* `tests/test_cleanup.py` - the `remove` command: what it reports, what it refuses
  to touch, and that nothing is deleted without confirmation
* `tests/test_ocr.py` - OCR language-name validation (a name becomes a filename,
  so traversal is refused), the pinned, checksum-verified tessdata download, the
  `+` form `-l` actually receives, and what the tessdata directories hold
* `tests/test_ocr_languages.py` - the chooser's generated list of models: every
  stem is one `-l` accepts, and nothing the app can download is missing from it
* `tests/test_settings_ui.py` - the backend-dependent rows, and the language
  pickers: only real codes are offered, an unknown one in the config is shown and
  warned about rather than replaced, and the OCR languages are a set that can be
  grown, trimmed and searched rather than a text field

The pipeline tests stub capture and OCR on purpose: a live screen is not a
reproducible input. Verification runs during development saw 24 "changes" in 26
polls purely because a chat window was animating, which makes live screen state
useless as a test signal.
