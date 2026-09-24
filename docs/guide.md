# Using LinTranslator

The picker and the panel, the hotkeys, the command line, and how to choose a
backend, a language pair, a glossary and a custom endpoint.

[Installation](install.md) is a separate document; so is the
[reasoning](design.md) behind the behaviour described here.

## The command line

```bash
# --- GUI ---
.venv/bin/python -m lintranslator gui                 # pick a region, then watch (default)
.venv/bin/python -m lintranslator gui --panel         # skip the picker, straight to the panel
.venv/bin/python -m lintranslator gui --pick          # region picker only, then exit
.venv/bin/python -m lintranslator gui --demo          # panel with a sample line (no model)

# --- while it is running ---
.venv/bin/python -m lintranslator reread              # read the box again, now (bind a hotkey to this)
.venv/bin/python -m lintranslator status              # what the running GUI is doing
.venv/bin/python -m lintranslator shortcut            # how to set up a global hotkey

# --- housekeeping ---
.venv/bin/python -m lintranslator remove             # what has been downloaded, and its size
.venv/bin/python -m lintranslator remove checkpoint  # free the 2.5 GB the conversion cached

# --- headless ---
# capture the region once, to check you framed the dialogue box
.venv/bin/python -m lintranslator grab -o /tmp/frame.png

# OCR only - no translation, no waiting. Use this to tune the region.
.venv/bin/python -m lintranslator read --repeat 5

# the real thing
.venv/bin/python -m lintranslator run
```

## The GUI

**Region picker** (the first window of `lintranslator gui`) shows a frozen screenshot and
lets you drag a rectangle over the dialogue text. The box can be resized from any
edge or corner: grips sit at every corner and at the middle of every edge, the
pointer changes shape over them, and the edge you grabbed travels with the pointer
instead of snapping under it. The controls are one toolbar row: **Capture**,
**Save**, **Settings** and **Close** grouped on the right, with **Watch live** —
the action the window exists for — carrying the accent. It used to be two rows of
equal-width buttons with no primary action, which made the one button that matters
look like its four neighbours.

The sidebar is a 340 px control column and the canvas takes everything else —
1160 px of a 1500 px window, against 840 px before. The culprit was one
`set_hexpand(True)` on the crop thumbnail: `GtkBox` decides which children share
the spare width with `gtk_widget_compute_expand`, which is true for a widget any of
whose *descendants* expands, so that single flag propagated up through the sidebar
and its scroller and split the surplus evenly with the canvas — the sidebar's own
340 px request was ignored. `probe/picker_sidebar_check.py` measures both columns
and prints them.

**Watch live** saves the region, starts the panel, and then **takes itself off the
screen** — minimise where the compositor honours it, hide where it does not
(measured: kwin_wayland 6.7.4 + GTK4 ignores `minimize()` outright, so the hide is
what usually runs; see `probe/minimise_check.py`). That is not politeness: the
pipeline reads pixels off the screen, so a picker sitting over the dialogue box is
captured along with it. Measured before this was fixed, the first frame after the
click contained the picker's own status line ("captured 2560x1440 — drag over the
dialogue text") at 93 % confidence, while the dialogue line underneath it fell to
54.7 % and was dropped by the confidence gate — a real line, silently lost.

Reading starts about half a second later, when the window is actually gone — not
after a guessed delay. The card itself honours `display.width` (its long labels
are ellipsised or scroll, never stretching it), and pauses are shown in amber: an
unexplained pause is indistinguishable from a broken translator. The window comes
back through the panel's **Region…** entry, which **captures the screen again on
the way back**: the shot the picker
holds was taken when watching started, and re-framing the box is the only reason to
press it, so reopening on a screen the game has moved on from defeats the point.
The box you had is kept — what is replaced is the screen underneath it. While
watching the picker is already off screen, so the grab starts at once instead of
paying the Capture button's 1 s countdown (measured: click → fresh shot in ~0.7 s,
~0.5 s of it the portal round trip; `probe/region_recapture_check.py` also checks
that the pipeline polling the same portal on another thread does not make either
grab fail). While it is on screen reading pauses (the panel says why), and **Apply
box** takes it off screen again and resumes on the new area — without rebuilding
the pipeline or moving the card. Dragging a new box while watching re-points the
running pipeline immediately, so the rectangle on screen is never a lie about what
is being read.

### The card: one control row, and a size that does not move

The card is the output, so it is the one window that stays on screen while
watching. Two properties matter more than how it looks.

**It never changes size.** `display.target_lines` and `display.source_lines`
reserve space for each text area, and anything longer scrolls inside its own area
instead of growing the card. Before this the card was content-sized, and GTK never
shrinks a resizable window back, so it ratcheted: measured, a four-line reply took
it from 172 px to 312 px and it *stayed* 312 px when the next line was two
characters long. One long line permanently covered more of the game. Measured
after, the card is 207 px at the default budget — 3 translation lines and 2
original lines with `font_scale` raised to 1.2 — and 207 px for every input tried: a
two-character line, a 900-character one, a settled translation, a long error.
`probe/panel_layout_check.py` prints the breakdown and fails if the height moves;
`probe/panel_contact_sheet.py` renders every state to `data/panel_*.png`.

**You can still drag it to the size you want.** The window is frameless, so it has
no window-manager resize handles; the grips along the edge are widgets this app
draws, and a drag on one applies the new size with `set_default_size`, which does
resize an already-mapped window (measured: 555×207 → 700×340 on a mapped panel).
`gtk_window_begin_resize_drag` is no help here — GTK4 removed it, and its
replacement `Gdk.Toplevel.begin_resize()` needs the Wayland input *serial*, which
GTK4 exposes nowhere.

Only the **east, south and south-east** edges resize, and that is deliberate. On
Wayland a client cannot move its own window, so a drag on the north or west edge
could not hold the opposite edge still: the card would grow rightward while the
pointer moved left. Offering those edges would be worse than not offering them.
A drag is floored at the card's own contents, so it cannot be shrunk into
uselessness, and the result is written to `display.width` / `display.height` and
restored next launch — a size that silently reverted would be worse than no
resizing. `display.height: 0` means "size it from the line budgets"; dragging
pins it, and changing either budget in Settings releases it again, because
otherwise those two sliders would appear to do nothing after the first drag. The
grips paint nothing — the cursor is the affordance — and they do not have to:
GTK4 still hit-tests a widget that has a size request but no background, which
`probe/panel_resize_check.py` checks with `Gtk.Widget.pick()`.

**The wheel does not change the display settings.** Those four sliders are the
card's style values, and `Gtk.Scale` changes value on every scroll it is given —
in a tall scrolling dialog that means reaching the buttons at the bottom drags the
style along with it. Measured, one wheel notch over the font-size slider took it
from 1.2 to 0.7. Each slider now carries a **capture-phase** scroll controller,
which runs on the way *down* the widget tree, ahead of the scale's own
bubble-phase handling. It does not simply swallow the event: that would stop the
dialog scrolling wherever the pointer happened to rest on a slider, so the wheel
is handed to the dialog's scroller and the gesture does what it looked like it
would. Dragging, clicking and the arrow keys are untouched. `probe/settings_wheel_check.py`
prints the controller phases, shows `Gtk.Widget.pick()` landing on each slider, and
demonstrates that the built-in handler really does move the value when it is
reached.

**The control row re-flows as the card is resized.** Every action lives on the
card while it fits, and the ones that stop fitting move into the **⋮** menu — the
same widget either way, so an action is never in two places and never missing from
both. It is priority order, left to right, so the row always holds a *prefix* of it
and **Region…** — the only way back to the picker once the panel owns the box — is
leftmost and last to leave. Measured as the card narrows:

| card width | on the card | in ⋮ |
|---|---|---|
| 560 px | Region, Re-read, Start, Copy, Settings, Quit | — |
| 460 px | Region, Re-read, Start, Copy | Settings, Quit |
| 380 px | Region, Re-read | Start, Copy, Settings, Quit |
| 300 px | Region | Re-read, Start, Copy, Settings, Quit |

`⋮` hides itself when nothing overflowed, because an empty menu promises actions
that are already on the card. `probe/panel_overflow_check.py` walks those widths and
renders each to `data/overflow_<width>.png`.

Two details that are easy to get wrong, both commented where they live. The split is
decided from the width the card is *meant* to be, not the width it is currently
allocated: a button can never be laid out narrower than its own text, so until
enough of them have left, the window's minimum keeps the row wide — deciding from
that allocation would hold the extra buttons on the card, clamped, for a frame. And
`do_size_allocate` is the only hook GTK4 offers for "the width changed": the
`size-allocate` signal was removed and `Gtk.Widget` has no width property.

The layout before this put all six buttons in two right-aligned rows, which cost
62 px of a 172 px card *and did not line up* — the rows measured 133 px and 152 px
wide, so the left edge stepped in by 19 px.

The translation is the headline and the original text sits below it, because a
translation panel that covers the text you are trying to read is worse than no
panel. Notes that run to several lines — "not always-on-top", "no global hotkey" —
take over the translation area while it is still empty, where they are readable,
rather than being ellipsised into the one-line status row; the first translation
displaces them and their text stays in the status tooltip. Errors share the status
row instead of adding a line of their own, so the card cannot grow taller exactly
when something has gone wrong.

The header strip is the **drag handle**, and it names the backend, the model and
the box being read. It is the only draggable part: when the whole card was the
handle, a drag that started on the translation moved the window instead of
selecting the text.

### One stylesheet for all three windows

`lintranslator/theme.py` is the single source of visual truth, and `lintranslator gui` installs it
before building any window. The panel used to be the only window with any CSS at
all, which left the picker and the settings dialog as stock widgets beside a custom
dark card.

Nothing is styled by bare element name — every rule is scoped to a `.lintranslator-*` class
or to a window class — so the picker's hand-drawn cairo area, and any widget nobody
has classified, keep the toolkit's own look. Where the app does paint a control it
paints it explicitly rather than inheriting: this machine runs **Breeze light**, so
an unstyled button on the dark card was a white block, the overflow menu's labels
were dark text on a dark popover and could not be read at all, and the picker's
checkboxes were filled white squares that read as "already ticked" whether or not
they were. The app also asks for the dark variant of the desktop theme, but does
not rely on getting it — Breeze loads no dark variant here, so the window
background went dark while its labels, scales and buttons stayed light.

Widgets inside `window.lintranslator-app` (the picker and the settings dialog) are
therefore painted explicitly too. That needs two pieces of care, both of which are
commented where they live: a `window.lintranslator-app label` rule outranks a bare
`.lintranslator-hint`, and `window.lintranslator-app button` outranks `button.lintranslator-primary`, so
the semantic classes are re-stated scoped rather than left to source order.


It previews what the pipeline will see:

* the **Preview** dropdown — **raw** (nothing added), **OCR input**
  (autocontrasted, the closest view to what tesseract reads) or **threshold**
  (the ink mask, which makes it obvious when a region is mostly background art).
  It is one three-way choice, not two switches: the crop is drawn one way at a
  time. All three are display only — OCR reads the crop itself, so changing this
  cannot change the text below.
* live **OCR text and confidence**, so framing is judged by the actual result
  rather than by eye
* the **translation**, updated as you adjust the region. It is not optional: the
  debounce coalesces a drag into one request and the cache makes unchanged text
  free, so a switch to turn it off only bought a stale translation — it also
  gated the re-translate after **Apply** in Settings, which left the old model's
  answer on screen under the new settings. This is the fastest way to judge a
  model or prompt.

There is no automatic region finder. Frame the box by hand — it takes a few
seconds and avoids a whole class of surprises from guessing at screen content.

**Settings** is available here too, so backend, model, API key and prompt can all
be changed without leaving the picker. Saving re-translates the current selection
immediately. A no-op would look like the setting had been ignored. The OCR engine
is rebuilt on that same signal, because `ocr.langs` is a setting like any other: a
window that kept the engine it was opened with went on reading a Korean box with
`eng` after the dialog said `eng+kor`, and all that produces is "(no text found in
this region)", with nothing on screen saying why.

### Re-read: when a line comes out wrong

**Re-read** reads the box again *now* and translates it from scratch. It skips
everything that normally suppresses a repeat — the change detector, the OCR
throttle, the settle window, the "already translated" check and the translation
cache — because pressing it means "do that again", and an answer served from the
cache would look like the button did nothing.

Three ways to trigger it, in order of how well they survive the game having focus:

| how | where it works | setup |
|---|---|---|
| a **global hotkey** | any window, game included | none if LinTranslator was started from the application menu (it asks the compositor itself); otherwise bind `lintranslator reread` as a desktop shortcut — `lintranslator shortcut` prints the exact command |
| `lintranslator reread` | any window, game included | run it from a terminal or a script; it talks to the running window over its control socket |
| **Ctrl+R** / **F5** | while the translation panel has focus | none |

Wayland gives an application no way to read global keys, so a shortcut that works
while the game is focused has to come from the desktop. LinTranslator asks for one through
`org.freedesktop.portal.GlobalShortcuts`, which KDE implements: the compositor
shows its own binding dialog once and then sends the keypress. That portal refuses
callers without an *application id*, which a plain terminal launch does not have
(KDE answers `An app id is required`) — hence the two fallbacks, and hence the
menu entry:

```bash
lintranslator install-desktop     # ~/.local/share/applications + ~/.local/bin
```

The `.desktop` file and its launcher ship inside the package
(`lintranslator/data/`), so this works the same from a checkout, a wheel or a
distro package. Then launch LinTranslator from the application menu rather than a
terminal — that launch is what carries the application id.

The panel always says which of these is in force: `global hotkey for Re-read:
Ctrl+Alt+R`, or `no global hotkey — … Run 'lintranslator shortcut' for the setup`.

### Live translation

**Live translation** (on by default) is the continuous mode: it watches the
region and translates whenever the text changes. This is what makes translation
follow the game, and it is what the panel uses.

How it decides a line is ready:

0. **Never while LinTranslator's own window is on screen.** The picker and the settings
   window report when they are mapped (`lintranslator/occlusion.py`), and the loop does not
   capture at all until they are gone — a paused poll, not a fixed delay, so
   reading starts the moment the window actually unmaps. The panel is the one
   window that cannot be gated (it is the output), so its text is caught instead by
   `lintranslator/selftext.py`, **line by line**: the lines that are ours are dropped and
   the game's are kept, because dropping the whole read loses the dialogue under a
   panel that clips the edge of the box. A read that echoes the translation we just
   produced is dropped too. Both are *said* to be dropped in the status line rather
   than silently translated.
0b. **Not dialogue at all.** A read with no real words ("e¢", "12", "| / \") is
   dropped, and a read shorter than a dozen characters has to be *more* confident
   than `ocr.min_confidence` before a model is asked about it
   (`detect.short_text_confidence`). Measured: a patterned poster inside the box
   read as `e¢` at 62.5 %, above the ordinary gate, and was translated until this
   existed. Long confident nonsense still gets through — there is no cheap way to
   tell it from dialogue — so a wrong translation is one **Re-read** (or a better
   box) away.
1. **Capture** polls the region (`capture.fps`, default 2/s), and reports the whole
   grab cost — portal round trip plus PNG decode — because that is what the poll
   budget has to cover.
2. **Change detection** compares a downscaled signature. Unchanged frames skip OCR
   entirely — that is what makes an always-on loop cheap.
3. **OCR** runs on a change, throttled to at most once per `detect.ocr_min_interval`
   so a blinking advance cursor cannot make it run flat out.
4. **Settling** waits until the text stops changing, comparing reads by edit
   distance so OCR jitter ("line" vs "line.") is not mistaken for new text. A
   timed re-read (`detect.refresh_interval`) confirms the line even when the
   screen never goes pixel-stable. Text that does not look like a finished
   sentence waits `detect.incomplete_grace` longer, because a game pauses
   mid-reveal and a pause is not the end of a line.
5. **Translation** runs once per distinct line; repeats are served from cache. Each
   event carries `total_ms`: the time from the capture to the finished translation,
   so the panel can show how stale a line is instead of implying it is instant.

### Always on top: read this first

**On native Wayland no application can raise itself above other windows.**
Stacking belongs to the compositor, and GTK4 removed the `keep_above` API that
GTK3 had. LinTranslator does what it can, reports when it cannot, and does not pretend
otherwise.

Two options that work:

**A — launch under XWayland** (no setup, verified working). The standard
`_NET_WM_STATE_ABOVE` hint still functions there and LinTranslator applies it itself:

```bash
GDK_BACKEND=x11 .venv/bin/python -m lintranslator gui
```

Confirmed on this machine by reading the property back off the window:
`_NET_WM_STATE contains ABOVE: True`. The status line stops showing the warning
once it takes effect.

**B — a KWin window rule via System Settings** (native Wayland, permanent):

*System Settings → Window Management → Window Rules → New*

| field | value |
|---|---|
| Window title | `Substring match` → `LinTranslator` |
| Keep above other windows | **Force** → **Yes** |

> **Add rules through System Settings, not by editing `kwinrulesrc` directly.**
> KWin rewrites that file from its own configuration state, so a rule written
> behind its back is discarded on the next reload. This was tried and does not
> stick - the file reliably reverted to `rules=`.

> **Worth knowing:** an always-on-top window also takes clicks over its area, so a
> card lying over the game blocks input there. Keep it clear of anything you need
> to click.

## Setting the region without the GUI

Regions are stored as **fractions** of the screen, so they survive a resolution
change.

```bash
# x,y,w,h as fractions of the screen
.venv/bin/python -m lintranslator region --x 0.175 --y 0.838 --w 0.790 --h 0.082 --fraction
```

Or open the picker on an existing screenshot, which also works without grabbing
the live screen:

```bash
.venv/bin/python -m lintranslator gui --pick --from-file shot.png
```

To let the code find the box itself, call the calibrator on a full screenshot
(this is a helper for scripting; the picker has no auto-detect button):

```python
from PIL import Image
from lintranslator.calibrate import calibrate
print(calibrate(Image.open("shot.png")).describe())
```

It scores every candidate text block on ink density, edge density, width and
position; it returns `None` when nothing looks like dialogue. Treat it as a
suggestion and confirm by looking at the crop.

> **The failure mode to avoid:** a region that crops text too tightly still
> produces plausible-looking OCR while silently dropping a line. During
> development a box 20px too short read line 1 correctly and cut line 2 in half.
> Always eyeball the crop (or the picker's preview) before trusting `run`.

## Switching translation backend

```bash
# int8 CTranslate2 (default)
.venv/bin/python -m lintranslator run --backend ct2

# transformers fallback
.venv/bin/python -m lintranslator run --backend local

# DeepL / OpenAI need a key in config.json ("translate": {"api_key": "..."})
.venv/bin/python -m lintranslator run --backend deepl
```

| backend | latency | notes |
|---|---|---|
| `ct2` | **~0.2-0.35 s/line** | int8 NLLB via CTranslate2, ~630 MB, offline. **Default.** |
| `local` | ~0.8-1.7 s/line | the same 2.5 GB checkpoint through transformers; needs torch |
| `openrouter` | network-bound | **many models, one key**; needs a key + model id; prompt-tunable |
| `deepl` | network-bound | best fluency for JA; needs a key |
| `openai` | network-bound | prompt-tunable, needs a key |
| `chat` | network-bound | any OpenAI-compatible server (llama.cpp, Ollama, vLLM, Groq, Together); needs `api_base`; key optional; prompt-tunable |
| `none` | 0 | pass-through, for testing the pipeline |

The unofficial Google endpoint was removed, so every remote backend is now an
endpoint with a documented API and terms.

Translated lines are cached **in memory for the run**, keyed by source text, so
repeated dialogue (battle callouts, menus, the picker's drag preview) is instant
and free within a session. Nothing is written to disk by default: the on-disk
cache used to accumulate a plaintext transcript of everything that had ever
passed through the capture box — which is whatever was on screen, not only the
game. Persisting it is opt-in: set the top-level
`"cache_path": "/some/path/cache.json"` in `config.json`, and a config that
already names a path keeps working. The cache is scoped per backend, model **and
language pair**, so changing any of the three re-translates rather than serving
the previous setting's answer.

## Source and target language

Set both in the GUI: **Settings → From / To**. Each is a searchable list of the
202 languages NLLB was trained on, and the pair applies to every backend — it is
one setting, not one per backend.

The list is deliberate. The languages are **FLORES-200** codes (`eng_Latn`,
`jpn_Jpan`), because that is what the local model needs verbatim: the source code
is prepended to the tokens and the target is the decoder prefix. Anything else is
scored as `<unk>` — NLLB answers with fluent text in the wrong language and
nothing raises. A picker is the only shape that cannot produce that.

Every backend is derived from the same pair, and the hint under the pickers says
what the selected backend is actually sent:

| backend | source/target become | example |
|---|---|---|
| `ct2`, `local` | the FLORES code, unchanged | `eng_Latn` → `jpn_Jpan` |
| `openrouter`, `openai`, `chat` | the name, in the prompt | "English" → "Japanese" |
| `deepl` | DeepL's code (33 languages) | `EN` → `JA` |
| `none` | unused | — |

Two consequences are shown rather than implied:

* A target DeepL cannot translate (Cebuano, Serbian, …) is flagged in the dialog
  and refused at startup with a message naming the setting, instead of an HTTP
  400 that names only the parameter.
* The source language needs a matching **OCR** language, which is a separate
  setting. Reading Japanese with `eng.traineddata` gives confident nonsense, so
  when `ocr.langs` cannot read the source the dialog offers a one-click
  `Use eng+jpn` — additive, never a silent overwrite. A name that is not
  lowercase letters, digits and underscore is refused too — it would otherwise
  become a filename under the tessdata directory — and the Settings dialog says
  so instead of saving it.

  The **OCR languages** field beside it is that setting, and it is on screen
  whether or not something is wrong, because the list has to be trimmed as well
  as grown. Every model in it competes for every word: measured on one Korean
  line, `kor` took 33 ms, `eng+kor` 68 ms and `kor+eng+jpn` 101 ms for identical
  text at identical 92% confidence, and on clean English the extra models changed
  nothing but the time (149 → 332 ms). Where they do change the answer is the
  ambiguous case — a degraded capture where `eng+jpn` produced a `デ` where `eng`
  had a dash — and the mixed one, where `kor` alone read "Manager Kim!" as
  `1308 ㅎ 86@『 시 머 !`. So a list that has grown past what the box needs is
  offered `Use kor+eng` — the source's model plus `eng`, which is what carries
  the Latin names and UI labels any box can contain — while a list that already
  is that is left alone: `eng+kor` over Korean is a choice, not a mistake.

  The field takes exactly what `-l` takes, which is the escape hatch for anything
  the button does not cover. Names are stored `+`-separated, so `kor eng` is
  accepted and saved as `kor+eng`: tesseract splits that argument on `+` alone,
  and `-l "kor eng"` makes it load one model named "kor eng" and give up on all
  of them.

From a terminal the pair is reachable as flags, and `lintranslator check` validates it:

```bash
.venv/bin/python -m lintranslator run --source-lang jpn_Jpan --target-lang eng_Latn
.venv/bin/python -m lintranslator check        # flags a code NLLB cannot score
```

## Glossary

Machine translation gets titles, names and invented jargon wrong predictably.
Edit `translate.glossary` in `config.json`:

```json
"glossary": { "管理者": "マネージャー" }
```

A plain `"key": "value"` entry rewrites the **output**. That is the common case:
NLLB renders the title "Manager" as the job word 管理者, and patching the known
wrong output is cheap and deterministic.

For explicit control use the structured form:

```json
"glossary": {
  "pre":  { "Manager": "Executive Manager" },
  "post": { "管理者": "マネージャー" },
  "case_sensitive": false
}
```

`pre` rewrites the source before translation, for disambiguation. **Measure it
before relying on it** - on this project's own sample lines, rewriting "Manager"
to "Executive Manager" made the output worse, not better:

| source | NLLB output |
|---|---|
| `Manager, the results are in.` | `管理者結果が出ました` |
| `Executive Manager, the results are in.` | `経営責任者成果が届きました` ("CEO") |

That is why the built-in Limbus glossary is post-only. Set
`use_builtin_glossary: false` to drop the built-ins, which are layered *under*
your entries so your terms always win.

Matching is case-insensitive, longest-key-wins, and boundaries are ASCII-only so
that an entry like `管理者` still matches inside `管理者異常` - a `\b`-anchored
pattern silently fails there, because Python counts CJK as word characters.

Glossary edits take effect immediately, including for lines already in the cache:
the cache stores the raw model output and terms are applied on the way out.

## Remote models (OpenRouter)

The local model is fast but literal. When you want better prose, point LinTranslator at
a hosted model. OpenRouter gives you many models behind one key and speaks the
OpenAI chat-completions protocol, which is also what this backend uses for any
compatible endpoint.

```bash
# 1. key - simplest is the GUI (panel or picker -> Settings -> API key).
#    The environment is also honoured, and takes second place to a saved key.
export OPENROUTER_API_KEY=sk-or-...

# 2. see what your key can reach
.venv/bin/python -m lintranslator models --backend openrouter --filter gemini
.venv/bin/python -m lintranslator models --backend openrouter --free     # free tier only

# 3. point the config at one
.venv/bin/python -m lintranslator check --backend openrouter --model google/gemini-2.0-flash-001
```

To make it permanent, set it in `config.json`:

```json
"translate": {
  "backend": "openrouter",
  "model": "google/gemini-2.0-flash-001",
  "api_key": null,
  "temperature": 0.0,
  "prompt": "",
  "glossary_hint": "Faust keeps her name in Latin script. Manager is a title."
}
```

* **`model` is required** and has no default. Model ids change often, and a stale
  default would fail confusingly rather than obviously.
* **`api_key: null` means "read the environment"**, checked in this order:
  `OPENROUTER_API_KEY`, then `LINTRANSLATOR_API_KEY`. A key saved through the GUI
  wins over the environment, and the Settings dialog says which one is in use.
  `lintranslator check` masks the key when reporting it.
* An error body a provider returns is collapsed to a single line, capped at 200
  characters, and has anything credential-shaped — including the configured key —
  replaced with `***` before the card shows it, because that text ends up in bug
  reports.
* A key saved from the GUI is written to `config.json` **in plain text**. That is
  a deliberate trade: a GUI that requires an environment variable is not a GUI.
  What is not a trade is who can read it: the file is written 0600 in your own
  config directory (`~/.config/lintranslator/`), not beside the source.
  Use **Clear** to remove it, or leave the field empty to rely on the environment.
* **`prompt`** is the instruction sent to the model before every line, and the
  single biggest lever on quality: use it to say which game is being translated
  and how its characters speak. `{source}` and `{target}` are replaced with the
  language names. It is read by the three chat backends (`openrouter`, `openai`,
  `chat`) — the local ones decode a language code rather than follow an
  instruction, and DeepL has no prompt parameter, so Settings hides the field for
  them. **An empty value means the built-in default**, which is also the
  **Default prompt** entry in the Settings preset list (`Limbus Company` and
  `Literal / faithful` are the other two starting points); clearing the field and
  saving is how you go back to it. A prompt that never names the target language
  gets a "translate into X, output only" line appended, so a vague prompt still
  translates instead of being answered as chat.
* **`glossary_hint`** appends free-form instructions to the translation prompt.
  This is how you steer a large model on names and tone - use it instead of
  `glossary.pre`, which is for the local model.
* **`api_base`** overrides the provider URL, which is also how you reach any
  OpenAI-compatible server (llama.cpp, Ollama, vLLM, Groq, Together) with
  `"backend": "chat"` — see below.

### Comparing models

The picker has a **Translate** button, and the preview updates as you adjust the
region, so you can frame a region and immediately see how the configured backend
renders it - without running the full panel. The backend is changed in **Settings**
inside the picker, and saving re-translates the current selection, so you can
compare without reopening anything.

Translations are cached per **backend and model**, so switching models always
re-runs the new one. Without that, a switched model would appear to produce
identical output because it was being served the previous model's cached result.

## Pointing LinTranslator at your own endpoint

The `chat` backend speaks the OpenAI `/chat/completions` protocol, so it works
with llama.cpp, Ollama (`http://localhost:11434/v1`), vLLM, Groq, Together, or
any gateway in front of them. `translate.api_base` is the base URL **without**
`/chat/completions`, and it is editable in Settings → **Base URL** — no
`config.json` editing needed.

With no base URL the backend refuses to start rather than silently defaulting to
OpenAI's endpoint: the endpoint *is* this backend, so there is no sensible
default to guess at.

No API key is required. A local server that ignores auth is sent no
`Authorization` header at all; a hosted gateway uses the same key field in
Settings, or `LINTRANSLATOR_API_KEY`.

The base URL must be `https://`. Plain `http://` is allowed only for loopback
(`localhost`, `127.x.x.x`, `::1`); for a server elsewhere on your LAN set
`translate.allow_insecure_http: true`, and the app says why it is asking — the
key rides in a header and the text being translated rides in the body.

One line in `config.json` is enough:

```json
"translate": { "backend": "chat", "model": "qwen2.5-7b-instruct", "api_base": "http://localhost:11434/v1" }
```

`lintranslator check` reports the endpoint and where the key came from:

```
translate backend: chat (English->Japanese)
  model: qwen2.5-7b-instruct
  api base: http://localhost:11434/v1
  api key: none (fine for a local server that ignores auth)
```
