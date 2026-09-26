# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

The version lives in `lintranslator/__init__.py`; the packaging metadata reads it
from there rather than keeping a second copy.

## [Unreleased]

- A local model that is slow is no longer reported as unreachable, and one that
  thinks is no longer reported as empty. Both were one line away from working:
  `qwen3.5:4b` through Ollama took 10.3 s for a single short line and answered
  with 3,544 characters of hidden reasoning in a field of its own and an empty
  `content` - so the card said "chat unreachable: timed out", and would have said
  "returned an empty translation" had it waited. The timeout is now a row in
  Settings (`translate.timeout`, where 0 means "pick one": 20 s hosted, 120 s for
  a server on this machine, which is the case that has no way to know it should
  wait), the timeout message names the limit and the setting rather than calling a
  slow answer unreachable, and `translate.reasoning_effort` sends the request
  field of that name - "none" is the difference between those 10.3 s of thinking
  and 0.4 s of translation, measured on the same line. A model that answers with
  reasoning and no translation now says so by name, with the two things that fix
  it, instead of reading as a bug in the app. Empty by default: not every provider
  accepts the field, and a hosted one has no use for it.
- A `google` backend: Cloud Translation - Basic (v2), one API key, no model id.
  v2 rather than v3 because v3 wants a project id and an OAuth token, and that is
  the tier the Translation LLM lives in - more setup than a dialogue box is worth.
  The key travels in `X-goog-api-key` instead of the `?key=` form the REST
  examples use, because a URL ends up in logs and error text and this app redacts
  response bodies, not URLs. The reply is unescaped: v2 answers `&#39;` for an
  apostrophe whether or not the input was HTML, and the card draws text.
  Its language codes are the ISO 639-1 column the table already carried for
  exactly this, with one exception - `zho_Hans` and `zho_Hant` both carry "zh" and
  Google tells the two scripts apart, so a Traditional target goes out as `zh-TW`
  rather than coming back Simplified with nothing on the card to say so. 153 of
  the 202 languages have an ISO code; the other 49 are refused by name, in the
  dialog and in `check`, instead of being sent as a guess and answered with an
  HTTP 400 that names the parameter rather than the setting.
- `lintranslator check` reports the API key for `deepl` and `google`. DeepL
  printed nothing there at all, so the one command whose job is to say what is
  missing was quiet about the one thing that stops that backend from working, and
  the Google row says what the key needs - a project with billing enabled - since
  the free allowance is the part people expect and the billing is the part they
  do not.
- Nothing is shown on the card about always-on-top on a native Wayland session. It
  used to say, on every launch and until the first translation, that the card was
  not always-on-top and how to fix it - three lines of instructions in the area
  the translation goes. Native Wayland cannot do it at all, the fix is a KWin
  window rule the card cannot detect, and so the notice was wrong for anyone who
  had already added one and unclearable by anyone who had not. The README's
  "Always on top" section covers both options. A failure the user *can* fix - the
  X11 path with `python-xlib` missing, or the window not found - still reports, in
  one line instead of three, with the steps in the README.
- The default backend is None, not OpenRouter and not the local int8 model. A
  fresh install can now avoid both of the things a first run should not do
  unasked: download 2.5 GB before it can say a word, and send the text it reads
  off the screen to a provider the user never chose. It reads the region and shows
  the OCR text, which is what `none` always did, and naming the backend on the
  card is what keeps that from reading as a translator that failed. The dropdown
  lists `none` first for the same reason: the first entry is the default, and it
  used to be `ct2` while the default was OpenRouter. OpenRouter, DeepL, OpenAI and
  any OpenAI-compatible endpoint are one Settings change away, and none of them
  downloads a model.
- `lintranslator convert` says what it will fetch - the fp32 checkpoint into the
  shared HuggingFace cache, and the converted weights beside it - and waits for a
  yes. Without a terminal there is nobody to ask, so it refuses unless `--yes` is
  passed.
- The `local` backend no longer downloads the checkpoint by itself. transformers
  would fetch whatever was missing while the panel said "warming up"; it now
  refuses unless `translate.allow_model_download` is set, and says so by name
  alongside the `lintranslator convert` alternative. A cache holding only the
  tokenizer - which is what the `ct2` backend leaves behind - deliberately does not
  count as the model being present, because that is exactly the case where the
  download would have happened unannounced.
- The picker's toolbar is three buttons plus the primary action: **Save** is gone,
  **Close** is **Quit** - the word the card already uses for the same act - and the
  vertical rules between the buttons went with them. Save wrote the region to
  `config.json` and stopped there, which **Start** already does on its way to
  opening the card, while `lintranslator region --x --y --w --h` stores one without
  reading anything: a button with nothing of its own to do. The rules were
  furniture at four buttons; the gap in front of Start stays, because Quit ends the
  session and must not sit flush against the button that begins one - which is what
  the second rule was really there for.
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
- One hint is gone outright rather than trimmed: the display area's "The
  translation scrolls after 4 lines…" - and it took its widget and its four
  slider callbacks with it, because a readout whose whole content is the two
  sliders above it has nothing left to say. What stays is what a user cannot see
  for themselves: the codes NLLB and DeepL decode with, the licence the local
  weights carry, what an empty field means, and every warning.
- Settings no longer edits the capture area - hint, readout and all. Its
  Smaller/Larger and 8 px move buttons were a correction made blind: this window
  is on neither the screen it reads nor the crop that comes back from it, so the
  one thing that says whether the box is right was never on screen. The picker
  shows the box, the crop and the OCR text side by side, and
  `lintranslator region --x --y --w --h` sets the region from a shell, so both
  ways of deciding it remain. The display area stays, because nothing else sets
  the card's font size, width or line budgets. The arithmetic that only those
  buttons called - `scaled_region`, `nudge_region`, `region_to_fraction` - went
  with them, along with their tests; `region_to_fraction` was already written out
  longhand in the picker. The dialog also sizes itself from the column it built
  instead of a fixed 940 px, because that number was tuned for a column that
  included this section: it left a band of dead space under the toolbar, and it
  would have gone stale again the next time a section moved.
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
- The OCR languages are chosen in Settings instead of only added to. The row
  appeared only when the list could not read the source, and its one button could
  only append, so a list that had grown could not be shortened even though every
  extra model competes for every word. It is now a searchable list of the 124
  models `tessdata_fast` ships - `languages.py` names only the 100 reachable from
  a translation language, so the list is generated by
  `probe/gen_ocr_languages.py` - and each row says what ticking it would cost:
  `ready`, `will download` (one of the 16 the app fetches against a pinned
  checksum), or `not installed`, which was otherwise discovered by saving and
  watching OCR fail. The model the source needs is marked, and a line under the
  list names what is only competing. The one-click fix button that used to sit
  there - "Use eng+jpn" to add, "Use eng+kor" to trim - is gone with it: both
  moves are a tick in the list now, which is where the choice is made. A
  hand-edited name is normalised before it reaches `-l` - tesseract splits that
  argument on `+` alone, and `-l "kor eng"` loads no language at all.

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
