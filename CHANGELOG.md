# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

The version lives in `lintranslator/__init__.py`; the packaging metadata reads it
from there rather than keeping a second copy.

## [Unreleased]

**A paused read says what is holding it, and the picker stops pausing once it is gone**

- **A queued re-read names the window that is in the way.** Pressing Re-read
  while reading was paused said only "re-read queued — reading is paused", and
  that line replaced the "paused — the region picker is on screen" the panel had
  just written, so the card named neither the cause nor the way out. The note
  carries the reason now - "re-read queued — the region picker is on screen —
  press Apply box to keep translating". The button still cannot override the
  pause, which is the point of the pause, but it no longer hides why it is there.
- **The picker's guard follows the window, not the signal that happened to
  arrive.** The pause was set on `map` and cleared on `unmap`, so a guard entry
  left behind by a missed event paused reading with nothing on screen to explain
  it. It is re-derived from the window's own state - visible and mapped - on
  `map`, `unmap` and `notify::visible`, and the fallback hide re-derives it too,
  so hiding the picker lifts the pause whether or not GTK delivers the event.
- **A compositor-side minimise is not detectable on Wayland, and the reason no
  longer pretends it is.** Measured on KDE Wayland with GTK 4.22: after
  `Gtk.Window.minimize()` the window still reports `mapped=True` and
  `visible=True`, its geometry is unchanged, no `unmap` is emitted, and
  `Gdk.ToplevelState` reports no `MINIMIZED` flag. "Minimise it to keep
  translating" was therefore a dead end there; the panel's reason and the
  picker's own status line now name **Apply box**, which hides the window on
  every backend.
- **The panel's Region button is "Select region"**, which says what pressing it
  does rather than naming the thing it selects. On a narrow card the longer label
  pushes one more action into the ⋮ menu.

**Reading the screen no longer writes it to disk**

- **The polling loop reads a live stream instead of asking for a screenshot per
  poll.** `org.freedesktop.portal.Screenshot` is a one-shot, user-initiated API,
  and using it as a 2 fps source meant the portal wrote a full-screen PNG on every
  grab for the app to find and delete: `~/Pictures/Screenshot_<stamp>.png` on KDE
  and GNOME, a hardcoded `/tmp/out.png` on wlroots, `$XDG_RUNTIME_DIR` on
  Hyprland. On KDE that is one 1.5–2.5 MB file per poll, and a crash, a read-only
  home, or a refused delete leaves it there. `capture.backend` now defaults to
  `portal-screencast`, which reads frames over PipeWire and writes nothing at all
  (2–3 ms a frame against ~220 ms, because there is no full-screen encode, write,
  read and decode in the loop). It costs one consent dialog per session; the
  restore token is kept in the config so later runs skip it. That needed
  `persist_mode` to be sent at all: it defaults to 0, meaning "do not persist",
  and a portal only returns a restore token when persistence was asked for - so
  without it the consent dialog reappears on every launch. This is the first
  release in which the ScreenCast path works at all - `create_screencast` sent
  `CreateSession` a body D-Bus rejects, so it had never once run.
- **The choice has a row in Settings and a command, because it had neither.**
  Reading the screen was the one setting with no UI at all, which left
  `config.json` as the only switch for the thing that decides whether a file is
  written per poll. Settings gains a "Reading the screen" section that labels
  each backend by what it does to the disk and says so when the stream cannot run
  here, and `lintranslator capture` reports or sets it
  (`lintranslator capture --backend portal-screenshot`).
- **The picker still uses the screenshot portal**, deliberately: one grab, the
  user asked for it and is watching, so a temporary file is the right trade. It
  is also why picking a region works before any screen-sharing consent is given.
- **A capture backend that cannot run says so, and falls back once.** If the
  stream fails - no consent, a revoked session, no GStreamer - the portal takes
  over for the rest of the run and the user is told once, with the reason. A
  failure per poll would be its own kind of unusable. `lintranslator check` now
  reports the session, the configured backend and whether it can run there, and
  an unknown `capture.backend` is refused instead of silently meaning the portal.
- **An environment that cannot stream is never asked to.** The stream is skipped
  up front when the Python has no `pipewiresrc`, and on an X11 session under a
  desktop whose portal refuses X11 outright - KDE's returns `OtherError` from
  `CreateSession` there, and shows its own "Screen Sharing Not Available" dialog
  before it does. Asking anyway bought the user that dialog on every launch and
  an error code that says nothing. GNOME is still asked on X11, because it
  screencasts through mutter and is not Wayland-only. Reading then happens the
  older way, and says so once rather than looking like a failure.
- **A screenshot the app could not delete is counted and reported**, instead of
  being swallowed. `remove_screenshot`'s answer was discarded, so a refused or
  failed delete left a picture of the user's screen on disk in silence - which is
  how `~/Pictures` reached 1060 files and 2.2 GB. The delete also moved into a
  `finally`, so a grab that fails *after* the portal wrote the file (an oversize
  read, an undecodable image) still cleans up; previously those paths returned
  before the delete ran. `ScreenGrabber.stats["leaks"]` counts them.
- **The Pictures directory is read from `user-dirs.dirs`.** `$XDG_PICTURES_DIR`
  is normally unset, and on a localized system the folder is not called
  "Pictures" at all - so the portal's path fell outside the delete allow-list and
  *every* grab was refused and left behind. `lintranslator check`'s backend line
  and the leak note are what make that visible now.
- **A portal timeout explains itself.** jeepney raises a bare `TimeoutError` with
  no message, which bypassed `portal_request`'s own timeout handling: an
  unanswered consent dialog surfaced as a silent, empty failure with no hint that
  anything had been asked.

**A read that finds nothing says which kind of nothing it was**

- **"(no text found in this region)" was two different answers.** Tesseract
  reading nothing, and tesseract reading a line that `ocr.min_confidence` then
  rejected, looked identical in the picker - and only the second is a setting the
  user can move. So a box that reads fine at 41% under a gate of 55% read as "this
  OCR cannot read the language". The readout now shows the rejected read and names
  the gate underneath it ("read 1, best 41% — under the 55% gate"), and when
  nothing was read at all it names the recipe in force ("input: grey, inverted,
  contrast untouched"). `OcrResult` keeps the lines the gate threw away whole
  rather than counting them, and the panel's forced re-read note makes the same
  three-way distinction instead of always saying "check the region".
- **`ocr.min_confidence` has a row now**, because it decides whether a good read
  survives and had none. A read that misses the gate by a point is dropped, and
  the window reported that as "no text found in this region" - indistinguishable
  from an OCR that cannot see the box at all.
- **`ocr.psm` stays a config key, deliberately.** It changes a read as much as
  anything else does: on a one-line strip over bright artwork the default mode
  read garbage at 38.1% where "a single line" read the text at 70.8%. But no
  single value is safe for every box shape - that same mode reads a two-line box
  as *nothing at all* (0.0% against 74.1% for the default) - and the box's shape
  is not a question the dialog should ask. So the picker names the layout in force
  when a read comes back empty, `lintranslator check` prints it beside the recipe,
  and the names with the measurements behind them live in `ocr.PSM_NAMES` so the
  readout, the check and the numbers cannot drift apart.
- **The hint under the OCR-input row is gone.** The row explains itself through
  its tooltips, and the picker's readout is where the recipe's effect is actually
  visible.

**OCR input is a setting, and the preview shows it**

- **`ocr.invert` and `ocr.threshold` are new, and both are in Settings.** The
  engine already had an `invert` argument that nothing could reach, and the recipe
  was grey, 3x upscale and autocontrast with no way to read white dialogue on a
  black panel, or to separate grey glyphs from a background the stretch cannot.
  The new **OCR input** row sets both, beside the contrast stretch that was
  config-only until now. The cut is applied *after* the stretch, so the number
  means the same thing whatever the contrast setting is, and 0 shows as "off"
  rather than as a level - it is not one.
- **The picker's preview is the recipe now, not a drawing of it.** Both of its
  non-raw modes drew their own grey autocontrast, so `invert` and a fixed cut were
  invisible in the one view that exists to judge them, and its "threshold" mode was
  a fixed 150 that no setting could move. "Preview: OCR input" is
  `TesseractOcr.prepared(crop)` itself, and "Preview: threshold" is that image cut
  at the configured level - or, with no cut set, at the one tesseract would pick
  for itself (Otsu), which is the comparison that answers "would a fixed cut help
  here?". The tooltip names the recipe in force.
- **`lintranslator read|run --invert --threshold N`** set the same two for one run,
  and **`lintranslator check`** prints the recipe it would use, which is the usual
  answer to "why is this box read as nothing?".

**Popovers stop borrowing the desktop theme's colours**

- **The model, language and OCR-language lists could not be read in a light
  desktop theme.** The app paints its own windows dark and its labels light, but
  the lists inside its popovers were left to the desktop theme, and Breeze light
  paints `list` and `list row` with the theme's base colour - white - *over* the
  popover surface. The result was white rows under `#f2f3f5` labels, and it
  happened only "in a certain system colour theme" because the app's request for
  the dark variant is only a hint: GTK deprecated
  `gtk-application-prefer-dark-theme` in 4.20, and it never applied to a theme
  that ships no dark variant. All three pickers (`ModelPicker`,
  `LanguagePicker`, `OcrLanguagePicker`) are fixed by painting the list nodes
  themselves, with hover and selection in this app's own colours rather than the
  theme's blue.
- **Every `Gtk.DropDown` popup had the same defect**, because the `Gtk.ListView`
  GTK puts in one carries `.view` - the Backend, Thinking and Prompt-preset rows
  in Settings, and the picker's "Preview: raw / OCR input / threshold" chooser.
- **The prompt editor is no longer a white slab in a dark dialog.** A
  `Gtk.TextView` is an entry-like surface, so it now gets the entry's colours.
- `probe/theme_check.py` renders all four surfaces under a forced light theme
  and fails if any of them comes out in the desktop theme's colours, which is the
  check that was missing when this shipped.

## [0.2.0] - 2026-09-27

The release where the app stops shipping a translation model, and the one where
the Settings dialog stops offering settings nothing reads.

**Removing the local model**

- **The `ct2` and `local` backends, and `lintranslator convert` are gone.** They
  existed to run `facebook/nllb-200-distilled-600M`, first through transformers
  and then as int8 weights through CTranslate2, and the app now has no model of
  its own: local translation means a server you run (Ollama, llama.cpp, vLLM)
  reached through the `chat` backend, and the README says how.
- **The model licence warning went with them.** NLLB is CC-BY-NC-4.0 and the
  dialog said so next to the two local backends. Nothing the app downloads is
  non-commercial any more, so there is nothing to warn about - and the weights-dir
  row, `translate.ct2_model_dir`, `translate.device`, `translate.threads`,
  `translate.max_new_tokens` and `translate.allow_model_download` went too. An old
  config that still carries them is told so, by name, on the next `check`.
- **Five dependencies went with the classes that imported them**: `ctranslate2`,
  `transformers`, `torch`, `sentencepiece` and `sacremoses`. The `.[ct2]` and
  `.[local]` extras are gone; the base install is Pillow, jeepney and pytesseract,
  and no path through this app downloads gigabytes.
- **A config left on `ct2` or `local` is migrated to `none` on load**, with a
  warning naming the replacement. Without it the value loaded fine and then raised
  "unknown translation backend" on every line, because `translate.backend` was
  never validated against a list.
- **The CJK space normaliser** (`translate.normalize_cjk`, `languages.is_cjk`).
  It removed the token-level spaces NLLB inserted between CJK tokens; no backend
  this app talks to produces them.
- **`lintranslator remove checkpoint` / `remove model`**, and the "not ours, not
  touched" HuggingFace-cache report. `remove` now reports the OCR language data
  and the optional cache and nothing else - a model you serve is yours, and not
  this command's to delete. A running GUI is no longer warned about losing weights
  under it, because it cannot lose them.
- `probe/gen_languages.py` no longer checks the table against NLLB's tokenizer
  vocabulary: membership is frozen where it stands and the script warns when a
  source disagrees, because no model the app can reach is the authority on which
  codes exist. The generator and `languages.py` had also drifted - the generator's
  copy of `google_code` predated the Google backend's `zh-TW` fix, so regenerating
  the table would have reverted it. They are byte-identical again.
- The README's local-model section is now the Hy-MT2-1.8B recipe: Q4_K_M GGUF
  (1.13 GB), a Modelfile with the model card's sampling values, `ollama create`,
  and the four Settings fields. It is Apache-2.0, and it is not in Ollama's
  library, so it is a `create` rather than a `pull`.

**Everything below was unreleased until now, and is part of 0.2.0**

- A legacy `api_key` is filed under the backend that issued it when the key itself
  says so. The migration moved it to whatever backend was configured, which is the
  only backend the old field records - and not always the right one: a key left in
  the shared field while the backend was on `chat` was attached to `chat`, and the
  next translation sent an OpenRouter key to a local Ollama server as a bearer
  token. `sk-or-` and `AIza` are the prefixes that name a provider; anything else
  keeps the configured backend. A key that was moved says so in the config
  warnings, because a wrong guess here is invisible until a request fails.
- A server on this machine is asked not to think, unless the Thinking row says
  otherwise. Hidden reasoning is pure latency for one line of dialogue and can
  consume the whole answer budget - measured on qwen3.5:4b through Ollama: 10.3 s
  and an empty answer with it, 0.4 s and a translation without - so on loopback
  the app sends `reasoning_effort: "none"` by default. Hosted endpoints are sent
  nothing, as before: the field is not universally accepted and their thinking is
  the user's money. A model that answers with reasoning and no translation anyway
  is asked once more with the thinking off, on this machine only, instead of
  failing with a sentence about hidden reasoning.
- `lintranslator check` notes when a stored key looks like another backend's.
  Prefixes are a guess, so it is a note rather than a failure: a gateway that
  fronts OpenRouter is a real setup and its key starts `sk-or-` too.
- API keys are stored per backend: `translate.api_keys: {"deepl": "...",
  "openrouter": "..."}` instead of one `translate.api_key` for all of them. The
  old field is migrated on load - under the backend that was configured when it
  was written, which is the only backend the file records and the right answer for
  every config Settings ever wrote - and dropped on the next save. It was not a
  cosmetic problem: the key was refilled into the same masked field whichever
  backend was selected, so a DeepL key sat behind OpenRouter's box looking as if
  it belonged there, and Save wrote it as OpenRouter's. The next translation sent
  it to `openrouter.ai` as a bearer token. The request failed, so nothing was
  silently mis-translated, but the key had left the machine by then - and pasting
  the right key over it destroyed the DeepL one. The field now swaps with the
  backend, exactly like the model field, keeps a key typed but not yet saved when
  the backend changes, clears only its own backend, and is never written from a
  backend that has no key field at all.
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
  for themselves: the code each backend decodes with, what an empty field means,
  and every warning.
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
