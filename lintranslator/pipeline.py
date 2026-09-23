"""The polling loop that wires capture -> detect -> OCR -> translate -> emit.

Design notes:

* The poll rate and the work rate are decoupled. `fps` controls how often we
  sample the screen; OCR only runs when the pixels actually changed, and
  translation only runs when the OCR text has settled. At 2 fps this loop is
  near-idle while dialogue is static.
* The loop refuses to read the screen while one of lintranslator's own windows is on it
  (`gate`). Capturing then translates our own UI and loses the line underneath it;
  see `lintranslator.occlusion` for the measurement.
* The area being read can be changed while the loop runs (`request_region`), and
  the change is applied on the worker thread so the region, the change detector
  and the settler can never disagree about which area they are looking at.
* Every stage is individually measurable, and `Pipeline.stats()` reports the
  breakdown so a slow stage is obvious rather than guessed at.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .capture import Frame, ScreenGrabber
from .config import Config, Region
from .detect import (
    ChangeDetector,
    EmptyGuard,
    TextSettler,
    is_same_reading,
    similarity,
)
from .glossary import LIMBUS_GLOSSARY, Glossary
from .ocr import OcrResult, TesseractOcr
from .selftext import looks_like_own_ui, noise_reason
from .translate import CachedTranslator, Translation, build_translator

EventCallback = Callable[["Event"], None]
# Returns why capturing must wait, or None when it is safe.
GateCallback = Callable[[], "str | None"]
# Called with the reason when reading pauses, and with "" when it resumes.
GateNotify = Callable[[str], None]


@dataclass
class Event:
    """One settled, translated line of dialogue."""

    source: str
    target: str
    confidence: float
    translate_elapsed: float
    # Frame captured -> translation ready: how stale the translation is. Includes
    # the settle window, so it is the number a user actually experiences.
    total_elapsed: float
    cached: bool
    backend: str
    # Cost of the grab this line came from (portal + decode + crop).
    capture_elapsed: float = 0.0
    # Source with the game's original line breaks, for display in the panel.
    display_source: str = ""
    at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {
            "type": "translation",
            "at": self.at,
            "source": self.source,
            "display_source": self.display_source,
            "target": self.target,
            "confidence": round(self.confidence, 1),
            "cached": self.cached,
            "backend": self.backend,
            "capture_ms": round(self.capture_elapsed * 1000),
            "translate_ms": round(self.translate_elapsed * 1000),
            "total_ms": round(self.total_elapsed * 1000),
        }


@dataclass
class Stats:
    polls: int = 0
    changed: int = 0
    ocr_runs: int = 0
    translations: int = 0
    empty_reads: int = 0
    errors: int = 0
    ocr_seconds: float = 0.0
    translate_seconds: float = 0.0
    refreshed_reads: int = 0
    throttled: int = 0
    # Polls skipped because one of our own windows was on screen.
    paused_polls: int = 0
    # Reads dropped because they were lintranslator's own UI (or its own translation).
    self_reads: int = 0
    # Reads dropped as not-dialogue (too little text, or short and unsure).
    junk_reads: int = 0

    @property
    def ocr_avg_ms(self) -> float:
        return 1000 * self.ocr_seconds / self.ocr_runs if self.ocr_runs else 0.0

    @property
    def translate_avg_ms(self) -> float:
        return 1000 * self.translate_seconds / self.translations if self.translations else 0.0

    def as_dict(self) -> dict:
        return {
            "polls": self.polls,
            "changed": self.changed,
            "ocr_runs": self.ocr_runs,
            "translations": self.translations,
            "empty_reads": self.empty_reads,
            "refreshed_reads": self.refreshed_reads,
            "throttled": self.throttled,
            "paused_polls": self.paused_polls,
            "self_reads": self.self_reads,
            "junk_reads": self.junk_reads,
            "errors": self.errors,
            "ocr_avg_ms": round(self.ocr_avg_ms, 1),
            "translate_avg_ms": round(self.translate_avg_ms, 1),
        }


class Pipeline:
    """Stateful capture/translate loop. Call `start()`, then `step()` in a loop."""

    def __init__(
        self,
        config: Config,
        on_event: EventCallback | None = None,
        on_ocr: Callable[[OcrResult], None] | None = None,
        gate: GateCallback | None = None,
        on_gate: GateNotify | None = None,
        on_self_read: Callable[[str], None] | None = None,
        on_note: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.on_event = on_event
        self.on_ocr = on_ocr
        # Why capturing must wait right now, or None. Supplied by the GUI, which
        # knows which of its own windows are on screen.
        self.gate = gate
        self.on_gate = on_gate
        # Called when a read is dropped for being lintranslator's own text, so the UI can
        # say so instead of silently showing nothing.
        self.on_self_read = on_self_read
        # Called with a short message the user asked to be told about (a re-read
        # that found nothing, a re-read queued while paused).
        self.on_note = on_note

        self.grabber = ScreenGrabber(config.capture.region)
        self.ocr = TesseractOcr(
            langs=config.ocr.langs,
            psm=config.ocr.psm,
            upscale=config.ocr.upscale,
            autocontrast=config.ocr.autocontrast,
            tessdata_dir=config.ocr.tessdata_dir,
            min_confidence=config.ocr.min_confidence,
            allow_unverified_tessdata=config.ocr.allow_unverified_tessdata,
        )
        self.detector = ChangeDetector(
            min_changed_fraction=config.detect.min_changed_fraction,
            stride=4,
        )
        self.settler = TextSettler(
            settle_frames=config.detect.settle_frames,
            settle_max_wait=config.detect.settle_max_wait,
            settle_window=config.detect.settle_window,
            incomplete_grace=config.detect.incomplete_grace,
        )
        self.empty_guard = EmptyGuard(limit=config.detect.empty_streak_limit)
        self.translator = CachedTranslator(
            build_translator(config.translate),
            cache=_cache_for(config),
            glossary=_glossary_for(config),
        )
        self.stats = Stats()
        self._min_interval = 1.0 / max(0.1, config.capture.fps)
        self._refresh_interval = config.detect.refresh_interval
        self._ocr_min_interval = config.detect.ocr_min_interval
        self._last_frame: Frame | None = None
        self._last_ocr_at = 0.0
        self._next_poll = 0.0
        self._last_settled_text: str | None = None
        self._last_display: str | None = None
        self._last_confidence: float = 0.0
        # What the last translation said, so the panel reading itself back can be
        # recognised instead of translated again.
        self._last_target_text: str = ""
        # When the frame behind the held text was captured, and what it cost.
        self._frame_at: float = 0.0
        self._frame_capture_elapsed: float = 0.0
        # Set by `request_region` from the GUI thread; applied by `step`.
        self._pending_region: Region | None = None
        self._gate_reason: str | None = None
        # Set by `request_reread` (button or hotkey) from the GUI thread.
        self._reread_requested = False

    # -- lifecycle --------------------------------------------------------- #
    def warmup(self) -> None:
        """Validate OCR data and load the translation model before the loop."""
        self.ocr.ensure_ready()
        self.translator.warmup()

    def start(self, now: float | None = None) -> None:
        """Begin the loop. `now` lets a caller supply its own clock.

        The whole pipeline - pacing, settling and the refresh interval - then
        runs on one consistent time base, which is what makes the timing logic
        testable without sleeping.
        """
        self._next_poll = time.monotonic() if now is None else now

    def close(self) -> None:
        self.translator.close()
        self.grabber.close()

    def __enter__(self) -> "Pipeline":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- one iteration ----------------------------------------------------- #
    def step(self, now: float | None = None) -> Event | None:
        """Run a single poll. Returns an Event when a new line is translated."""
        now = now if now is not None else time.monotonic()
        self._decision = ""

        # A region change arrives from the GUI thread; applying it here keeps all
        # pipeline state on one thread, and the new area is read on this poll
        # rather than after the next sleep.
        if self._pending_region is not None:
            self._apply_pending_region(now)

        # A re-read was asked for (button or hotkey): act now instead of at the
        # next scheduled poll. The request is only consumed once the screen is
        # actually read, so pressing it while reading is paused still works.
        forced = self._reread_requested
        if forced:
            self._next_poll = min(self._next_poll, now)

        # Light pacing: if we're early, report how long the caller should sleep.
        if now < self._next_poll:
            self._decision = "early"
            return None
        # Pacing is advanced even for a paused poll, or the loop would spin.
        self._next_poll = now + self._min_interval
        self.stats.polls += 1

        # Never read the screen while one of our own windows is on it. Measured
        # on a live screen: the first capture after "Watch live" contained the
        # picker's status line at 93% confidence while the dialogue under it fell
        # below the confidence gate and was dropped.
        reason = self.gate() if self.gate is not None else None
        if reason:
            self.stats.paused_polls += 1
            self._decision = "paused"
            if reason != self._gate_reason:
                self._enter_gate(reason)
            if forced:
                self._note("re-read queued — reading is paused")
            return None
        if self._gate_reason is not None:
            self._leave_gate()

        # A line that appeared and then stopped changing must still be released:
        # OCR only runs on pixel changes, so without this the last line before a
        # scene goes static would never be translated.
        if self.settler.tick(now):
            held = self.settler.held
            if held and not self._already_translated(held):
                self._decision = "emit:tick-timeout"
                return self._emit(held, self._last_confidence, now, self._last_display or "")
            # Nothing new to translate, but the released line must still be
            # recorded. Clearing without recording let the very same text be
            # re-observed and re-emitted a moment later, so every line was
            # translated twice.
            if held:
                self._last_settled_text = held
                self._decision = "skip:already-translated"
            self.settler.reset()

        try:
            frame = self.grabber.grab()
        except Exception as exc:  # noqa: BLE001 - a failed grab must not kill the loop
            self.stats.errors += 1
            self.grabber.failures += 1
            self._decision = f"error:{type(exc).__name__}"
            self._on_error(exc)
            return None

        # Remember when these pixels were taken and what they cost: the age of
        # the frame is what makes `Event.total_elapsed` mean something.
        self._last_frame = frame
        self._frame_at = frame.timestamp
        self._frame_capture_elapsed = frame.elapsed

        if forced:
            # Asked for explicitly: read what is on screen now, whatever the
            # change detector, the throttle or the settle timer think about it.
            self._reread_requested = False
            self.detector.update(frame.image)  # keep the signature in step
            self._decision = "ocr:reread"
            return self._process(frame, now, refreshed=True, force=True)

        if not self.detector.update(frame.image):
            # Pixels are unchanged, but the held text still needs confirmation.
            # Re-read the *same* frame on a timer rather than waiting for the
            # screen to go still, which a live game may never do.
            if self._refresh_due(now):
                self._decision = "ocr:refresh"
                return self._process(frame, now, refreshed=True)
            self._decision = "skip:no-pixel-change"
            return None

        self.stats.changed += 1
        # A blinking advance cursor makes every poll look like a change, which
        # would run OCR continuously for no benefit - the text is not changing.
        # Throttling costs at most `ocr_min_interval` of latency and is bounded
        # well under the settle window, so translation still follows promptly.
        if self._ocr_throttled(now):
            self.stats.throttled += 1
            self._decision = "skip:throttled"
            return None
        self._decision = "ocr:changed"
        return self._process(frame, now)

    # -- pausing, and moving the area being read ---------------------------- #
    def _enter_gate(self, reason: str) -> None:
        """Stop reading until `reason` goes away, and forget held text.

        The held text has to go: while paused, time passes with no evidence about
        the screen, so a release timer that kept running would emit a line that is
        no longer there - or release a paused reveal's fragment the instant
        reading resumes. The detector is reset for the same reason, so the first
        frame after the pause is actually looked at.
        """
        self._gate_reason = reason
        self.settler.reset()
        self.detector.reset()
        self._notify_gate(reason)

    def _leave_gate(self) -> None:
        self._gate_reason = None
        self.settler.reset()
        self.detector.reset()
        self._notify_gate("")

    def _notify_gate(self, reason: str) -> None:
        if self.on_gate is None:
            return
        try:
            self.on_gate(reason)
        except Exception:  # noqa: BLE001 - a UI callback must not kill the loop
            pass

    def request_region(self, region: Region) -> None:
        """Read a different area from the next poll on.

        Safe to call from the GUI thread: the request is applied by `step` on the
        worker thread. Restarting the pipeline instead would reload the model and
        rebuild the panel window, which is both slow and visibly jarring - a
        translation card that jumps to a new position every time the box moves.
        """
        self._pending_region = region

    def request_reread(self) -> None:
        """Read the box again now and translate it, whatever has changed.

        Backs the Re-read button and the hotkey, so it has to do what a person
        means by "do that again": capture now rather than at the next scheduled
        poll, ignore the change detector, the OCR throttle and the settle window,
        ignore the "already translated" check, and skip the translation cache.

        Safe to call from the GUI thread; applied by `step` on the worker thread.
        The request survives a pause (`self.gate`), so pressing the hotkey while
        the picker is on screen still re-reads when reading resumes.
        """
        self._reread_requested = True

    def _apply_pending_region(self, now: float) -> None:
        region = self._pending_region
        self._pending_region = None
        if region is None:
            return
        self.config.capture.region = region
        self.grabber.region = region
        # A different area is a different context: the old pixels, the old OCR
        # text and the old settle timers say nothing about it.
        self.detector.reset()
        self.settler.reset()
        self.empty_guard.reset()
        self._last_settled_text = None
        self._last_display = None
        self._next_poll = min(self._next_poll, now)

    def _ocr_throttled(self, now: float) -> bool:
        if self._ocr_min_interval <= 0:
            return False
        return (now - self._last_ocr_at) < self._ocr_min_interval

    def _refresh_due(self, now: float) -> bool:
        if self._refresh_interval <= 0:
            return False
        if self.settler.held is None:
            return False
        # Never re-read something already translated and still on screen.
        if self.settler.held == self._last_settled_text:
            return False
        return (now - self._last_ocr_at) >= self._refresh_interval

    def sleep_time(self) -> float:
        """Seconds to wait before the next poll is due (wall clock)."""
        return max(0.0, self._next_poll - time.monotonic())

    def sleep_time_at(self, now: float) -> float:
        """Pacing remainder against a supplied clock.

        The main loop uses `sleep_time()`; tests use this so the whole pipeline -
        pacing, settling and the refresh interval - runs on one virtual time base
        instead of mixing injected time with wall-clock reads.
        """
        return max(0.0, self._next_poll - now)

    # -- internals --------------------------------------------------------- #
    def _process(
        self, frame: Frame, now: float, refreshed: bool = False, force: bool = False
    ) -> Event | None:
        try:
            result = self.ocr.read(frame.image)
        except Exception as exc:  # noqa: BLE001
            self.stats.errors += 1
            self._on_error(exc)
            return None

        self.stats.ocr_runs += 1
        self._last_ocr_at = now
        if refreshed:
            self.stats.refreshed_reads += 1
        self.stats.ocr_seconds += result.elapsed
        if self.on_ocr:
            self.on_ocr(result)

        # Our own chrome can be inside the box. Drop those *lines* and keep the
        # game text under them: dropping the whole read loses a real line whenever
        # the panel clips the edge of the box, which is exactly the "it stopped
        # detecting" failure this replaced.
        raw_text = result.text
        stripped = result.without_lines(looks_like_own_ui)
        dropped_own = stripped is not result
        result = stripped
        if dropped_own:
            self.stats.self_reads += 1
            self._note_self_read(raw_text)

        text = result.text
        self._last_confidence = result.confidence
        if not text:
            # Nothing to translate: either the box is empty, or it holds only our
            # own UI - which must never be translated, nor be allowed to settle.
            self.stats.empty_reads += 1
            self.settler.reset()
            self.empty_guard.observe(False)
            self._decision = "skip:self-ui" if dropped_own else "skip:no-text"
            if force:
                self._note(
                    "only lintranslator's own window is in the box"
                    if dropped_own
                    else "nothing readable in the box — check the region"
                )
            return None

        # Confident nonsense: background art and UI chrome read cleanly enough to
        # pass the OCR confidence gate, and translating "e¢" is worse than saying
        # nothing. Said out loud, because a silent skip is indistinguishable from
        # a broken translator.
        junk = noise_reason(
            text, result.confidence, self.config.detect.short_text_confidence
        )
        if junk is not None:
            self.stats.junk_reads += 1
            self._decision = "skip:not-dialogue"
            self.settler.reset()
            self._note(f"skipped {text.strip()[:24]!r} — {junk}")
            return None

        if self._is_echo(text):
            # The panel showing the translation it just produced, read back.
            # Left alone this loops: the translation is translated again. Reported
            # as its own case - "your window is in the box" would be wrong (the
            # text matched, the box may be perfectly clear).
            self.stats.self_reads += 1
            self._decision = "skip:self-echo"
            self.settler.reset()
            self._note("skipped: that is the panel's own translation being read back")
            return None

        if force:
            # Asked for by hand: translate what is on screen now. No settle
            # window (the user is waiting for it) and no duplicate check (they
            # pressed it because they want this line again, from the backend
            # rather than the cache).
            self.empty_guard.observe(True)
            self.settler.reset()
            return self._emit(text, result.confidence, now, result.display_text, force=True)

        if not self.empty_guard.observe(bool(text)):
            self.stats.empty_reads += 1
            self.settler.reset()
            self._decision = "skip:no-text"
            return None

        if not self.settler.observe(text, now):
            return None

        if self._already_translated(text):
            return None
        return self._emit(text, result.confidence, now, result.display_text)

    def _already_translated(self, text: str) -> bool:
        """Whether this line has effectively been emitted already.

        Fuzzy, not equality: OCR reads the same line slightly differently from
        poll to poll (a blinking advance cursor appears as a trailing glyph, for
        example). An exact-match check treats those as new lines and translates
        the same dialogue twice, which is exactly what was observed.
        """
        if self._last_settled_text is None:
            return False
        return is_same_reading(text, self._last_settled_text)

    def _is_echo(self, text: str) -> bool:
        """Whether this read is the panel's own translation, read back.

        The panel cannot be gated - it has to stay on screen while watching - so
        if it covers the box, its output is captured. Left alone that loops: the
        translation is translated again, forever.
        """
        if not text or not self._last_target_text:
            return False
        return is_same_reading(text, self._last_target_text)

    def _note_self_read(self, text: str) -> None:
        if self.on_self_read is None:
            return
        try:
            self.on_self_read(text)
        except Exception:  # noqa: BLE001 - a UI callback must not kill the loop
            pass

    def _note(self, message: str) -> None:
        """Tell the UI something the user asked to be told about."""
        if self.on_note is None:
            return
        try:
            self.on_note(message)
        except Exception:  # noqa: BLE001 - a UI callback must not kill the loop
            pass

    def _emit(
        self,
        text: str,
        confidence: float,
        now: float,
        display_source: str = "",
        force: bool = False,
    ) -> Event | None:
        """Translate settled text and report it.

        `force` (a re-read the user asked for) also skips the translation cache:
        answering a "do that again" press from the cache would look like the
        button did nothing.
        """
        self._last_settled_text = text
        self._last_display = display_source
        # Record the emit time rather than clearing everything: the settler needs
        # it to avoid re-emitting the same line while it is still on screen.
        self.settler.confirm(now)
        self.settler.reset()
        try:
            translation: Translation = self.translator.translate(text, force=force)
        except Exception as exc:  # noqa: BLE001
            self.stats.errors += 1
            self._on_error(exc)
            return None

        # Only worth remembering when the translation is in a different language.
        # If the backend handed the source straight back, a re-read of that text
        # is already caught by the duplicate check, and keeping it here would risk
        # swallowing a genuinely similar next line. The test is deliberately
        # strict: a real translation that happens to contain the source text
        # ("[ja] ...", a kept character name) must still enable the guard.
        target = translation.target or ""
        self._last_target_text = "" if _echoes_source(target, text) else target
        self.stats.translations += 1
        self.stats.translate_seconds += translation.elapsed
        # Age of the translation: from the moment these pixels were captured to
        # the moment there is something to show. Settling dominates it, which is
        # the honest cost of not translating half-typed lines.
        age = max(0.0, time.monotonic() - self._frame_at) if self._frame_at else 0.0
        event = Event(
            source=text,
            target=translation.target,
            confidence=confidence,
            translate_elapsed=translation.elapsed,
            total_elapsed=age,
            cached=self.translator.last_was_cached,
            backend=self.translator.name,
            capture_elapsed=self._frame_capture_elapsed,
            display_source=display_source or text,
        )
        if self.on_event:
            self.on_event(event)
        return event

    def _on_error(self, exc: Exception) -> None:
        handler = getattr(self, "on_error", None)
        if handler:
            handler(exc)

    @property
    def last_decision(self) -> str:
        """Why the previous poll did or did not act. For `run --verbose`."""
        return getattr(self, "_decision", "")

    def report(self) -> dict:
        return {
            "stats": self.stats.as_dict(),
            "capture": self.grabber.stats,
            "cache": self.translator.cache.stats,
            "detector": {
                "change_rate": round(self.detector.change_rate, 3),
                "frames": self.detector.frames,
                "last_changed_fraction": round(self.detector.last_fraction, 5),
            },
            "paused": self._gate_reason or False,
        }


def _echoes_source(target: str, source: str) -> bool:
    """Whether the backend handed the source back instead of translating it.

    Strict on purpose: this decides whether the panel's own output can be
    recognised later, and a loose test ("mostly similar") would also swallow real
    translations that keep the source's wording - names, "[ja] " prefixes, that
    kind of thing.
    """
    if not target or not source:
        return False
    if target.strip() == source.strip():
        return True
    return similarity(target.strip(), source.strip()) >= 0.97


def _cache_for(config: Config):
    from .translate import TranslationCache

    path = config.cache_path or None
    return TranslationCache(path)


def _glossary_for(config: Config) -> Glossary:
    """User glossary entries layered over the built-in game glossary.

    User entries win, so a config can correct or disable a built-in term.
    """
    user = Glossary.from_config(config.translate.glossary)
    builtin = LIMBUS_GLOSSARY if config.translate.use_builtin_glossary else Glossary()
    return Glossary.merge(builtin, user)
