# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

The version lives in `lintranslator/__init__.py`; the packaging metadata reads it
from there rather than keeping a second copy.

## [Unreleased]

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
