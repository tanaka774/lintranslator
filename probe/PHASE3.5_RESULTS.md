# Phase 3.5 results — GUI settings, prompt, and the settling bug

## The bug report: "auto-detection of text change doesn't work"

Reproduced and fixed. It was not one bug but three, and the pipeline was
innocent each time - every failure was in how it interacted with a live screen.

### What the live diagnostic showed

Instrumenting a real run on this desktop made it obvious:

```
 1 changed=0.01270 ocr_run=True held='See Ga ae AG: y/ Bus @ Bash - In…'
 2 changed=0.00751 ocr_run=True held='i Qa gh 3 y/ 4. @ Bash - Instrum…'
 4 changed=0.00219 ocr_run=True held='tag Nae Si 4 f/ | Ow ye." @ Bash…'
```

The held text changes on nearly every poll. The region contained a busy desktop
rather than dialogue, so OCR returned noise, each read differed from the last,
and the held text never settled. Two structural causes:

1. **A live screen is rarely pixel-stable.** A blinking advance cursor, an
   animated portrait, drifting particles, or simply the mouse moving over the
   capture region all change pixels, so OCR re-runs and the held text keeps
   being replaced.
2. **The release timer reset on every change**, so a jittering read reset it
   forever. Nothing could ever be translated.

### Three settling designs, two of which fail

| rule | why it fails |
|---|---|
| N identical consecutive reads | a still screen produces one read and no confirmation |
| timer since the text was **first held** | jitter replaces the held text, resetting the timer forever |
| timer since the text **last changed** | still never fires: an oscillating read changes on *every* poll |

The working rule accepts **either** test: two reads within 2 characters are the
same line seen twice, and so are two reads that are >= 85% similar on a line long
enough (24+ chars) for a ratio to mean anything. Either one counts as the same
line and does not restart the stability window. A typewriter reveal grows by far
more than 2 characters and stays well under the ratio floor, so it is still held
until it finishes.

The ratio half is not a nicety - an earlier revision used edit distance alone and
was wrong on real screen content. Edit distance is exact on short lines, where a
ratio punishes absurdly (`line` vs `line.` scores 0.89, which any sensible
threshold rejects, yet they are obviously the same line). But on a long line,
jitter is not "a character or two": a live 70-character line came back with 2-10
character edits between polls on **identical pixels** (an unstable run of leading
em-dashes, a trailing cursor glyph). Edit-distance-only classified those as new
lines, which reset the settle window on every read, so `stable_for` never reached
`settle_window` and **nothing was ever translated** - while the timed re-read that
is supposed to rescue a jittery line never ran, because the region changed pixels
on every poll and took the change path instead. See `probe/live_trace.py`.

A second mechanism, `detect.refresh_interval` (default 0.9 s), re-reads the last
frame on a timer even when pixels are unchanged, so a screen that never goes
still can still confirm a line.

Verified: the previously-failing case (alternating OCR reads + a blinking cursor
that keeps the frame changing) now emits exactly one translation.

### Why the user saw nothing at all

Separately, the reported command was `lintranslator gui --pick`, which opens *only* the
region picker - it never started a pipeline, so no translation could appear. That
was a usability failure, not a code bug, and is fixed by making the default flow
pick-then-watch.

## New GUI features

### Settings dialog (panel → Settings)

| Setting | Implementation |
|---|---|
| Backend | dropdown over all seven backends |
| Model | free-text entry, preset dropdown, **Fetch list** loads every model the key can reach (background thread, since it is a network call) |
| API key status | shows whether a key was found and masks it (`sk-or-…3456`) |
| Prompt | presets + editable text view; `{source}`/`{target}` substituted |
| Capture area | pixel dimensions shown; grow/shrink per axis; move; or use the picker |
| Display area | font size, card width, show/hide the source line |

Saving applies live: the pipeline is restarted with the new backend, model and
prompt, and the CSS is rebuilt so font changes take effect immediately.

### Prompt presets

Three presets ship. The **Limbus Company** one names the game, the Manager title,
the twelve Sinners, and recurring terms (Distortion, E.G.O, Mirror Dungeon,
Golden Bough), plus per-character voice notes (Faust clinical, Ryoshu crude,
Don Quixote theatrical). It also instructs the model to keep names in Latin script
rather than transliterating, and to preserve `[ ]` brackets.

Templating uses explicit `.replace()`, not `str.format`, so a user prompt
containing JSON braces cannot raise.

### Default flow

`lintranslator gui` now shows the picker and then continues into the panel. `--panel`
skips straight to the panel; `--pick` still stops after the picker.

## Also fixed

* **Cache namespacing** (found while adding model switching): cache keys are now
  scoped per backend *and model*, so switching models cannot serve the previous
  model's cached output - which would have looked exactly like the new model
  being no better.
* **`lintranslator models`** lists OpenRouter models, with `--filter` and `--free`.
* **`lintranslator check`** reports remote backends: model, API base, and whether a key was
  found (masked).

## Tests

104 total, up from 81. New coverage: 14 region-arithmetic and prompt tests
(including that a region grown at the screen edge cannot overflow, and that
scaling by 1.0 is a no-op), the oscillating-read regression, the
similarity/edit-distance distinction, and prompt templating with stray braces.

## Verification limits

* The settings dialog and picker were verified by offscreen render, and all three
  GUI modes were smoke-tested for clean startup. Drag interactions and button
  clicks were driven programmatically, not with a real mouse.
* OpenRouter was exercised with a stubbed transport plus a real bogus-key request
  (clean 401). No live model call was made - no credentials here.
* The settling fix is verified against scripted screens that reproduce the failing
  conditions, not against Limbus Company itself.

## Follow-up: auto-detect moved the selection

Reported from a real run: pressing Auto-detect moved the box rather than refining
it. Cause: `calibrate()` scanned the whole screen and returned the best-scoring
text block anywhere. With a browser or chat window visible behind the game that
is a different block entirely, so the button teleported instead of refining.

Fix: `calibrate(near=...)` confines the search to a window around the caller's
current selection (margin proportional to the region, capped at 120 px so a large
region cannot swallow the screen). The picker passes its `sel`, so the button now
refines.

Two things this exposed:

* **Filtering on the block's bounding box is not enough.** `_merge` joins rows
  across gaps, so a candidate can span the whole screen while its real text is a
  thin band inside it. The filter therefore uses the rows that actually carried
  ink (`_Block.text_y0/text_y1`), and requires genuine row *and* column overlap.
* **The search band had to follow the hint.** `bottom_fraction` limits the scan to
  the lower 45% of the screen on the assumption that dialogue lives there. A
  selection above that line would have been excluded before the filter ran, so
  "near" a top-half selection could never succeed. The band now starts above the
  hint when one is given.

Verified with a fixture containing a bright "chat window" block above the dimmer
dialogue block: the near-hint result stays in the dialogue band and is identical
across three different but similar hints, whereas a blind scan is free to pick the
brighter block. Also verified that text in the top half is findable via `near`
while the blind scan correctly returns nothing there.

Six new tests in `tests/test_calibrate.py`.

---

# Follow-up: live capture/translate was genuinely broken

The user's report — "live-capturing to detect the text change before the
translation ... this app cannot do it at all" — was correct, and two real bugs
were behind it. Both were only visible by driving the real `Pipeline` with
scripted frames at realistic pacing.

## Bug 1: every line was translated twice

The tick path released the held text **without recording it**:

```python
if self.settler.tick(now):
    held = self.settler.held
    if held and held != self._last_settled_text:
        return self._emit(...)      # emits, but never records
    self.settler.reset()            # held text forgotten
```

The very next poll re-read the same text, saw no match in `_last_settled_text`,
and emitted again. Measured: 5 emissions for 3 lines, the duplicates arriving
~0.4 s apart — in the panel that looks like flickering or repeated output.

## Bug 2: OCR jitter defeated the duplicate check

Even after fixing that, the same line came out twice, because the dedupe used
**exact string equality**. OCR reads the same line slightly differently between
polls — a blinking advance cursor appeared as a trailing glyph:

```
'AAAA first line of dialogue here. l'   (cursor visible)
'AAAA first line of dialogue here.'     (cursor dark)
```

Exact matching treats those as two different lines. Fixed by reusing the settler's
edit-distance comparison (`is_same_reading`) for the dedupe as well.

Result after both fixes: exactly one translation per distinct line.

## Also fixed

* **OCR thrashing on a blinking screen.** A blinking cursor changes pixels every
  poll, so the change detector fired constantly and OCR ran at 100% of polls.
  Added `detect.ocr_min_interval` (0.25 s), kept well under `settle_window` so it
  cannot meaningfully delay translation.
* **Time-base inconsistency.** `TextSettler.stable_for` read the wall clock while
  `Pipeline.step(now)` accepted an injected clock, so the settle window behaved
  differently under test than in production. Replaced with `stable_seconds(now)`,
  and `Pipeline.start(now)` now accepts a clock so the whole pipeline can run on
  one time base.
* **`calibrate()` threshold was hard-coded at 150.** Dimming the reference
  screenshot to 55% made detection return `None`; a dimmer game UI would silently
  produce "detected a box but OCR found no text in it". The threshold is now
  measured from the image (background mean + 55% of the robust maximum).

## Auto-detect removed

The user asked for it to be removed, and they were right to. A one-shot region
finder that guesses at screen content caused more confusion than it saved —
including being mistaken for the continuous detection they actually wanted. The
button and its debug view are gone from the picker. `lintranslator/calibrate.py` is kept
as a tested library helper (documented as unused by the GUI) since it is useful
from a script.

## Verified end to end on the live screen

Not a stub: real portal captures, real tesseract, real ct2 translation, on a
static text region:

```
EN: Ww Vv F] Paste QFind = .
JA: Ww Vv F] ペースト QFind = .
polls=48 changed=1 ocr_runs=2 refreshed_reads=1 errors=0
translate 277 ms
```

Note `refreshed_reads=1`: the timed re-read is what confirmed the line, which is
exactly the mechanism that was broken.

## Tests

118 total, up from 111. New ones cover: one translation per distinct line, jitter
not duplicating, a static screen not repeating, OCR being gated by the change
detector, the throttle engaging at a fast poll rate, and a never-static screen
still translating.

---

# Follow-up: orphaned "Waiting for dialogue…" window on close

Reported: after closing the app, a small box reading "Waiting for dialogue…"
stayed on screen.

## Cause

Only the picker's **Close button** quit the application. Closing the window any
other way (the title-bar X, or the window manager's close) just closed that
window, leaving the translation panel alive:

```
--- closing the PICKER window (as the window manager X would) ---
app still running: True
windows still open: 1
   TranslatorPanel visible=True title='LinTranslator'
picker._panel still set: True
   panel pipeline worker running: True
```

So the panel stayed visible with its pipeline still capturing, showing its idle
text. A GTK application keeps running while any window exists, and that panel was
one.

## Fix

* The picker handles `close-request`: it stops and destroys the panel, disconnects
  the panel's own close handler first (which would otherwise try to re-show a
  picker that is going away), then quits the application on an idle callback.
* The panel handles `close-request` itself and stops its worker, so it is
  self-cleaning regardless of how it was closed rather than depending on
  application shutdown order.
* The Close button now routes through the same shutdown path.

## Verified

```
panel opened: True | worker running: True
--- closing the picker (X / Close) ---
app.run() returned: 0 -> process exits cleanly
```

```
before close -> worker alive: True
after  close -> worker alive: False
lintranslator threads left: []
```

No orphaned window, no leaked pipeline thread. To observe the thread exit at all
the main loop had to be held open (`Gio.Application.hold`); otherwise the process
exits and daemon threads die with it, which is why this was easy to miss.


---

# Live capture verified through the REAL portal path

Earlier verification of the change-detection loop replaced the grabber, so it
skipped `ScreenGrabber` -> xdg-desktop-portal -> PNG -> crop. That is precisely
the link a user means by "the app captures the screen", so it was the weakest
part of the evidence. It has now been tested end to end.

## Method

A fullscreen GTK window renders dialogue lines low on the screen. The pipeline
runs with the **real** `ScreenGrabber`, so screen capture, cropping, change
detection, OCR and translation are all genuine; only the on-screen content is
scripted.

## Result

Three lines shown in sequence, each held ~6 s:

```
t= 0.4  ocr:changed          ocr= 1  held='> AAAA first dialogue line on screen'
t= 1.9  emit:tick-timeout    ocr= 3
        >>> EMIT '> AAAA first dialogue line on screen'
t= 2.6  skip:no-pixel-change ocr= 3      <- static screen, OCR correctly skipped
t= 6.4  ocr:changed          ocr= 4      <- text changed, detected
t= 7.8  emit:tick-timeout    ocr= 4
        >>> EMIT '> DDDD second dialogue line on screen'
t=18.0  ocr:changed          ocr=22
t=19.4  emit:tick-timeout    ocr=24
        >>> EMIT third line

translations: 3 of 3
stats: polls=34 changed=24 ocr_runs=24 errors=0
capture: 31 grabs, 108 ms each, real 2560x1440 portal captures
```

So the cycle is: **static screen -> no OCR; text change -> detected -> OCR ->
settled -> translated once -> back to no OCR until the next change.** Which is
the behaviour "live capture" is supposed to have.

## Caveat on this test

The OCR text came out garbled (`> AAAA firet Aianlaniian`) because the target
window used GTK's default UI font at 3x over an unrelated background. That is a
limitation of the *test target*, not of the pipeline: detection, settling,
dedup and translation all behaved correctly on it. It does mean this run says
nothing about OCR accuracy on Limbus Company specifically - only that the
capture-and-detect loop fires on every text change through the real screen
capture path.

## How to diagnose a real run

`lintranslator run --verbose` prints the per-poll decision, so a failure can be located
rather than guessed at:

| output | meaning |
|---|---|
| `ocr:changed` / `ocr:refresh` | capture and detection are working |
| `skip:no-pixel-change` | screen static; expected between lines |
| `emit:settled` / `emit:tick-timeout` | a line was translated |
| `skip:already-translated` | the same line is still on screen |
| `skip:no-text` | OCR found nothing in the region |
| `skip:throttled` | a fast-changing region is being rate-limited |

---

# Follow-up: Quit did nothing (needed a second press)

Reported: pressing **Quit** on the panel appeared to duplicate it, and quitting
only worked on a second press.

## Cause

`_on_quit` called `application.quit()` and nothing else. GTK's `quit()` only
*requests* a shutdown, and with two windows open the request was swallowed - so
the panel stayed on screen and the picker's close handler ran. Pressing Quit
again then worked, because by then the first request had taken effect.

Reproduced by holding the main loop open and counting windows after the press:

```
1. startup      : panels=1 pickers=1
2. after Quit#1 : panels=1 pickers=1 windows=['TranslatorPanel', 'RegionPicker']
   picker._panel: True
   picker visible: True
```

Nothing closed.

## Fix

Quit now closes every window outright rather than asking the application to:

```python
self.close_pipeline()
application = self.get_application()
if application is not None:
    for window in list(application.get_windows()):
        window.destroy()   # destroy, not close: the picker's close handler
                           # would otherwise re-show a window on its way out
    application.quit()
```

The worker join timeout in `PipelineThread.stop` was also reduced from 5 s to 1 s.
A pipeline step is ~0.3 s, so the longer wait only froze the UI; the thread is a
daemon and cannot hold the process open regardless.

## Verified

```
Quit returned in 0.04s
app.run() returned rc=0  -> single press is enough
```

```
startup                panels=1
after Watch live       panels=1
after close            panels=0
after reopen           panels=1
after 2nd Watch live   panels=1
```

No path produces a second panel.

---

# Follow-up: Quit closed everything, then another panel appeared

Reported after the previous Quit fix: pressing **Quit** closed the picker and the
panel, and then a new panel appeared.

## Cause: the default flow ran two separate GTK applications

`lintranslator gui` (no flags) did this:

```python
if not skip_pick:
    code = run_gui(cfg, MODE_PICK, ...)   # application #1: picker
    ...
return run_gui(cfg, MODE_PANEL, ...)      # application #2: panel
```

`run_gui` calls `Gtk.Application.run()`, which **blocks until the application
exits**. So the sequence was:

1. Application #1 starts: picker + the panel it manages.
2. Pressing Quit closed both and ended application #1.
3. `run_gui` returned, and the CLI started **application #2**, which built a
   fresh panel.

Hence "another panel appears". It was a second application launching, not a
duplicated window - which is why the window counts looked correct at every point
I had measured.

## Fix

One application owns both windows. The picker already creates and manages the
panel, so the CLI no longer starts a second one for the default mode:

```python
if skip_pick:                      # --panel
    return run_gui(cfg, MODE_PANEL, ...)
return run_gui(cfg, MODE_PICK, ...)   # default and --pick: picker owns the panel
```

## Verified

```
GTK application #1 starting (mode=pick)
  panel #1 created
  windows: ['RegionPicker', 'TranslatorPanel']
-> GTK applications started: 1, panels created: 1
```

```
pressing Quit (panel exists: True)
  cli.main returned rc=0 after 2.2s
  panels created in total: 1  <- must be 1
```

Both GUI modes still launch and stay open (`--pick`, `--panel`).

---

# Follow-up: the app was capturing and translating its own windows

Reported as "weird behaviour" around **when capture starts** and **what area it
reads**. Both were real, and both were measured on a live screen with Limbus
Company running rather than reasoned about.

## What the measurement showed

The picker's `_on_start` (the "Watch live" button) returned in **1 ms** with the
worker already running, and the first `grab_full()` **started 10 ms after the
click** - with the picker still mapped on top of the box. The portal then took
171-275 ms, so the first pixels were read at ~0.2 s, the first OCR at ~0.5 s, and
the first emission at ~2.0 s (settle window included).

Per-grab OCR of the captured region, exactly as the pipeline saw it:

```
[ 0] t= 0.01s picker(mapped=True) 'a a = 7 Although the trial won't be open to the public, you may,'
...
[ 1] t= 0.51s picker(mapped=True) 'captured 2560x1440 — dra If by "beyond p. : a anything new. It w'
[ 2] t= 1.01s picker(mapped=True) 'captured 2560x1440 — dra If by "beyond p. : ae anything new. It '
...
[ 8] t= 4.51s picker(mapped=False) 'If by "beyond prediction" he meant something that might get the '
```

`probe/handoff_timing.py` drives the real `_on_start`, the real `PipelineThread`,
the real portal grabber and real tesseract; only the display content is whatever
was on screen. Saved evidence: `data/probe_polluted_first_crop.png` (exact region
pixels from a polluted first capture) and `data/probe_clean_crop.png` (same region,
no app windows).

Through the configured OCR settings (`min_confidence = 55`), the polluted frame
became:

```
KEPT     conf= 87.0  'Watcning...'
KEPT     conf= 93.2  'captured 2560x1440 — dra'
DROPPED  conf= 54.7  'One might come to... py ny'      <- a real dialogue line
KEPT     conf= 95.4  'without letting them in on all available information...'
```

and the string handed to the translator was:

```
'Watcning... captured 2560x1440 — dra without letting them in on all available
 information... Ah, | can practically hear the complaints lodged in the
 tick-tocks of your winding clockwork. é.'
```

So the app's own UI was read at *higher* confidence than the game text, and a
dialogue line underneath it was silently dropped. `probe/first_emit_sim.py`
replays that frame through the real pipeline: **one emission, 2.0 s after the
click, of that exact string.**

### The area itself was never wrong

`ScreenGrabber.grab()` is byte-identical to `Image.crop(region.to_pixels(...))`
on the same PNG, and the fraction round-trip is exact (`0.115625 -> x=296`,
`w=1175` on 2560x1440). The failure was *when* reading started, not *where*.

### A second, separate defect: the box and the area could disagree

Driving the real picker: press Watch live, drag a new box, press Watch live again-

```
press 2: config region = (0.5199, 0.8, 0.3, 0.1)
         panel worker is the SAME object: True
         worker still capturing = (0.115625, 0.7958, 0.4589, 0.1146)
```

Only **Save** applied the new box, by tearing the panel down and rebuilding it -
which reloaded the model and let the compositor move the card. The picker's own
status line claimed "Adjusting it restarts watching with the new area."

## Fixes

| # | Fix | Where |
|---|---|---|
| 1 | The picker **minimises itself** on Watch live, and the pipeline **does not capture at all** while a LinTranslator window is mapped | `picker.py`, `occlusion.py`, `pipeline.py` |
| 2 | The pause is a **condition, not a delay**: reading resumes the moment the window actually unmaps | `occlusion.py`, `panel.py` |
| 3 | Pausing **forgets held text** and resets the change detector, so a resume cannot release a stale line or a paused reveal's fragment | `pipeline.py` |
| 4 | The panel gets a **Region** button to bring the picker back; while it is up the panel says why reading is paused | `panel.py`, `picker.py` |
| 5 | The area can be **re-pointed while the loop runs** (`request_region`), applied on the worker thread - no model reload, no card jump, and the box on screen is never a lie | `pipeline.py`, `panel.py`, `picker.py` |
| 6 | **Self-text guard**: reads matching LinTranslator's own chrome - or echoing the translation just produced - are dropped and reported, never translated | `selftext.py`, `pipeline.py` |
| 7 | `Event.total_elapsed` is now measured (capture -> translation) instead of hardcoded `0.0`, and `capture_ms` exists; `Frame.elapsed` covers the whole grab and reports the portal's share separately | `pipeline.py`, `capture.py` |
| 8 | Re-capturing the screen keeps the box just dragged instead of resetting it to the saved one | `picker.py` |

## Verified

The same live probe, after the fix:

```
GATE transitions
  t= 0.01s  PAUSED: the region picker is on screen — minimise it to keep translating
  t= 0.51s  resumed — reading is allowed
  first grab starts at t= 0.51s

[ 0] t= 0.51s guard=None conf= 92.4 "Although the trial won't be open to the public, you may, as clos"
```

and the region/pixel comparison against a frame with no app windows:

| | before | after |
|---|---|---|
| region differing while the picker was up | **19.6-20.1 %** | **0.0-0.1 %** (every grab) |
| first capture content | picker status line + occluded line | game dialogue, conf 92 |

171 tests pass, up from 153. New coverage: the pause and its resumption, held text
not surviving a pause, live re-pointing (and that a new area is read as new), the
self-UI and self-echo guards, the measured event timing, the guard/self-text rules
(including that real dialogue is never flagged), and the picker-to-pipeline wiring.

## UX notes

* The picker disappearing on Watch live is deliberate and reversible: the panel's
  **Region** button brings it back, and its taskbar entry stays.
* Every pause is explained in the panel's status line ("paused — the region picker
  is on screen…"), because an unexplained pause is indistinguishable from a broken
  translator - which is what this whole round was about.
* The panel status now ends with the real age of the line (`· 2.0s after capture`)
  rather than only the translation call time.

## Addendum: minimise() is ignored on this compositor

The first version of the fix minimised the picker and only hid it if it was still
on screen after 500 ms. `probe/minimise_check.py` shows the fallback is not a
fallback here — it is the mechanism:

```
mapped samples: [(0.05, True), (0.25, True), (0.45, True), (0.65, True), (0.85, True),
                 (1.05, True), (1.26, True), (1.46, True), (1.66, True), (1.86, True)]
minimize() unmapped the window: NO
```

kwin_wayland 6.7.4 with GTK4 keeps the window mapped for the full two seconds after
`Gtk.Window.minimize()`. Both calls are kept - minimise where a compositor honours
it (the taskbar entry survives), hide where it does not - and the hide fallback now
runs after 300 ms rather than 500 ms, because the window's own unmap round trip
costs another ~200 ms either way.

Measured start latency after the fix, from the click:

```
t= 0.01s  PAUSED: the region picker is on screen — minimise it to keep translating
t= 0.51s  resumed — reading is allowed
          first grab starts at t= 0.51s   (picker mapped=False, guard=None)
```

---

# Follow-up: "text changes feel slower, and sometimes nothing is detected"

Reported after the self-capture fix shipped. Reproduced, and it was three things -
one of them a pre-existing bug in the settler that the fix made easy to hit.

## 1. The card grew, so it reached into the box (introduced here)

`data/panel_idle.png` (before) is **560 px** wide; after the self-capture round it
was **664 px**. The cause was the new status suffix: a `Gtk.Label` without wrapping
reports its whole text as its *minimum* width, so the card grew to fit
`"... · 2.1s after capture"` instead of honouring `display.width`.

A wider card is a card that reaches further into the region it is reporting on.
Fixed by wrapping the status and backend labels (and shortening the suffix to
`+2.1s`), which puts the card at **360 px** - the configured width, and 200 px
narrower than it was before any of this work.

## 2. Dropping the whole read threw the dialogue away (introduced here)

The first version of the self-text guard checked the *joined* read and discarded
all of it when any of our text was in it. With the panel clipping the box, the
panel's status line changes on every poll, so every read was "ours" and the
dialogue under it was never translated:

```
clean box            before 4/4   drop-read 4/4   per-line 4/4
panel clipping box   before 1/4   drop-read 0/4   per-line 4/4
```

Fixed by filtering **per OCR line** (`OcrResult.without_lines`): our lines go, the
game's stay. Latency in the overlap case is now identical to a clean box
(`probe/detect_latency.py`).

## 3. A shorter line could never replace a longer held one (pre-existing)

The real find. `TextSettler.observe` kept the longer read whenever the new read was
judged a *different* line:

```python
if not is_same_reading(text, self._held_text, ...):
    self._changed_at = now
    if len(text) >= len(self._held_text):   # <- stale text survives
        self._held_text = text
```

So if a long line was held (not yet settled) and a *shorter* line replaced it, the
held text stayed stale: the new text differed from it on every poll, the stability
window restarted every poll, and **nothing was translated again** until a longer
line happened to appear. Measured live - the held text frozen while the screen had
moved on:

```
held='— For lack of any meaningful help they could provide; given the nature'
kept text: "Although the trial won't be open to the public, e associates, attend."
decision=ocr:changed   (44 polls, no emission)
```

"Keep the longer read" is correct for a reveal in progress and for jitter of one
line; applied to a genuinely different line it wedges the settler. Fixed: a
different line is adopted whatever its length.

## Live numbers after the fixes

`probe/live_lines.py` - fullscreen window, real portal capture, real tesseract,
one line at a time, 3 lines:

| scenario | detected | latency (line on screen -> translation ready) |
|---|---|---|
| clean box | 3/3 | 1.45 s, 1.45 s, 1.45 s |
| our status line inside the box | 3/3 | 1.91 s, 1.91 s, 1.91 s |

Start of watching is unchanged at ~0.5 s from the click to the first capture (the
window-unmap round trip dominates; the hide fallback was reduced 500 -> 150 ms).

## Also tightened

* The echo guard now requires the backend to have handed the *source* back
  (>= 0.97 similarity), rather than "roughly similar" - a real translation that
  contains the source text ("[ja] ...", a kept character name) must not disable it.

## Tests

188 total, up from 185. New: a shorter line replacing a longer held one (settler
and full loop), jitter of one line still not restarting the window, a mixed read
keeping the game line, and the card honouring `display.width`.

---

# Feature: Re-read (button, in-window shortcut, global hotkey)

A line that comes out wrong - garbled OCR, a truncated read, a translation the
model fumbled - had no recovery except waiting for the next line. Now there is a
**Re-read** control that reads the box again immediately and translates it from
scratch.

## What "again" means

`Pipeline.request_reread()` is applied on the worker thread like every other
cross-thread request, and it deliberately skips all four things that normally
suppress a repeat: the change detector, the OCR throttle, the settle window and the
`_already_translated` check - plus the translation cache
(`CachedTranslator.translate(..., force=True)`). Pressing a button that answers
from the cache would look like the button did nothing.

The request is only consumed when the screen is actually read, so pressing it while
reading is paused queues it ("re-read queued — reading is paused") and it fires as
soon as reading resumes. A re-read that finds nothing says so
("nothing readable in the box — check the region") instead of looking broken.

## Three ways to trigger it

| how | works while the game has focus | verified |
|---|---|---|
| global hotkey (compositor-granted) | yes | portal accepts the request; **refused from a terminal launch** (see below) |
| `lintranslator reread` over the control socket | yes | end to end, separate process |
| Ctrl+R / F5 in the panel | no (needs the panel focused) | yes |

The control socket is one line in, one line out, in `$XDG_RUNTIME_DIR` (falling
back to a per-user cache dir, then a uid-suffixed temp path, so a sandboxed session
still works). It refuses to listen when another GUI already owns the socket, so a
second instance can never cut off the one the user is looking at.

`probe/reread_check.py` drives the real picker and panel and checks all three:

```
[ok ] control socket listening — /…/LinTranslator.sock
[ok ] button sets the status — re-reading the box…
[ok ] control socket answers — ['re-reading']
[ok ] `lintranslator reread` from another process — rc=0 out='re-reading'
[ok ] `lintranslator status` reports state — watching, reading, backend none, hotkey not bound
[ok ] a translation arrived (the re-read translated it)
```

## The global hotkey, and why it needs an application id

`org.freedesktop.portal.GlobalShortcuts` is present on this machine, so LinTranslator asks
the compositor for a shortcut itself (`lintranslator/hotkey.py`, Gio.DBus, async, with a
timeout so an unanswered dialog cannot hang anything). Launched from a terminal the
portal refuses outright:

```
[not bound] CreateSession: GDBus.Error:org.freedesktop.portal.Error.NotAllowed:
            An app id is required (36)
```

That is a launch-context rule, not a bug: the portal derives an application id from
the caller's systemd scope (`app-*.scope`), which a KDE menu launch creates and a
terminal launch does not. So the shortcut is attempted, the refusal is reported in
words that name the fix, and two fallbacks always work:

* `packaging/LinTranslator.desktop` + `packaging/tlk-gui` — launch from the menu and the
  portal path works with no setup at all;
* bind `lintranslator reread` as a KDE custom shortcut — works from any launch, and
  `lintranslator shortcut` prints the exact command with absolute paths.

The panel says which is in force, so this is never a silent absence.

## Layout

Six buttons would have pushed the card past `display.width`, so the controls are two
right-aligned rows (Region · Re-read · Settings / Copy · Pause · Quit). The card
stays at 360 px, which the width regression test now also covers.

## Tests

210 total, up from 200. New: a re-read re-emits the same line from a fresh read,
ignores pacing and the throttle, survives a pause, reports an empty box; the cache
is bypassed but refreshed by `force`; the control socket round-trips, refuses to
steal a live socket, answers unknown commands and survives a crashing handler; the
panel's button, shortcut and control commands all reach the pipeline; and the
hotkey's failure mapping.

---

# Follow-up: the box could be grown but never shrunk

## The report

"you're supposed to be able to resize the selected square area of the capture
zone, but it currently doesn't work properly."

## What the drag actually did

`probe/region_resize_check.py` drives the real `RegionPicker` handlers
(`_on_drag_begin` / `_on_drag_update` / `_on_drag_end`) with the cumulative offsets
a `GtkGestureDrag` reports: press on an edge, move 120 px along it, look at the box.
On a 400x200 box, dragging every edge and corner in both directions:

```
grab  drag          expected                 got                      verdict
e     grow right    (1000, 700, 520, 200)    (1000, 700, 520, 200)    ok
e     shrink left   (1000, 700, 280, 200)    (1000, 700, 400, 200)    FAIL
w     grow left     (880, 700, 520, 200)     (880, 700, 520, 200)     ok
w     shrink right  (1120, 700, 280, 200)    (1000, 700, 400, 200)    FAIL
n     grow up       (1000, 580, 400, 320)    (1000, 580, 400, 320)    ok
n     shrink down   (1000, 820, 400, 80)     (1000, 700, 400, 200)    FAIL
...
failed shrinks : 8 of 8 - the edge never follows the drag
```

Every outward drag worked, and every inward one was a complete no-op: the box came
back byte-identical. That is the whole of "resize doesn't work properly" - it is
not that resizing was fiddly, it is that half of it did nothing at all.

## Cause 1: the moving edge was rebuilt from min()/max()

```python
if "w" in mode: x0 = min(left, x1 - 4)   # left = min(start_x, end_x)
if "e" in mode: x1 = max(right, x0 + 4)  # right = max(start_x, end_x)
```

The edge was placed at the pointer's *extreme* position rather than moved by the
pointer's *travel*. Dragging the right edge leftwards gives `right = start_x` (the
press point), so the edge is pinned exactly where the pointer went down and never
follows the drag. Dragging right gives `right = end_x`, which is why outward drags
worked and hid the bug.

There was a second, matching error in the caller. `apply_drag` was handed the
selection as it changed on every event *and* cumulative offsets from the drag
start, so the two conventions disagreed about what `sel` meant. With the geometry
fixed to a delta but the caller left as it was, the same 90 px shrink lands on
`(100, 100, 20, 100)` instead of `(100, 100, 110, 100)` - the edge accelerates away
from the pointer. Both halves are needed:

```
box the drag started from (fixed wiring): (100, 100, 110, 100)
box as it changes      (old wiring)    : (100, 100, 20, 100)
```

## Cause 2: the grab tolerance was in screenshot pixels

`HANDLE = 8` was applied to screen (screenshot) pixels and then converted to widget
space, so the target shrank with the zoom. At the picker's own default size on this
2560x1440 screen (1180x960 canvas, scale 0.461) an edge could only be grabbed
within **3.7 px** of mouse travel - less than half of the 8 px the drawn corner
handle suggests, and a pixel hunt. A press a few pixels further in hit *move*
instead, which drags the whole box: the other way this reads as "resize is broken".

## Fix

| # | Fix | Where |
|---|---|---|
| 1 | A grabbed edge moves by the pointer's **travel**; the opposite edge does not move at all | `selection.py` |
| 2 | The caller passes the box **the drag started from**, so a resize is a pure function of the drag and not of how many events reported it | `picker.py` |
| 3 | Grab tolerance **floored in widget pixels** (`HANDLE_WIDGET_MIN = 8`), so an edge is never harder to hit than a pointer can manage at any zoom | `selection.py` |
| 4 | Grips drawn at every corner **and the middle of every edge**, sized to the pointer tolerance; the old affordance was corners only | `picker.py` |
| 5 | `hit_test` reports `new` when the canvas has no size, instead of claiming a corner grab - every point maps to (0, 0) there, which is within tolerance of all four edges at once | `selection.py` |

Clamping is now reachable, which it never was before: an edge dragged past the one
opposite stops at the 4 px minimum (`(1000, 700, 4, 200)`) instead of the box
quietly refusing to move. The grab offset is preserved too - grabbing 5 px inside
an edge and dragging 120 px moves the edge exactly 120 px, where it previously
snapped under the pointer and lost those 5 px.

## Verified

The same probe, after the fix: 16/16 directions correct, plus the clamps, the
off-edge grab, and the letterboxed-scale tolerance:

```
  before: 3.7 widget px (8 screenshot px)
  now   : 8.0 widget px (17 screenshot px at this zoom)
...
cases checked  : 22
failed         : none
PASS
```

`probe/region_resize_render.py` draws the canvas offscreen and samples the pixels
a couple of pixels inside each anchor - clear of the 2 px border, so a missing grip
cannot pass on the border's paint - confirming eight grips and none in the middle
of the box (`.cache/region_resize_handles.png`).

## Tests

237 total, up from 214. New: every edge and corner resizing in both directions
(parametrized), the grab offset surviving the drag, the opposite edge never moving,
a resize never inverting or degenerating the box, the same drag landing on the same
box whether it arrives as one event or ten, the tolerance floor, `hit_test` without
a canvas, and - through the real picker handlers - an inward drag shrinking the box
and a fresh drag still drawing one.


---

# Follow-up: what happens when the box has no text (or nonsense)

Asked as a question - "what would TL do if the OCR text doesn't make sense, or the
area has no text?" - so it was measured rather than described
(`probe/empty_region.py`, real tesseract with the configured gate, real pipeline).

| region | OCR said | reached the translator |
|---|---|---|
| blank | nothing (0 lines) | **nothing** — `skip:no-text` |
| background art (pattern) | `'e¢'` at **62.5 %** — *above* `min_confidence=55` | **`'e¢'` was translated** |
| small dim glyphs | `'wv f] \\|<aSte OFjnd =. 4/7 ae'` at 81.7 % | translated (garbage in, garbage out) |
| clean UI gibberish | `'Ww WF] Paste OFind =.'` at 77.0 % | translated |
| background art, changing every poll | `'e¢'` | nothing (never settles) — but OCR ran on **10 of 10** polls |

Three rails already existed: nothing to read (silent), low confidence (dropped by
the OCR gate), and our own windows (dropped by `selftext`). All three miss the same
case: **confident nonsense**. A patterned poster inside the box reads as one or two
glyphs at 60-80 %, which clears the gate, and a translation of "e¢" is worse than
no translation.

## The rail added

`selftext.noise_reason(text, confidence)` — two conservative tests, both asking
whether there is enough language present to be worth a model call:

* fewer than two letters (or fewer than two letters+digits) → not dialogue;
* fewer than twelve letters+digits **and** below `detect.short_text_confidence`
  (default 75) → too short to be sure.

Nothing longer than twelve characters is judged on confidence at all, and real
short lines ("Yes.", "Hm?") come off a clean game font in the nineties, so they
pass. Dropped reads are reported, not silent: `skipped 'e¢' — there is not enough
text there to be dialogue`. Background art now costs zero model calls; long
confident nonsense still gets through, which is the honest limit of a cheap rule -
and what **Re-read** is for.

Tests: 329, up from 320.
