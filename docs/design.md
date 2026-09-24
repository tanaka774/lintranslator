# Why it works this way

The numbers below are ones this project actually took - see
[`probe/`](../probe/) for the raw runs. They are kept because they are what stops
each choice being re-litigated from scratch.

Three decisions were driven by measurement, not preference.

## 1. Capture must go through xdg-desktop-portal

On Wayland a client cannot read the framebuffer. `mss`, `import`, X11 grabs and
friends all fail or return black. The supported path is
`org.freedesktop.portal.Screenshot` / `ScreenCast`, which KWin backs with
`zkde_screencast_unstable_v1`.

Measured on this machine: **~0.2 s per grab**, silently reusing the permission
grant (no dialog after the first call). This is fast enough to poll at 2 fps.

## 2. Tesseract, not PP-OCR

Measured on a reference dialogue frame from a commercial game, kept locally and
not redistributed (ground truth: `[The committee has resolved that this entry
warrants retention as a standing record. The material below is the file concerning
today's submission.]`):

| engine | line 1 | line 2 | warm latency |
|---|---|---|---|
| **tesseract** `-l eng --psm 6`, 3x upscale | exact | exact | **~105 ms** |
| rapidocr-onnxruntime 1.4.4 (PP-OCRv4) | `[` -> `l` | **word spaces lost** | ~410 ms |
| rapidocr 3.x (PP-OCRv6) | `[` -> `l` | exact | ~600 ms |
| tesseract on the **raw** screenshot | garbage | garbage | - |

The reference frame and the exact dialogue text behind these numbers are not in
the repository, so the table is reproduced from the recorded runs rather than
re-runnable from a checkout.

Preprocessing is not optional. The last row is the whole reason this project
crops and upscales before OCR: the same engine that is exact on a cropped,
3x-upscaled, autocontrasted region returns noise on the untouched frame.

## 3. NLLB, never `opus-mt-en-jap`

`Helsinki-NLP/opus-mt-en-jap` is the obvious-looking choice and it is unusable.
Given this project's sample line it produced output unrelated to the input:

```
in : [The committee has resolved that this entry warrants retention as a standing record. The material below is the file concerning today's submission.]
out: わたし が こう い う 理由 は , 日 ごと に すなわち , 十 分 の 一 と し て 語 ら れ た ...
```

`facebook/nllb-200-distilled-600M` is correct and fast (**~1.7 s/line** on an
8-thread CPU, greedy decoding):

```
out: [この事件は記録として保存されるべきであると決定された. 今日の要請に関する事件記録は以下のとおりです.]
```

## Design notes

**Poll rate and work rate are decoupled.** `fps` controls how often the screen is
sampled. OCR runs only when pixels change, and translation only when the OCR text
has settled. A static dialogue box costs almost nothing.

**Change detection counts changed samples, not the average difference.** A mean
metric misses the typewriter reveal: appending one word to a long line barely
moves the average. `changed_fraction` counts samples above a noise threshold, and
the trigger is `max(1, 0.0005 * n)` samples - the absolute floor keeps a small
region as sensitive as a large one.

**Settling is the subtlest logic here, and it took three attempts.** The rule is:
translate when the OCR text has stopped changing, where "stopped changing" means
*near-identical* reads, not exactly identical ones. Each earlier version failed
against a live screen in a different way:

1. *Require N identical consecutive reads.* Never fires on a still image, because
   OCR only runs when pixels change - one read, no confirmation.
2. *Release on a timer since the text was first held.* A live game is rarely
   pixel-stable (blinking advance cursor, animated portrait, drifting particles,
   or the mouse moving over the region), so the text is re-read constantly and a
   jittering read resets the timer forever. Nothing is ever translated.
3. *Release on a timer since the text last changed.* Still never fires when the
   read oscillates between two values, because it changes on every single poll.

The working rule accepts **either** test: reads within 2 characters count as the
same line, *and* so do reads that are >= 85% similar on a line long enough (24+
chars) for a ratio to mean anything. The ratio half is load-bearing, not a
nicety: jitter on a long line is **not** "a character or two". Measured on a live
70-character line, tesseract returned the same text with 2-10 character edits
between polls (an unstable run of leading em-dashes, a trailing cursor glyph).
Judging those by edit distance alone resets the settle timer on every read, and
the line is never translated at all.

A genuine line change stays far below the ratio floor (measured: 0.31 between two
consecutive game lines, against 0.90+ for noise on one line), and a typewriter
reveal grows by more than 2 characters, so both are still held until the text is
final. Short lines are decided by edit distance alone, because a ratio is
meaningless there (`Yes.` vs `No.` scores 0.67 - it would drop a real change).

**A pause mid-reveal is not the end of a line - and "stable for N seconds" cannot
tell the difference.** This shipped as a real bug: the panel showed truncated
dialogue (`...the proverbial poster child of company`) because the game held the
reveal for longer than `settle_window` and the fragment was released as final.

Two mechanisms fix it, and both are needed:

* `detect.incomplete_grace` (default 4.5 s) - text that does not end in sentence
  punctuation waits this much longer before release. Deliberately asymmetric:
  holding a finished line too long costs a short delay, while releasing a
  fragment shows the user a truncated translation.
* **Reveal detection** in the settler - when a new read *extends* the held text
  (the held text is a prefix of it, allowing a few characters of noise at the
  junction), that is the same line still being typed, not a new line. Judging it
  by edit distance alone failed this: `...child of company` -> `...child of
  company-sponsored contractors.` is a 23-character addition, so the finished
  sentence looked like a brand-new line and the fragment had already been sent.

`detect.settle_max_wait` (default 8 s) is a hard ceiling so a line the game never
punctuates still surfaces. It must stay **above** `settle_window +
incomplete_grace`; a saved config with a lower value silently disables the grace.

**The blinking caret is not part of the line.** OCR reads the advance cursor as a
lone trailing character, measured live as ` l`, ` +`, ` O`, ` é`, ` 4`, ` |`. It
is stripped in `OcrResult.text` (`strip_trailing_cursor`), because it otherwise
reaches the translator and makes consecutive reads look like different lines.

**Low-confidence OCR lines are dropped.** HUD chrome and background texture
leaking into the region used to be translated along with the dialogue, which both
wasted seconds per line and corrupted the output.

---
