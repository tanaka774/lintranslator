# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

The version lives in `lintranslator/__init__.py`; the packaging metadata reads it
from there rather than keeping a second copy.

## [Unreleased]

- The install instructions no longer build the venv on a uv-managed Python. They
  said `uv venv --python 3.12 --system-site-packages`, which produces a uv-managed
  3.12 whose "system" site-packages is uv's own - so the flag that existed for
  PyGObject exposed nothing, the distro's copy (built for the system 3.14) stayed
  invisible, and the GUI could not start. Both recipes now use the distro's
  interpreter, with the reason written next to them.
- The instructions also never said how to *get* the source. They now start with
  it, and give the install-from-git form for anyone who would rather not keep a
  checkout.
- Running `lintranslator` with no subcommand opens the GUI. `gui` was always
  described as the default in `--help`, but the parser required a subcommand and
  exited 2 instead, which is the wrong answer for the thing a `.desktop` launcher
  or a double-click on the entry point does.
- The launcher script looks for a virtualenv two levels up as well as one, so it
  still finds a source checkout's `.venv` now that it lives inside the package
  rather than in a top-level `packaging/` directory.
- The picker rebuilds its OCR engine when Settings are applied. It kept the
  engine it was opened with, so changing the OCR languages wrote the config and
  fetched the model while the preview went on reading with the old one — a clean
  printed Korean line came back as "(no text found in this region)".
- The OCR languages are editable in Settings instead of only addable. The row
  appeared only when the list could not read the source, and its one button could
  only append, so a list that had grown could not be shortened even though every
  extra model competes for every word. The field takes what `-l` takes, and the
  button now offers whichever move is missing: adding the source's model, or
  dropping the models that are only competing. A list typed with spaces is stored
  in the form tesseract needs — `kor eng` becomes `kor+eng`, where `-l "kor eng"`
  loads no language at all.

## [0.1.0] - 2026-09-24

First release. Everything below is new; the entries are grouped by what they mean
to someone using it.

**Translating**

- Screen-region capture through `org.freedesktop.portal.Screenshot`, polling at
  2 fps by default, with the region stored as either pixels or screen fractions.
- OCR via system tesseract, with 3x upscaling and autocontrast, pinned and
  checksum-verified `tessdata_fast` language data downloaded on first use.
- Translation backends: local NLLB-200-distilled-600M through CTranslate2 int8
  (the default) or transformers, DeepL, OpenRouter, OpenAI, and any
  OpenAI-compatible endpoint including a local server.
- Change detection, typewriter settle detection and an empty-read guard, so a
  static screen costs nothing and a half-revealed line is not translated twice.
- Glossary term overrides applied before and after translation, and an optional
  translation cache.
- Self-capture guards: reading stops while the picker or settings window is on
  screen, and the panel's own text is removed line by line rather than dropping
  the whole read.

**Using it**

- GTK4 region picker with a live preview, a settings dialog, and an
  always-on-top translation panel.
- Global Re-read hotkey through `org.freedesktop.portal.GlobalShortcuts`, with a
  documented fallback for compositors that do not implement it.
- `lintranslator reread` / `status` over a per-user control socket.
- `lintranslator check` for a plain-language report on what is missing.

**Packaging**

- Version 0.1.0, MIT, with third-party terms in `NOTICE`.
- `install-desktop` puts the menu entry and launcher into the XDG directories,
  which is what gives the app the application id the hotkey portal requires.

[Unreleased]: https://github.com/tanaka774/lintranslator/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tanaka774/lintranslator/releases/tag/v0.1.0
