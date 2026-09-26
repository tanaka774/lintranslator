"""End-to-end pipeline tests with the capture and OCR stages stubbed out.

These exist because the live screen is not a reproducible input: it changes
underneath the test (this project's own verification runs saw 24 "changes" in 26
polls simply because a chat window was animating). Stubbing capture and OCR lets
the real loop logic - change detection, settling, caching, event emission - be
tested deterministically.

The settler/change-detector interaction is the subtle part, and it already
shipped one bug: requiring N identical consecutive OCR reads never fires on a
static screen, because OCR only runs when pixels change.
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

from lintranslator.config import Config
from lintranslator.ocr import OcrLine, OcrResult, strip_trailing_cursor
from lintranslator.pipeline import Pipeline
from lintranslator.translate import NullTranslator


class StubOcr:
    """Returns a scripted sequence of OCR results, one per call."""

    def __init__(self, texts: list[str], confidence: float = 90.0) -> None:
        self.texts = texts
        self.confidence = confidence
        self.calls = 0

    def ensure_ready(self):
        return None

    def read(self, image) -> OcrResult:
        text = self.texts[min(self.calls, len(self.texts) - 1)] if self.texts else ""
        self.calls += 1
        lines = (
            [OcrLine(text=text, confidence=self.confidence, box=(0, 0, 10, 10))]
            if text
            else []
        )
        return OcrResult(lines=lines, elapsed=0.001, engine="stub", lang="eng")


def _frame(text: str, size=(400, 60)) -> Image.Image:
    img = Image.new("RGB", size, (10, 10, 12))
    if text:
        ImageDraw.Draw(img).text((6, 20), text, fill=(240, 240, 240))
    return img


class StubGrabber:
    """Yields scripted frames and counts grabs."""

    def __init__(self, frames: list[Image.Image]) -> None:
        self.frames = frames
        self.grabs = 0
        self.failures = 0

    def grab(self):
        from lintranslator.capture import Frame

        image = self.frames[min(self.grabs, len(self.frames) - 1)]
        self.grabs += 1
        return Frame(image=image, full_size=image.size, region=(0, 0, *image.size), elapsed=0.001)

    def close(self):
        pass

    @property
    def stats(self):
        return {"grabs": self.grabs}


def _pipeline(texts: list[str], frames: list[Image.Image], **detect_overrides) -> Pipeline:
    cfg = Config()
    cfg.detect.settle_max_wait = 1.0
    cfg.translate.backend = "none"
    for key, value in detect_overrides.items():
        setattr(cfg.detect, key, value)

    events = []
    pipe = Pipeline(cfg, on_event=events.append)
    pipe.grabber = StubGrabber(frames)
    pipe.ocr = StubOcr(texts)
    pipe.translator = _StubTranslator()
    pipe.warmup = lambda: None  # type: ignore[method-assign]
    pipe.start()
    pipe.events = events  # type: ignore[attr-defined]
    return pipe


class _StubTranslator:
    """Records what was translated; avoids loading a real model."""

    name = "stub"

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.cache = _NullCache()
        self.last_was_cached = False

    def translate(self, text: str, force: bool = False):
        from lintranslator.translate import Translation

        self.seen.append(text)
        return Translation(target=f"[ja] {text}", source=text, backend="stub", elapsed=0.001)

    def warmup(self):
        pass

    def close(self):
        pass


class _NullCache:
    stats = {"entries": 0, "hits": 0, "misses": 0}


# --------------------------------------------------------------------------- #
def test_translates_once_when_the_screen_goes_static():
    """The regression case: text appears, the screen then stops changing, and the
    line must still be translated via the time-based release."""
    frame = _frame("The reactor is overheating")
    pipe = _pipeline(["The reactor is overheating"] * 4, [frame])

    now = 0.0
    emitted = []
    # Poll until the release fires or we run out of patience.
    for _ in range(12):
        # step() paces itself with monotonic time, so drive it with real sleeps
        event = pipe.step()
        if event:
            emitted.append(event)
            break
        import time

        time.sleep(0.25)

    assert emitted, "static text was never translated"
    assert emitted[0].source == "The reactor is overheating"
    assert emitted[0].target.startswith("[ja]")
    pipe.close()


def test_no_translation_while_text_keeps_changing():
    """A typewriter reveal must not produce a translation per character."""
    frames = [_frame(f"The react{'x' * i}") for i in range(6)]
    texts = [f"The react{'x' * i}" for i in range(6)]
    pipe = _pipeline(texts, frames)
    emitted = []
    for _ in range(5):
        event = pipe.step()
        if event:
            emitted.append(event)
        import time

        time.sleep(0.12)
    assert emitted == [], "mid-reveal text should not be translated"
    pipe.close()


def test_identical_static_frames_do_not_re_ocr():
    """OCR is the expensive stage; a frozen screen must not pay for it."""
    frame = _frame("static text")
    pipe = _pipeline(["static text"] * 10, [frame])
    for _ in range(6):
        pipe.step()
        import time

        time.sleep(0.05)
    assert pipe.ocr.calls <= 2, f"OCR ran {pipe.ocr.calls} times on a static frame"
    pipe.close()


def test_error_in_one_stage_does_not_kill_the_loop():
    """A failing grab must be reported, not crash the run."""
    pipe = _pipeline(["text"], [_frame("text")])
    errors = []
    pipe.on_error = errors.append  # type: ignore[attr-defined]

    def boom():
        raise RuntimeError("portal exploded")

    pipe.grabber.grab = boom  # type: ignore[method-assign]
    assert pipe.step() is None  # no exception escapes
    assert errors, "the failure was swallowed without reporting"
    assert "portal exploded" in str(errors[0])
    assert pipe.stats.errors == 1
    pipe.close()


# --------------------------------------------------------------------------- #
# Change-driven emission: one translation per new line
# --------------------------------------------------------------------------- #
class _ScriptedScreen:
    """Feeds a fixed sequence of OCR readings, one per grab.

    Decoupled from timing on purpose. An earlier version derived the reading
    from a frame counter and blinked a cursor, which meant the OCR text depended
    on how many polls had happened - so the test measured the fixture's timing
    rather than the pipeline's behaviour.
    """

    def __init__(self, readings: list[str], blinking: bool = False) -> None:
        self.readings = readings
        self.blinking = blinking
        self.i = -1

    def grab(self):
        from PIL import Image, ImageDraw

        from lintranslator.capture import Frame

        self.i = min(self.i + 1, len(self.readings) - 1)
        img = Image.new("RGB", (200, 40), (40, 40, 40))
        if self.blinking:
            # A blinking advance cursor: the frame changes on every poll even
            # though the text does not, which is what a live game does.
            ImageDraw.Draw(img).rectangle(
                [180, 8, 188, 32], fill=(255, 255, 255) if self.i % 2 else (0, 0, 0)
            )
        elif self.i and self.readings[self.i] != self.readings[self.i - 1]:
            ImageDraw.Draw(img).rectangle([0, 0, 199, 39], fill=(90, 90, 90))
        return Frame(image=img, full_size=img.size, region=(0, 0, 200, 40), elapsed=0.001)

    def close(self) -> None:
        pass

    @property
    def stats(self) -> dict:
        return {"grabs": self.i + 1}


class _ScriptedOcr:
    """Returns the current scripted reading."""

    def __init__(self, screen: _ScriptedScreen, confidence: float = 92.0) -> None:
        self.screen = screen
        self.calls = 0
        # Settable per test: how sure OCR is of what it read decides whether a
        # short or thin read is treated as dialogue (see `selftext.noise_reason`).
        self.confidence = confidence

    def ensure_ready(self):
        return None

    def read(self, image):
        from lintranslator.ocr import OcrLine, OcrResult

        self.calls += 1
        text = self.screen.readings[max(0, self.screen.i)]
        lines = [OcrLine(text, self.confidence, (0, 0, 10, 10))] if text else []
        return OcrResult(lines=lines, elapsed=0.001, engine="s", lang="eng")


def _pipeline_for(readings: list[str], blinking: bool = False, **overrides):
    """A real Pipeline driven by scripted readings on a virtual clock."""
    cfg = Config()
    cfg.translate.backend = "none"
    cfg.capture.fps = 2.0            # 0.5s per poll
    cfg.detect.settle_window = 1.0
    cfg.detect.settle_max_wait = 1.0
    cfg.detect.refresh_interval = 0.9
    for key, value in overrides.items():
        setattr(cfg.detect, key, value)

    screen = _ScriptedScreen(readings, blinking=blinking)
    events = []
    pipe = Pipeline(cfg, on_event=events.append)
    pipe.grabber = screen
    pipe.ocr = _ScriptedOcr(screen)
    pipe.translator = _StubTranslator()
    pipe.warmup = lambda: None
    pipe.start(0.0)
    return pipe, screen, events


def _drive(pipe, screen, events, ticks: int | None = None, step: float = 0.5):
    """Advance the virtual clock, polling until the script is exhausted.

    `ticks` extra polls run after the last reading so the settle window can
    elapse on a now-static screen, which is how a real line gets confirmed.
    """
    total = len(screen.readings) + (ticks if ticks is not None else 8)
    now = 0.0
    for _ in range(total):
        now += step
        pipe.step(now)
    return now


# --------------------------------------------------------------------------- #
def test_each_new_line_is_translated_exactly_once():
    """Regression: every line used to be emitted twice.

    The tick path released the held text without recording it, so the same line
    was re-observed and re-emitted a moment later.
    """
    readings = ["AAAA first line."] * 3 + ["BBBB second line."] * 3 + ["CCCC third line."] * 3
    pipe, screen, events = _pipeline_for(readings)
    _drive(pipe, screen, events)
    got = [e.source for e in events]
    assert got == ["AAAA first line.", "BBBB second line.", "CCCC third line."], got


def test_jittering_reads_do_not_duplicate_a_translation():
    """Regression: OCR reading the same line as 'x' then 'x l' (a cursor
    artefact) must not count as a new line. Exact-match dedupe failed this."""
    from lintranslator.detect import is_same_reading

    assert is_same_reading(
        "record pertaining to today's request.", "record pertaining to today's request. l"
    )
    assert not is_same_reading("AAAA first line.", "BBBB second line.")

    # The same line, jittering every poll, then a genuinely new line. The stub
    # OCR stands in for the real one, which strips the caret glyph before the
    # pipeline ever sees it (see `strip_trailing_cursor`), so the reads below are
    # what a scripted screen would actually deliver.
    readings = ["AAAA first line.", "AAAA first line. l"] * 3 + ["BBBB second line."] * 4
    readings = [strip_trailing_cursor(r) for r in readings]
    pipe, screen, events = _pipeline_for(readings)
    _drive(pipe, screen, events)
    assert [e.source for e in events] == ["AAAA first line.", "BBBB second line."], [
        e.source for e in events
    ]


def test_the_blinking_caret_is_not_part_of_the_line():
    """Live reads of one unchanged line ended in " l", " +", " O", " |" - the
    advance caret, not text. It polluted the translation and made consecutive
    reads look like different lines.

    The rule is deliberately narrow: stripping any lone trailing character broke
    real lines during live testing ("needed maintenance." lost its period for a
    moment when the caret was read as ".").
    """
    for raw, clean in [
        ("AAAA first line. l", "AAAA first line."),
        ("See you around. +", "See you around."),
        ("Got it. |", "Got it."),
        ("Hm, odd. O", "Hm, odd."),
        ("Wait for me", "Wait for me"),          # a word, not a caret
        ("Was it you? I", "Was it you? I"),      # pronoun I must survive
        ("don't let anyb", "don't let anyb"),    # a reveal fragment, not a caret
        ("Ready?", "Ready?"),                    # real sentence punctuation
        ('He said, "no."', 'He said, "no."'),    # closing quote must survive
        ("1 2 3", "1 2 3"),                      # a real trailing number
        ("Gregor But because | was sponsored", "Gregor But because | was sponsored"),
        # The caret is drawn next to the finished sentence, so it often arrives
        # AFTER the punctuation - and was then sent to the translator as text.
        (
            "their flames dominated the entire battlefield. f",
            "their flames dominated the entire battlefield.",
        ),
        ("a growing rupture in the formation... é", "a growing rupture in the formation..."),
        ("Was it you? I", "Was it you? I"),      # pronoun after a question mark
        ("Yes. It is", "Yes. It is"),            # real two-word ending
    ]:
        assert strip_trailing_cursor(raw) == clean, raw


def test_a_pause_mid_reveal_does_not_emit_a_fragment():
    """Regression from the panel: the game reveals a line, pauses mid-sentence,
    then finishes. The pause is not the end of the line, so the fragment must not
    be translated - and the finished line must still arrive exactly once.

    The overrides match production (`config.json`); the shared fixture defaults
    (1s windows) are deliberately tighter than the real ones.
    """
    partial = "The inspector is the proverbial poster child of company"
    full = "The inspector is the proverbial poster child of company-sponsored contractors."
    # The partial sits on screen for 3s (6 polls) before the rest appears.
    readings = [partial] * 6 + [full] * 6
    pipe, screen, events = _pipeline_for(
        readings, settle_window=1.2, settle_max_wait=8.0, incomplete_grace=4.5
    )
    _drive(pipe, screen, events)

    got = [e.source for e in events]
    assert got == [full], f"expected only the finished line, got {got}"


def test_a_finished_line_is_not_delayed_by_the_reveal_grace():
    """The unfinished-text grace must not cost normal dialogue its timing."""
    pipe, screen, events = _pipeline_for(["The reactor is overheating."] * 4)
    _drive(pipe, screen, events)
    assert [e.source for e in events] == ["The reactor is overheating."]


def test_static_screen_produces_no_repeat_translations():
    """A line sitting on screen must not be retranslated every settle window."""
    pipe, screen, events = _pipeline_for(["AAAA only line."] * 25)
    _drive(pipe, screen, events)
    assert len(events) == 1, [e.source for e in events]


def test_ocr_is_skipped_when_nothing_changes():
    """Change detection must gate OCR, or an idle screen burns CPU."""
    readings = ["AAAA line."] * 12 + ["BBBB line."] * 12
    pipe, screen, events = _pipeline_for(readings)
    _drive(pipe, screen, events)
    assert pipe.ocr.calls < len(readings) // 2, (
        f"OCR ran {pipe.ocr.calls} times over {len(readings)} polls; the change "
        "detector is not gating it"
    )


def test_blinking_screen_still_translates_and_does_not_stall():
    """A blinking cursor keeps pixels changing, so the screen never settles.

    This is the "nothing ever translates" bug reported from a live screen: every
    poll looks like a change, so the text is re-read constantly. It must still
    produce exactly one translation and must not re-read on every single poll.
    """
    readings = ["AAAA held line."] * 24
    # 10fps so the poll gap (0.1s) is shorter than the throttle (0.25s); at 2fps
    # the gap already exceeds it and the throttle would never be exercised.
    pipe, screen, events = _pipeline_for(
        readings, blinking=True, ocr_min_interval=0.25
    )
    pipe.config.capture.fps = 10.0
    pipe._min_interval = 0.1
    _drive(pipe, screen, events, step=0.1)
    assert [e.source for e in events] == ["AAAA held line."], [e.source for e in events]
    assert pipe.stats.ocr_runs < len(readings), (
        f"OCR ran {pipe.stats.ocr_runs} times over {len(readings)} polls"
    )


def test_ocr_throttle_limits_re_reads_on_a_blinking_screen():
    """Checked directly with explicit times: a fast poll loop over a screen that
    changes every poll must not run OCR every poll."""
    pipe, screen, events = _pipeline_for(["AAAA line."] * 4, blinking=True)
    pipe._ocr_min_interval = 0.25
    pipe._last_ocr_at = 10.0
    assert pipe._ocr_throttled(10.1) is True   # 0.1s later - too soon
    assert pipe._ocr_throttled(10.25) is False  # exactly at the interval
    assert pipe._ocr_throttled(11.0) is False   # well past it
    # Disabling the throttle restores one OCR per change.
    pipe._ocr_min_interval = 0.0
    assert pipe._ocr_throttled(10.0001) is False


def test_timed_refresh_re_reads_a_held_line():
    """The refresh mechanism must actually fire while a line is held.

    Verified directly rather than through an end-to-end emission, so it cannot
    pass for the wrong reason (e.g. the tick releasing the line instead).
    """
    readings = ["AAAA held line."] * 12
    pipe, screen, events = _pipeline_for(readings, blinking=True)
    # Hold the line explicitly and ask whether a re-read is due.
    pipe.settler._held_text = "AAAA held line."
    pipe._last_ocr_at = 0.0
    pipe._last_settled_text = None
    assert pipe._refresh_due(now=5.0) is True, "refresh never becomes due"
    assert pipe._refresh_due(now=5.0) is not None
    # And it must not re-read something already translated.
    pipe._last_settled_text = "AAAA held line."
    assert pipe._refresh_due(now=99.0) is False


# --------------------------------------------------------------------------- #
# Capture timing: never read the screen while our own windows are on it
# --------------------------------------------------------------------------- #
def test_our_own_window_on_screen_stops_capture_entirely():
    """The measured regression.

    The first capture after Start happened 10 ms after the click, with the
    picker still over the box. It read the picker's own status line -
    "captured 2560x1440 — drag over the dialogue text" - at 93% confidence, while
    the dialogue line the picker covered fell to 54.7% and was dropped by
    `ocr.min_confidence`. A real line, lost to our own window.

    So while a lintranslator window is on screen, nothing is captured at all.
    """
    pipe, screen, events = _pipeline_for(["AAAA a line."] * 6)
    reasons = []
    pipe.on_gate = reasons.append
    pipe.gate = lambda: "the region picker is on screen"

    _drive(pipe, screen, events)

    assert screen.i == -1, "captured the screen while our own window was on it"
    assert events == []
    assert pipe.stats.paused_polls > 0
    assert pipe.last_decision == "paused"
    assert reasons == ["the region picker is on screen"], reasons
    pipe.close()


def test_reading_starts_the_moment_the_window_is_gone():
    """No fixed delay: the pause lifts as soon as the window unmaps.

    The picker minimises itself when watching starts, and this is what makes that
    enough - there is no sleep long enough to be safe and short enough to feel
    instant, so the pipeline waits for the actual condition instead.
    """
    reason = {"value": "the region picker is on screen"}
    pipe, screen, events = _pipeline_for(["AAAA a line."] * 12)
    pipe.gate = lambda: reason["value"]
    gates = []
    pipe.on_gate = gates.append

    now = 0.0
    for _ in range(4):
        now += 0.5
        pipe.step(now)
    assert screen.i == -1

    reason["value"] = None  # the picker unmapped
    now += 0.5
    pipe.step(now)
    assert screen.i == 0, "reading did not start as soon as the window was gone"
    assert gates == ["the region picker is on screen", ""], gates
    pipe.close()


def test_a_pause_does_not_release_the_text_it_was_holding():
    """Held text must not survive a pause.

    While paused, time passes with no evidence about the screen, so a release
    timer that kept running would emit a line that is already gone - or release a
    paused reveal's fragment the instant reading resumed.
    """
    partial = "The inspector is the proverbial poster child of company"
    pipe, screen, events = _pipeline_for([partial] * 20)
    gate = {"value": None}
    pipe.gate = lambda: gate["value"]

    now = 0.0
    for _ in range(2):  # a line is read and held
        now += 0.5
        pipe.step(now)
    assert pipe.settler.held, "nothing was held to begin with"

    gate["value"] = "the settings window is on screen"
    for _ in range(20):  # far longer than any settle window
        now += 0.5
        pipe.step(now)
    assert events == [], "a line was emitted while reading was paused"

    gate["value"] = None
    now += 0.5
    assert pipe.step(now) is None, "held text was released the instant reading resumed"
    assert events == []
    pipe.close()


def test_the_region_can_be_changed_while_the_loop_runs():
    """A new selection re-points the running loop instead of being ignored.

    Measured before this: dragging a new box and pressing Start again left
    the pipeline reading the OLD area while the picker showed the new one as
    live - only Save applied it, and that restarted (and re-positioned) the panel.
    """
    from lintranslator.config import Region

    pipe, screen, events = _pipeline_for(["AAAA a line."] * 6)
    new = Region(0.5, 0.5, 0.25, 0.1, "fraction")
    pipe.request_region(new)

    now = 0.5
    pipe.step(now)

    assert pipe.grabber.region is new, "the grabber still reads the old area"
    assert pipe.config.capture.region is new
    assert screen.i == 0, "the new area was not read on the next poll"
    pipe.close()


def test_a_new_area_is_read_as_a_new_context():
    """The same text in a different box is a new reading.

    Otherwise re-pointing the loop at another part of the screen would silently
    skip whatever is already there, because it looks like the line just done.
    """
    from lintranslator.config import Region

    pipe, screen, events = _pipeline_for(["AAAA a line."] * 24)
    now = _drive(pipe, screen, events, ticks=16)
    assert [e.source for e in events] == ["AAAA a line."], [e.source for e in events]

    pipe.request_region(Region(0.4, 0.4, 0.2, 0.1, "fraction"))
    for _ in range(10):
        now += 0.5
        pipe.step(now)
    assert [e.source for e in events] == ["AAAA a line."] * 2, [e.source for e in events]
    pipe.close()


# --------------------------------------------------------------------------- #
# Self-reads: lintranslator's own UI must never reach the translator
# --------------------------------------------------------------------------- #
def test_our_own_ui_read_is_never_translated():
    """The exact string the live capture produced, gate and all."""
    polluted = (
        "Watcning... captured 2560x1440 — dra without letting them in on all "
        "available information... Ah, | can practically hear the complaints lodged"
    )
    pipe, screen, events = _pipeline_for([polluted] * 6)
    skips = []
    pipe.on_self_read = skips.append

    decisions = []
    now = 0.0
    for _ in range(len(screen.readings) + 8):
        now += 0.5
        pipe.step(now)
        decisions.append(pipe.last_decision)

    assert events == [], "lintranslator's own window was translated"
    assert pipe.translator.seen == []
    assert pipe.stats.self_reads >= 1
    assert "skip:self-ui" in decisions, decisions
    assert skips and "captured 2560x1440" in skips[0], skips
    pipe.close()


def test_the_panel_showing_its_own_translation_is_not_translated_again():
    """The panel cannot be gated - it has to stay on screen while watching.

    If it sits over the box, its own output is read straight back, and left alone
    that loops: the translation is translated again, forever.
    """
    source = "AAAA the source line."
    echo = "[ja] AAAA the source line."
    pipe, screen, events = _pipeline_for([source] * 4 + [echo] * 8)
    pipe.on_self_read = lambda text: None

    _drive(pipe, screen, events)

    assert [e.source for e in events] == [source], [e.source for e in events]
    assert pipe.stats.self_reads >= 1, "the echo was not recognised"
    pipe.close()


# --------------------------------------------------------------------------- #
# Timing that tells the truth
# --------------------------------------------------------------------------- #
def test_event_timing_is_measured_not_stubbed():
    """`total_elapsed` used to be hardcoded 0.0, so `lintranslator run --json` reported
    "total_ms": 0 for every line, and `capture_ms` did not exist at all."""
    import time as _time

    from lintranslator.capture import Frame

    class _AgedScreen:
        """One frame, captured two seconds ago, costing 250 ms to grab."""

        def __init__(self) -> None:
            self.grabs = 0

        def grab(self):
            self.grabs += 1
            image = _frame("AAAA a line.")
            return Frame(
                image=image,
                full_size=image.size,
                region=(0, 0, *image.size),
                elapsed=0.25,
                timestamp=_time.monotonic() - 2.0,
            )

        def close(self):
            pass

        @property
        def stats(self):
            return {"grabs": self.grabs}

    cfg = Config()
    cfg.translate.backend = "none"
    cfg.detect.settle_window = 0.5
    cfg.detect.settle_max_wait = 0.5
    events = []
    pipe = Pipeline(cfg, on_event=events.append)
    pipe.grabber = _AgedScreen()
    pipe.ocr = StubOcr(["AAAA a line."] * 6)
    pipe.translator = _StubTranslator()
    pipe.warmup = lambda: None
    pipe.start(0.0)
    now = 0.0
    for _ in range(8):
        now += 0.5
        pipe.step(now)

    assert events, "no translation to time"
    event = events[0]
    assert event.capture_elapsed == 0.25, "grab cost was not carried onto the event"
    assert 2.0 <= event.total_elapsed < 3.0, event.total_elapsed
    payload = event.as_dict()
    assert payload["capture_ms"] == 250
    assert payload["total_ms"] == round(event.total_elapsed * 1000) > 0
    pipe.close()


def test_a_mixed_read_keeps_the_game_line():
    """Regression: our window clipping the box must not cost the dialogue line.

    The panel is the one window that cannot be gated, so when it overlaps the box
    the read contains both its text and the game's. Dropping the whole read - the
    first version of this guard - is what "it stopped detecting" looked like:
    the panel's own status line kept changing, every read was discarded, and the
    dialogue line underneath never settled.
    """
    from lintranslator.ocr import OcrLine, OcrResult

    class MixedOcr:
        """Two lines: one of ours, one from the game."""

        def __init__(self) -> None:
            self.calls = 0

        def ensure_ready(self):
            return None

        def read(self, image) -> OcrResult:
            self.calls += 1
            lines = [
                OcrLine("12:34:56 · conf 90 · 812 ms · openrouter", 92.0, (0, 0, 200, 12)),
                OcrLine(
                    "Although the trial won't be open to the public, you may, as close",
                    93.0,
                    (0, 14, 400, 14),
                ),
            ]
            return OcrResult(lines=lines, elapsed=0.001, engine="stub", lang="eng")

    class _ChangingScreen:
        """A screen that really changes, so OCR keeps being asked to run."""

        def __init__(self) -> None:
            self.grabs = 0

        def grab(self):
            from lintranslator.capture import Frame

            self.grabs += 1
            image = _frame(f"frame {self.grabs}")
            return Frame(
                image=image,
                full_size=image.size,
                region=(0, 0, *image.size),
                elapsed=0.001,
            )

        def close(self):
            pass

        @property
        def stats(self):
            return {"grabs": self.grabs}

    cfg = Config()
    cfg.translate.backend = "none"
    cfg.capture.fps = 2.0
    cfg.detect.settle_window = 0.5
    cfg.detect.settle_max_wait = 0.5
    events = []
    pipe = Pipeline(cfg, on_event=events.append)
    pipe.grabber = _ChangingScreen()
    pipe.ocr = MixedOcr()
    pipe.translator = _StubTranslator()
    pipe.warmup = lambda: None
    pipe.start(0.0)

    now = 0.0
    for _ in range(8):
        now += 0.5
        pipe.step(now)

    assert events, "the dialogue line under our own UI was never translated"
    assert events[0].source.startswith("Although the trial"), events[0].source
    assert all("conf 90" not in e.source for e in events)
    assert pipe.stats.self_reads >= 1, "our own line was not recognised"
    pipe.close()


def test_a_shorter_line_after_a_long_one_is_still_translated():
    """The full-loop version of the settler regression.

    A long line appears and is replaced, before it ever settled, by a shorter
    one. The shorter line has to be adopted and translated - the pipeline used to
    hold the longer text forever and go silent.
    """
    long_line = (
        "For lack of any meaningful help they could provide; given the nature of this trial."
    )
    short_line = "Yes, of course."
    readings = [long_line] * 1 + [short_line] * 12
    pipe, screen, events = _pipeline_for(readings)
    _drive(pipe, screen, events)

    got = [e.source for e in events]
    assert short_line in got, f"the shorter line was never translated: {got}"
    assert long_line not in got, f"the never-settled long line was translated: {got}"
    pipe.close()


# --------------------------------------------------------------------------- #
# Re-read: the button, the in-app shortcut and the global hotkey all land here
# --------------------------------------------------------------------------- #
def test_reread_reads_the_same_line_again():
    """A re-read must do what a person means by "do that again".

    Change detection, the settle window and the "already translated" check all
    exist to suppress repeats; a re-read is the user asking for one, so it skips
    all of them and captures immediately rather than at the next poll.
    """
    readings = ["AAAA only line."] * 20
    pipe, screen, events = _pipeline_for(readings)
    now = _drive(pipe, screen, events, ticks=12)
    assert [e.source for e in events] == ["AAAA only line."]

    reads_before = pipe.ocr.calls
    pipe.request_reread()
    now += 0.5
    event = pipe.step(now)

    assert event is not None, "the re-read produced nothing"
    assert event.source == "AAAA only line."
    assert pipe.ocr.calls > reads_before, "the box was not read again"
    assert pipe.last_decision == "ocr:reread"
    assert [e.source for e in events] == ["AAAA only line."] * 2
    assert pipe.stats.translations == 2
    pipe.close()


def test_reread_ignores_the_ocr_throttle_and_the_pacing():
    """Pressed at a moment the loop would otherwise skip, it still acts now."""
    pipe, screen, events = _pipeline_for(["AAAA a line."] * 12, blinking=True)
    _drive(pipe, screen, events, ticks=6)
    # A poll that would be early, throttled or unchanged.
    pipe._next_poll = 999.0
    pipe._last_ocr_at = 999.0
    pipe.request_reread()
    event = pipe.step(1.0)
    assert event is not None, "the re-read was skipped by pacing or the throttle"
    assert pipe.last_decision == "ocr:reread"
    pipe.close()


def test_a_reread_while_reading_is_paused_is_not_lost():
    """The hotkey is most likely to be pressed while the picker is up."""
    reason = {"value": "the region picker is on screen"}
    pipe, screen, events = _pipeline_for(["AAAA a line."] * 12)
    pipe.gate = lambda: reason["value"]
    notes = []
    pipe.on_note = notes.append

    pipe.request_reread()
    now = 0.5
    pipe.step(now)
    assert screen.i == -1, "captured while our own window was on screen"
    assert notes == ["re-read queued — reading is paused"], notes

    reason["value"] = None  # the picker went away
    now += 0.5
    event = pipe.step(now)
    assert event is not None, "the queued re-read never happened"
    assert pipe.last_decision == "ocr:reread"
    assert event.source == "AAAA a line."
    pipe.close()


def test_a_reread_of_an_empty_box_says_so():
    """Nothing to translate is a real answer, not silence."""
    pipe, screen, events = _pipeline_for([""] * 8)
    notes = []
    pipe.on_note = notes.append
    pipe.request_reread()
    pipe.step(0.5)
    assert events == []
    assert pipe.last_decision == "skip:no-text"
    assert any("nothing readable" in note for note in notes), notes
    pipe.close()


def test_a_read_back_translation_is_reported_as_such():
    """The echo case is not "our window is in the box" - the text matched, which
    is a different (and more specific) diagnosis for the user."""
    source = "AAAA the source line."
    echo = "[ja] AAAA the source line."
    pipe, screen, events = _pipeline_for([source] * 4 + [echo] * 8)
    notes = []
    pipe.on_note = notes.append

    _drive(pipe, screen, events)

    assert [e.source for e in events] == [source]
    assert any("panel's own translation" in note for note in notes), notes
    assert pipe.stats.self_reads >= 1
    pipe.close()


def test_confident_nonsense_is_not_translated():
    """Background art reads as a glyph or two at 60-80% confidence - above the OCR
    gate. Translating "e¢" is worse than saying nothing, so the read is dropped
    and the reason is shown."""
    pipe, screen, events = _pipeline_for(["e¢ ¢"] * 8)
    pipe.ocr.confidence = 62.5
    notes = []
    pipe.on_note = notes.append
    _drive(pipe, screen, events)

    assert events == [], [e.source for e in events]
    assert pipe.stats.junk_reads >= 1
    assert any("not enough text" in note for note in notes), notes
    pipe.close()


def test_a_clean_short_line_still_gets_translated():
    """The rail must not cost a real short line: "Yes." in a clean font is high
    confidence, so it passes."""
    pipe, screen, events = _pipeline_for(["Yes."] * 8)
    pipe.ocr.confidence = 93.0
    _drive(pipe, screen, events)

    assert [e.source for e in events] == ["Yes."], [e.source for e in events]
    assert pipe.stats.junk_reads == 0
    pipe.close()
