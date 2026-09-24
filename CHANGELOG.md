# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

The version lives in `lintranslator/__init__.py`; the packaging metadata reads it
from there rather than keeping a second copy.

## [Unreleased]

- Settings has less text in it. The entry placeholders went first, because most
  of them named a control that already names itself: "pick a model below, or type
  any id" (the picker and Fetch list are the two buttons beside that field, and
  the list is not below it), "filter…" over the model list, and "eng" on the OCR
  languages field - where an empty box means "leave the list alone", not `eng`.
  "paste API key here" was the exception: it was the only thing labelling that
  row, so it is now a real **API key** label, since a placeholder is gone the
  moment a key is in the field.
- Then the lines that restated something already on screen. "Saved in config.json
  in plain text when you press Save." and "Using the key entered above
  (sk-or…a7d8)" both described the field they sat under - the tooltip on that
  field still says where the key is stored. 'The prompt says "English" →
  "Japanese".' said what the prompt box two rows down says itself, and it took
  the endpoint line under the pickers with it: the model row was already printing
  the same sentence, so the custom endpoint's warning appeared twice. "Local
  NLLB: the model field is the HF repo the tokenizer comes from" named what the
  row label already said (HF tokenizer / HF model) and was not even true for
  `local`. "Translation renders at 23 pt in a 867 px card" repeated the two
  sliders above it, "Use the picker for a big change" pointed at the window the
  user came from, and the prompt hint's "biggest quality lever" was selling a
  field that is only shown to the backends that read it.
- Two hints are gone outright rather than trimmed: the capture area's "Too tall
  and OCR picks up the nameplate or HUD; too short and it silently drops the
  second line of dialogue.", and the display area's "The translation scrolls
  after 4 lines…" - the second one taking its widget and its four slider
  callbacks with it, because a readout whose whole content is the two sliders
  above it has nothing left to say. What stays is what a user cannot see for
  themselves: the codes NLLB and DeepL decode with, the licence the local weights
  carry, what an empty field means, and every warning.
- The picker no longer opens the translation card. It was presented when the
  picker was built, so `lintranslator` put two windows on screen before a region
  had even been chosen - and, being mapped while the picker grabbed the screen
  for its canvas, the card was in the picker's own screenshot of the game it was
  supposed to be translating. The card is now opened by **Start**, which is also
  what the picker's primary button says: "Watch live" described the mode rather
  than the thing the button does, and the card's own button has read Start all
  along.
- The picker's sidebar says less. The caption under the translation - "This
  window is a still screenshot, so it will not follow the game. Press Watch live
  to translate continuously." - repeated the status line directly above it, and
  that status line itself only told the user to press the button they were
  looking at. The status line now stays empty until it has something to say that
  the window cannot show by itself (that the box is live, or why reading is
  paused).
- The picker's sidebar has no heading, because it was the third copy of the same
  sentence: "Drag over the dialogue text" was a title there, "cover the whole
  text block" was the line under it, and the toolbar's status line already says
  "captured 1500x980 — drag over the dialogue text" - with the capture size,
  which the title did not have. That title's tooltip also still pointed at a
  "Find box" button that no longer exists. What is left is the one line a user
  cannot arrive at by looking: a box that is slightly too short still reads
  plausibly while silently dropping a line, so the recognized text looks right
  and only the crop shows what was missed.
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
